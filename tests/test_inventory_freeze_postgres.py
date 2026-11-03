"""Persistent freeze regression against disposable PostgreSQL with real WMS guards."""
import asyncio
from pathlib import Path

import asyncpg
import pytest

from app.shared.config import settings
from test_inventory_documents_postgres import (
    BASE,
    cmd,
    create,
    count_item,
    items,
    physical_state,
    inventory_env,  # noqa: F401
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def freeze_env(inventory_env, monkeypatch):
    env = inventory_env
    async with env["pool"].acquire() as conn:
        if not await conn.fetchval("SELECT to_regclass('wms.inventory_freezes') IS NOT NULL"):
            await conn.execute(
                (ROOT / "scripts/migrations/20261030_add_inventory_location_freeze.sql").read_text()
            )
    monkeypatch.setattr(settings, "INVENTORY_FREEZE_ENABLED", True)
    return env


async def begin(env, document=None):
    document = document or await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/freeze", json=cmd(document)
    )
    assert response.status_code == 200, response.text
    return response.json()["document"], response.json()["freeze"]


async def confirm(env, document):
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/freeze/confirm",
        json=cmd(
            document,
            unrecorded_operations_resolved=True,
            confirmation="Проверено: физические операции завершены и отражены, неизвестных действий в адресах не обнаружено.",
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()["document"], response.json()["freeze"]


@pytest.mark.parametrize(
    "kind,source,destination",
    [
        ("transfer", "zone", "empty"),
        ("transfer", "bin", "zone"),
        ("receive", None, "zone"),
        ("adjust", "zone", None),
        ("adjust", None, "zone"),
        ("kit_assembly", "zone", None),
        ("kit_disassembly", None, "zone"),
        ("re_sorting", "zone", None),
        ("ship", "zone", None),
    ],
)
async def test_ordinary_movement_cannot_cross_freeze(freeze_env, kind, source, destination):
    env = freeze_env
    await begin(env)
    before = await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity) VALUES($1,'inv-a',$2,$3,1)",
                kind,
                env[source] if source else None,
                env[destination] if destination else None,
            )
        assert caught.value.sqlstate == "P7601"
    assert before == await physical_state(env["pool"])


async def test_partition_guard_rejects_missing_row_adjust_before_projection(freeze_env):
    env = freeze_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["empty"], include_descendants=False)]
    )
    await begin(env, doc)
    before = await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('adjust','inv-new',$1,1)",
                env["empty"],
            )
        assert caught.value.sqlstate == "P7601"
    assert before == await physical_state(env["pool"])


async def test_http_transfer_conflict_and_unrelated_address_works(freeze_env):
    env = freeze_env
    await begin(env)
    async with env["pool"].acquire() as conn:
        codes = {
            key: await conn.fetchval(
                "SELECT location_code FROM wms.locations WHERE location_id=$1", env[key]
            )
            for key in ("zone", "bin", "empty")
        }
    response = await env["client"].post(
        "/api/movements",
        json=[
            dict(
                movement_type="transfer",
                product_id="inv-a",
                from_location_code=codes["zone"],
                to_location_code=codes["empty"],
                quantity=1,
                user_name="test",
            )
        ],
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "INVENTORY_LOCATION_FROZEN"
    response = await env["client"].post(
        "/api/movements",
        json=[
            dict(
                movement_type="transfer",
                product_id="inv-a",
                from_location_code=codes["bin"],
                to_location_code=codes["empty"],
                quantity=1,
                user_name="test",
            )
        ],
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE wms.inventory SET quantity=quantity WHERE location_id=$1",
        "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('FROZEN-KIZ','inv-a',$1,'active','test')",
        "INSERT INTO wms.containers(qr_code,container_type,location_id) VALUES('FROZEN-C','box',$1)",
        "UPDATE wms.locations SET is_active=false WHERE location_id=$1",
    ],
)
async def test_direct_projection_lifecycle_container_and_location_guard(freeze_env, statement):
    env = freeze_env
    await begin(env)
    async with env["pool"].acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.execute(statement, env["zone"])
        assert caught.value.sqlstate == "P7601"


async def test_confirmation_cancels_old_progress_and_requires_new_final_count(freeze_env):
    env = freeze_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    item = (await items(env, doc))[0]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/items/{item['item_id']}/attempts", json=cmd(doc)
    )
    doc = response.json()["document"]
    old = response.json()["attempt"]
    doc, freeze = await begin(env, doc)
    doc, freeze = await confirm(env, doc)
    doc = await count_item(env, doc, item, 8)
    response = await env["client"].get(
        f"{BASE}/{doc['document_id']}/items/{item['item_id']}/attempts"
    )
    attempts = response.json()["items"]
    assert attempts[0]["status"] == "cancelled" and attempts[0]["attempt_id"] == old["attempt_id"]
    assert attempts[1]["evidence"]["freeze"] == dict(
        freeze_id=freeze["freeze_id"], epoch=freeze["epoch"]
    )
    assert attempts[1]["status"] == "completed"


async def test_release_is_explicit_audited_and_does_not_release_claims(freeze_env):
    env = freeze_env
    doc, freeze = await begin(env)
    before = await physical_state(env["pool"])
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/close-without-adjustment", json=cmd(doc, reason="close")
    )
    assert response.status_code == 409
    request = cmd(doc, reason="Снято без корректировок")
    url = f"{BASE}/{doc['document_id']}/freeze/release"
    response = await env["client"].post(url, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["freeze"]["state"] == "released"
    assert (await env["client"].post(url, json=request)).json() == response.json()
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["events"]["items"][0]["action"] == "released_without_adjustment"
    assert before == await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_location_claims WHERE document_id=$1",
                doc["document_id"],
            )
            == 1
        )
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
            env["zone"],
        )


async def fbs_http(env, monkeypatch, location_key="zone", task=9001):
    async with env["pool"].acquire() as conn:
        code = await conn.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env[location_key]
        )
        await conn.execute(
            "INSERT INTO public.assembly_task(task_id,vendor_code,date) VALUES($1,'inv-a',CURRENT_DATE)",
            task,
        )
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", code)
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    return await env["client"].post(
        "/api/fbs-shipments",
        json=[
            dict(
                product_id="inv-a",
                quantity=1,
                assembly_tasks=[str(task)],
                author="test",
                supply_id="s",
                warehouse_id=1,
                delivery_type="fbs",
                account="test",
            )
        ],
    )


async def test_real_fbs_continues_and_atomically_invalidates(freeze_env, monkeypatch):
    env = freeze_env
    doc, _ = await begin(env)
    doc, _ = await confirm(env, doc)
    response = await fbs_http(env, monkeypatch)
    assert response.status_code == 201, response.text
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["freeze"]["state"] == "invalidated"
    assert any(e["action"] == "fbs_invalidated" for e in status["events"]["items"])
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_items WHERE status='success'"
            )
            == 1
        )
        assert (
            await conn.fetchval("SELECT source_type FROM wms.movements WHERE movement_type='ship'")
            is None
        )
    # No stale result can be completed after invalidation.
    item = (await items(env, doc))[0]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/items/{item['item_id']}/attempts", json=cmd(doc)
    )
    assert response.status_code == 409
    doc, newfreeze = await confirm(env, doc)
    assert newfreeze["epoch"] == 2


async def test_fake_fbs_context_without_success_link_rolls_back(freeze_env):
    env = freeze_env
    doc, _ = await begin(env)
    before = await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipments(raw_message,total_items,status,source) VALUES('{}',1,'processing','standard') RETURNING shipment_id"
        )
        iid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,assembly_tasks,supply_id,account,warehouse_id,delivery_type) VALUES($1,'inv-a',1,'test','[]','s','test',1,'fbs') RETURNING item_id",
            sid,
        )
        with pytest.raises(asyncpg.PostgresError) as caught:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('wms.inventory_fbs_shipment_id',$1,true),set_config('wms.inventory_fbs_items',$2,true)",
                    str(sid),
                    f"[{iid}]",
                )
                await conn.execute(
                    "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship','inv-a',$1,1)",
                    env["zone"],
                )
        assert caught.value.sqlstate == "P7602"
    assert before == await physical_state(env["pool"])
    assert (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()["freeze"][
        "state"
    ] == "freezing"


async def test_freeze_cannot_overtake_uncommitted_writer_and_rr_writer_cannot_miss_freeze(
    freeze_env,
):
    env = freeze_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    async with env["pool"].acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
                env["zone"],
            )
            response = await env["client"].post(
                f"{BASE}/{doc['document_id']}/freeze", json=cmd(doc)
            )
            assert response.status_code == 409, response.text
    async with env["pool"].acquire() as conn:
        transaction = conn.transaction(isolation="repeatable_read")
        await transaction.start()
        await conn.fetch("SELECT * FROM wms.inventory_location_fences")
        try:
            doc, _ = await begin(env, doc)
            with pytest.raises(asyncpg.SerializationError):
                await conn.execute(
                    "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
                    env["zone"],
                )
        finally:
            await transaction.rollback()


async def test_maintenance_is_atomic_and_cannot_overwrite_frozen_stock(freeze_env):
    from app.infrastructure.database.repositories.system_repository import SystemRepository

    env = freeze_env
    await begin(env)
    before = await physical_state(env["pool"])
    with pytest.raises(asyncpg.PostgresError) as caught:
        await SystemRepository(env["pool"]).recalculate_inventory(product_id="inv-a")
    assert caught.value.sqlstate == "P7601"
    assert before == await physical_state(env["pool"])


async def test_controlled_fill_cannot_bypass_freeze(freeze_env):
    env = freeze_env
    async with env["pool"].acquire() as conn:
        code = await conn.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
    response = await env["client"].post(
        "/api/containers/register",
        json=dict(qr_code="FREEZE-C", container_type="box", location_code=code, contents=[]),
    )
    assert response.status_code == 201, response.text
    cid = response.json()["container_id"]
    await begin(env)
    before = await physical_state(env["pool"])
    response = await env["client"].post(
        "/api/container-operations/fill",
        json=dict(
            source_system="manual",
            external_operation_id="freeze-fill",
            author="test",
            container_id=cid,
            items=[dict(external_line_id="1", product_id="inv-a", quantity="1")],
        ),
    )
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])


@pytest.mark.parametrize("kind", ["assembly", "disassembly", "re_sorting"])
async def test_real_kit_and_resort_api_cannot_bypass_freeze(freeze_env, kind):
    env = freeze_env
    async with env["pool"].acquire() as conn:
        code = await conn.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
        operation = "re_sorting_operations" if kind == "re_sorting" else "kit_operations"
        await conn.execute(
            "INSERT INTO wms.operation_locations(operation_code,location_id,location_code,scope,author) VALUES($1,$2,$3,'direct','test')",
            operation,
            env["zone"],
            code,
        )
        kit = "inv-a" if kind == "disassembly" else "inv-new"
        component = "inv-new" if kind == "disassembly" else "inv-a"
        import json

        await conn.execute(
            "UPDATE public.products SET is_kit=true,kit_components=$2::jsonb WHERE id=$1",
            kit,
            json.dumps({component: 1}),
        )
    await begin(env)
    before = await physical_state(env["pool"])
    if kind == "re_sorting":
        path = "/api/re-sorting-operations"
        data = dict(from_product_id="inv-a", to_product_id="inv-new", reason="test")
    else:
        path = "/api/kit-operations"
        data = dict(operation_type=kind, kit_product_id=kit)
    response = await env["client"].post(
        path, json=dict(**data, quantity=1, author="test", location_code=code)
    )
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM wms.{operation}") == 0


async def test_task_admission_and_unresolved_pick_are_visible_and_prevent_confirmation(freeze_env):
    env = freeze_env
    async with env["pool"].acquire() as conn:
        user = await conn.fetchval(
            "INSERT INTO public.users(username,email,hashed_password) VALUES('freeze-test','freeze@example.invalid','test') RETURNING id"
        )
        tid = await conn.fetchval(
            "INSERT INTO wms.tasks(task_type,created_by,from_location_id,to_location_id) VALUES('picking',$1,$2,$3) RETURNING task_id",
            user,
            env["zone"],
            env["empty"],
        )
    doc, _ = await begin(env)
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["conflicting_operations"][0]["task_id"] == tid
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/freeze/confirm",
        json=cmd(
            doc,
            confirmation="Подтверждаю отсутствие незавершённых физических действий в выбранных адресах.",
            unrecorded_operations_resolved=True,
        ),
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "INVENTORY_FREEZE_OPERATIONS_PENDING"
    async with env["pool"].acquire() as conn:
        for statement, args in [
            (
                "INSERT INTO wms.tasks(task_type,created_by,from_location_id) VALUES('putaway',$1,$2)",
                (user, env["zone"]),
            ),
            (
                "INSERT INTO wms.tasks(task_type,created_by,parent_task_id) VALUES('recount',$1,$2)",
                (user, tid),
            ),
            ("UPDATE wms.tasks SET status='assigned' WHERE task_id=$1", (tid,)),
            (
                "INSERT INTO wms.task_items(task_id,product_id,quantity_planned) VALUES($1,'inv-a',1)",
                (tid,),
            ),
        ]:
            with pytest.raises(asyncpg.PostgresError) as caught:
                await conn.execute(statement, *args)
            assert caught.value.sqlstate == "P7601"
        await conn.execute("UPDATE wms.tasks SET status='cancelled' WHERE task_id=$1", tid)
    doc, _ = await confirm(env, doc)
    assert (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()[
        "conflicting_operations"
    ] == []


@pytest.mark.parametrize(
    "change",
    [
        "location_id=$2",
        "lifecycle_status='deactivated',location_id=NULL",
        "lifecycle_status='shipped',location_id=NULL",
    ],
)
async def test_existing_kiz_transfer_deactivation_ship_cannot_bypass_freeze(freeze_env, change):
    env = freeze_env
    async with env["pool"].acquire() as conn:
        async with conn.transaction():
            await conn.fetch(
                "SELECT inventory_id FROM wms.inventory WHERE location_id=$1 FOR UPDATE",
                env["zone"],
            )
            kid = await conn.fetchval(
                "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('FREEZE-EXISTING','inv-a',$1,'active','test') RETURNING kiz_id",
                env["zone"],
            )
    await begin(env)
    async with env["pool"].acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as caught:
            args = (kid, env["empty"]) if "$2" in change else (kid,)
            await conn.execute("UPDATE wms.kiz SET " + change + " WHERE kiz_id=$1", *args)
        assert caught.value.sqlstate == "P7601"


async def test_two_fbs_writers_racing_free_to_frozen_do_not_upgrade_deadlock(freeze_env):
    from app.infrastructure.database.repositories.fbs_shipment_repository import (
        FbsShipmentRepository,
    )

    env = freeze_env
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,2)",
            env["zone"],
        )
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    async with env["pool"].acquire() as gate:
        await gate.execute(
            """CREATE FUNCTION public.pause_freeze_fence() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN IF OLD.freeze_id IS NULL AND NEW.freeze_id IS NOT NULL THEN
          PERFORM pg_advisory_xact_lock(760312); END IF; RETURN NEW; END $$;
         CREATE TRIGGER zz_pause_freeze_fence BEFORE UPDATE ON wms.inventory_location_fences
          FOR EACH ROW EXECUTE FUNCTION public.pause_freeze_fence();"""
        )
        await gate.execute("SELECT pg_advisory_lock(760312)")
        freezing = asyncio.create_task(
            env["client"].post(f"{BASE}/{doc['document_id']}/freeze", json=cmd(doc))
        )
        workers = []
        try:
            for _ in range(100):
                if await gate.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=760312 AND NOT granted)"
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Freeze did not reach gate")

            async def write(pid):
                async with env["pool"].acquire() as conn:
                    async with conn.transaction():
                        sid = await conn.fetchval(
                            "INSERT INTO wms.fbs_shipments(raw_message,total_items,status,source) VALUES('{}',1,'processing','standard') RETURNING shipment_id"
                        )
                        iid = await conn.fetchval(
                            "INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,assembly_tasks,supply_id,account,warehouse_id,delivery_type) VALUES($1,$2,1,'test','[]','s','test',1,'fbs') RETURNING item_id",
                            sid,
                            pid,
                        )
                        repo = FbsShipmentRepository()
                        await repo.lock_items_for_processing(conn, item_ids=[iid])
                        await conn.execute(
                            "SELECT wms.inventory_freeze_fbs_lock_locations($1::bigint[])",
                            [env["zone"]],
                        )
                        mid = await conn.fetchval(
                            "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship',$1,$2,1) RETURNING movement_id",
                            pid,
                            env["zone"],
                        )
                        await repo.mark_items_success_in_transaction(
                            conn, item_ids=[iid], movement_id=mid
                        )

            workers = [asyncio.create_task(write(pid)) for pid in ("inv-a", "inv-b")]
            for _ in range(100):
                blocked = await gate.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'SELECT wms.inventory_freeze_fbs_lock_locations%'"
                )
                if blocked == 2:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Both FBS writers did not wait on the freeze gate")
        finally:
            await gate.execute("SELECT pg_advisory_unlock(760312)")
            response = await asyncio.wait_for(freezing, 5)
            await asyncio.wait_for(asyncio.gather(*workers), 5)
        assert response.status_code == 200, response.text
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["freeze"]["state"] == "invalidated"
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_items WHERE status='success'"
            )
            == 2
        )


async def test_new_child_after_document_start_does_not_expand_or_break_fixed_freeze(freeze_env):
    env = freeze_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=True)]
    )
    async with env["pool"].acquire() as conn:
        child = await conn.fetchval(
            "INSERT INTO wms.locations(name,parent_location_id,level) VALUES('02',$1,2) RETURNING location_id",
            env["zone"],
        )
    doc, _ = await begin(env, doc)
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT freeze_id FROM wms.inventory_location_fences WHERE location_id=$1", child
            )
            is None
        )
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
            child,
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_location_fences WHERE freeze_id IS NOT NULL"
            )
            == 2
        )


async def test_ancestor_hierarchy_writer_cannot_race_or_change_frozen_descendant(freeze_env):
    env = freeze_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["bin"], include_descendants=False)]
    )
    async with env["pool"].acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "UPDATE wms.locations SET parent_location_id=$1 WHERE location_id=$2",
                env["empty"],
                env["zone"],
            )
            response = await env["client"].post(
                f"{BASE}/{doc['document_id']}/freeze", json=cmd(doc)
            )
            assert response.status_code == 409, response.text
        finally:
            await tx.rollback()
    doc, _ = await begin(env, doc)
    async with env["pool"].acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.execute(
                "UPDATE wms.locations SET parent_location_id=$1 WHERE location_id=$2",
                env["empty"],
                env["zone"],
            )
        assert caught.value.sqlstate == "P7601"


async def test_unfrozen_fbs_skus_remain_concurrent_at_the_same_address(freeze_env):
    from app.infrastructure.database.repositories.fbs_shipment_repository import (
        FbsShipmentRepository,
    )

    env = freeze_env
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,2)",
            env["zone"],
        )

    async def write(conn, pid):
        sid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipments(raw_message,total_items,status,source) VALUES('{}',1,'processing','standard') RETURNING shipment_id"
        )
        iid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,assembly_tasks,supply_id,account,warehouse_id,delivery_type) VALUES($1,$2,1,'test','[]','s','test',1,'fbs') RETURNING item_id",
            sid,
            pid,
        )
        repo = FbsShipmentRepository()
        await repo.lock_items_for_processing(conn, item_ids=[iid])
        await conn.execute(
            "SELECT wms.inventory_freeze_fbs_lock_locations($1::bigint[])", [env["zone"]]
        )
        mid = await conn.fetchval(
            "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship',$1,$2,1) RETURNING movement_id",
            pid,
            env["zone"],
        )
        await repo.mark_items_success_in_transaction(conn, item_ids=[iid], movement_id=mid)

    async with env["pool"].acquire() as first:
        async with first.transaction():
            await write(first, "inv-a")
            async with env["pool"].acquire() as second:
                async with second.transaction():
                    await asyncio.wait_for(write(second, "inv-b"), 0.8)
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_items WHERE status='success'"
            )
            == 2
        )


async def test_disjoint_fbs_sources_lock_shared_freeze_headers_in_canonical_order(freeze_env):
    """X/Y and Y/X source order must not create a new FBS/FBS header deadlock."""
    from app.infrastructure.database.repositories.fbs_shipment_repository import (
        FbsShipmentRepository,
    )

    env = freeze_env
    async with env["pool"].acquire() as conn:
        extra = await conn.fetchval(
            "INSERT INTO wms.locations(name,parent_location_id,level) VALUES('Cross-FBS',$1,1) RETURNING location_id",
            env["root"],
        )
        for loc in (env["empty"], extra):
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,2)",
                loc,
            )
    for locs in ((env["zone"], env["empty"]), (env["bin"], extra)):
        doc = await create(
            env,
            mode="partial",
            locations=[dict(location_id=loc, include_descendants=False) for loc in locs],
        )
        doc, _ = await begin(env, doc)
        await confirm(env, doc)

    async def worker(locs):
        async with env["pool"].acquire() as conn:
            async with conn.transaction():
                sid = await conn.fetchval(
                    "INSERT INTO wms.fbs_shipments(raw_message,total_items,status,source) VALUES('{}',2,'processing','standard') RETURNING shipment_id"
                )
                ids = []
                for _ in locs:
                    ids.append(
                        await conn.fetchval(
                            "INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,assembly_tasks,supply_id,account,warehouse_id,delivery_type) VALUES($1,'inv-a',1,'test','[]','s','test',1,'fbs') RETURNING item_id",
                            sid,
                        )
                    )
                repo = FbsShipmentRepository()
                await repo.lock_items_for_processing(conn, item_ids=ids)
                await conn.execute(
                    "SELECT wms.inventory_freeze_fbs_lock_locations($1::bigint[])", list(locs)
                )
                for loc, iid in zip(locs, ids):
                    mid = await conn.fetchval(
                        "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship','inv-a',$1,1) RETURNING movement_id",
                        loc,
                    )
                    await repo.mark_items_success_in_transaction(
                        conn, item_ids=[iid], movement_id=mid
                    )

    await asyncio.wait_for(
        asyncio.gather(worker((env["zone"], extra)), worker((env["bin"], env["empty"]))), 5
    )
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_freezes WHERE state='invalidated'"
            )
            == 2
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.movements WHERE movement_type='ship'")
            == 4
        )


async def test_real_multi_source_kiz_fbs_retry_and_duplicate_keep_atomic_invalidation(
    freeze_env, monkeypatch
):
    from app.retry_worker import process_pending_retries

    env = freeze_env
    monkeypatch.setattr(settings, "FBS_TASK_PROCESSING_MODE", "task_level")
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    async with env["pool"].acquire() as conn:
        code = await conn.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["empty"]
        )
        await conn.execute(
            "INSERT INTO public.assembly_task(task_id,vendor_code,date) VALUES(9001,'inv-a',CURRENT_DATE),(9002,'inv-a',CURRENT_DATE)"
        )
        async with conn.transaction():
            await conn.fetch(
                "SELECT * FROM wms.inventory WHERE location_id=$1 FOR UPDATE", env["zone"]
            )
            await conn.execute(
                "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('FBS-FROZEN-ACTIVE','inv-a',$1,'active','test')",
                env["zone"],
            )
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", code)
    doc, _ = await begin(env)
    doc, _ = await confirm(env, doc)
    payload = [
        dict(
            product_id="inv-a",
            quantity=2,
            assembly_tasks=["9001", "9002"],
            kiz_bindings=[dict(assembly_task="9001", kiz_code="FBS-FROZEN-ACTIVE")],
            author="test",
            supply_id="s",
            warehouse_id=1,
            delivery_type="fbs",
            account="test",
        )
    ]
    response = await env["client"].post("/api/fbs-shipments", json=payload)
    assert response.status_code == 201, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT state FROM wms.inventory_freezes WHERE document_id=$1", doc["document_id"]
            )
            == "frozen"
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.movements WHERE movement_type='ship'")
            == 0
        )
        assert await conn.fetchval("SELECT status FROM wms.fbs_shipment_items") == "pending_retry"
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,2)",
            env["empty"],
        )
        await conn.execute(
            "UPDATE wms.fbs_shipment_items SET next_retry_at=clock_timestamp()-interval '1 minute'"
        )
    await process_pending_retries(env["pool"])
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval("SELECT status FROM wms.fbs_shipment_items") == "success"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_task_results WHERE outcome='written_off'"
            )
            == 2
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.movements WHERE movement_type='ship'")
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["empty"],
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT state FROM wms.inventory_freezes WHERE document_id=$1", doc["document_id"]
            )
            == "invalidated"
        )
    response = await env["client"].post("/api/fbs-shipments", json=payload)
    assert response.status_code == 201, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.movements WHERE movement_type='ship'")
            == 2
        )
