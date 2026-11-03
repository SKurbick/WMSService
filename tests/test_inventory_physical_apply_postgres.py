"""Physical effects of the controlled inventory pilot; disposable PostgreSQL ONLY."""
import asyncio
from decimal import Decimal

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
)  # noqa: F401
from test_inventory_freeze_postgres import ROOT, freeze_env, begin, confirm  # noqa: F401


@pytest.fixture
async def physical_env(freeze_env, monkeypatch):  # noqa: F811
    env = freeze_env
    async with env["pool"].acquire() as conn:
        if not await conn.fetchval("SELECT to_regclass('wms.inventory_applications') IS NOT NULL"):
            before = await conn.fetchval(
                "SELECT prosrc FROM pg_proc WHERE oid='wms.update_inventory_from_movement()'::regprocedure"
            )
            await conn.execute(
                (ROOT / "scripts/migrations/20261030_add_inventory_physical_apply.sql").read_text()
            )
            after = await conn.fetchval(
                "SELECT prosrc FROM pg_proc WHERE oid='wms.update_inventory_from_movement()'::regprocedure"
            )
            prefix = "\n    IF NEW.source_type = 'inventory_document' THEN\n        PERFORM wms.apply_strict_inventory_source_adjustment(NEW);\n        RETURN NEW;\n    END IF;\n"
            assert after == "BEGIN" + prefix + before + "\nEND;\n"
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", True)
    return env


async def latest(env, doc):
    return (await env["client"].get(f"{BASE}/{doc['document_id']}")).json()


def approval(doc):
    return cmd(doc, submitted_revision=doc["submitted_revision"])


async def submit_preview(env, doc):
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    assert response.status_code == 200, response.text
    doc = response.json()["document"]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/application-preview", json=approval(doc)
    )
    assert response.status_code == 200, response.text
    return await latest(env, doc), response.json()


async def ready_document(env, quantity="8", doc=None):
    doc, _ = await begin(env, doc)
    doc, _ = await confirm(env, doc)
    for item in await items(env, doc):
        actual = quantity[item["product_id"]] if isinstance(quantity, dict) else quantity
        doc = await count_item(env, doc, item, actual)
    doc, preparation = await submit_preview(env, doc)
    assert preparation["can_apply"], preparation
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/freeze/ready", json=approval(doc)
    )
    assert response.status_code == 200, response.text
    return response.json()["document"], preparation


@pytest.mark.parametrize("quantity,movements", [("8", 1), ("12.25", 1), ("10", 0), ("0", 1)])
async def test_successful_shortage_surplus_zero_and_no_delta(physical_env, quantity, movements):
    env = physical_env
    doc, prep = await ready_document(env, quantity)
    request = approval(doc)
    url = f"{BASE}/{doc['document_id']}/apply"
    response = await env["client"].post(url, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["applied"] and result["document"]["status"] == "applied"
    assert len(result["movements"]) == movements
    assert (await env["client"].post(url, json=request)).json() == result
    async with env["pool"].acquire() as conn:
        actual = await conn.fetchval(
            "SELECT coalesce(sum(quantity),0) FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
            env["zone"],
        )
        assert actual == Decimal(quantity)
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory_applications") == 1
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.inventory_application_movements")
            == movements
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_location_claims WHERE document_id=$1",
                doc["document_id"],
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_location_fences WHERE freeze_id=$1",
                prep["freeze_id"],
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT state FROM wms.inventory_freezes WHERE freeze_id=$1", prep["freeze_id"]
            )
            == "released"
        )
        for movement in result["movements"]:
            assert movement["source_type"] == "inventory_document"
            assert movement["source_id"] == doc["document_id"]
            assert movement["source_item_id"] == prep["components"][0]["component_id"]
            assert (
                await conn.fetchval(
                    "SELECT movement_ref FROM wms.movement_registry WHERE movement_id=$1 AND movement_created_at=$2",
                    movement["movement_id"],
                    await conn.fetchval(
                        "SELECT created_at FROM wms.movements WHERE movement_id=$1",
                        movement["movement_id"],
                    ),
                )
                == movement["movement_ref"]
            )
    result_get = (await env["client"].get(f"{BASE}/{doc['document_id']}/application")).json()
    assert result_get == result


async def test_found_item_surplus_creates_missing_row(physical_env):
    env = physical_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["empty"], include_descendants=False)]
    )
    doc, _ = await begin(env, doc)
    doc, _ = await confirm(env, doc)
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/items",
        json=cmd(doc, product_id="inv-new", location_id=env["empty"]),
    )
    assert response.status_code == 201, response.text
    doc = await count_item(env, response.json()["document"], response.json()["item"], "2.25")
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"] and prep["components"][0]["stock_evidence"]["inventory_id"] is None
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/freeze/ready", json=approval(doc)
    )
    doc = response.json()["document"]
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200, response.text
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval(
            "SELECT quantity FROM wms.inventory WHERE product_id='inv-new' AND location_id=$1",
            env["empty"],
        ) == Decimal("2.25")


async def test_two_rows_apply_atomically_and_different_key_is_safe(physical_env):
    env = physical_env
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,3)",
            env["zone"],
        )
    doc, _ = await ready_document(env, {"inv-a": "8", "inv-b": "4"})
    responses = await asyncio.gather(
        *(
            env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    doc = await latest(env, doc)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200 and len(response.json()["movements"]) == 2
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 8
        )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-b' AND location_id=$1",
                env["zone"],
            )
            == 4
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='inventory_document'"
            )
            == 2
        )


@pytest.mark.parametrize(
    "fault",
    ["last_movement", "deferred_commit", "missing_row", "insufficient_row", "after_projection"],
)
async def test_fault_rolls_back_whole_application_and_same_key_can_retry(physical_env, fault):
    env = physical_env
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,3)",
            env["zone"],
        )
    doc, _ = await ready_document(env, {"inv-a": "8", "inv-b": "4"})
    before = await physical_state(env["pool"])
    async with env["pool"].acquire() as conn:
        if fault == "deferred_commit":
            await conn.execute(
                """CREATE FUNCTION public.fail_inventory_test() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='test deferred commit failure'; END $$;
             CREATE CONSTRAINT TRIGGER test_inventory_fault AFTER INSERT ON wms.inventory_applications
             DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.fail_inventory_test();"""
            )
            relation = "inventory_applications"
        elif fault == "after_projection":
            await conn.execute(
                """CREATE FUNCTION public.fail_inventory_test() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN IF pg_trigger_depth()=2 AND NEW.product_id='inv-a'
               AND NULLIF(current_setting('wms.inventory_application_id',true),'') IS NOT NULL THEN
               UPDATE wms.inventory SET quantity=quantity+1 WHERE inventory_id=NEW.inventory_id;
             END IF; RETURN NEW; END $$;
             CREATE TRIGGER test_inventory_fault AFTER UPDATE ON wms.inventory
             FOR EACH ROW EXECUTE FUNCTION public.fail_inventory_test();"""
            )
            relation = "inventory"
        else:
            body = {
                "last_movement": "IF NEW.source_type='inventory_document' AND NEW.product_id='inv-b' THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='last movement fault'; END IF;",
                "missing_row": "IF NEW.source_type='inventory_document' AND NEW.from_location_id IS NOT NULL THEN DELETE FROM wms.inventory WHERE product_id=NEW.product_id AND location_id=NEW.from_location_id; END IF;",
                "insufficient_row": "IF NEW.source_type='inventory_document' AND NEW.from_location_id IS NOT NULL THEN UPDATE wms.inventory SET quantity=1 WHERE product_id=NEW.product_id AND location_id=NEW.from_location_id; END IF;",
            }[fault]
            await conn.execute(
                "CREATE FUNCTION public.fail_inventory_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                + body
                + " RETURN NEW; END $$; CREATE TRIGGER test_inventory_fault BEFORE INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION public.fail_inventory_test();"
            )
            relation = "movements"
    request = approval(doc)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])
    assert doc == await latest(env, doc)
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory_applications") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory_application_movements") == 0
        assert (
            await conn.fetchval(
                "SELECT state FROM wms.inventory_freezes WHERE document_id=$1", doc["document_id"]
            )
            == "ready_to_apply"
        )
        await conn.execute(f"DROP TRIGGER test_inventory_fault ON wms.{relation}")
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "context", ["null_empty_batch", "multi_batch", "active_kiz", "empty_container", "fbs_location"]
)
async def test_unsupported_physical_context_rejected_without_changes(
    physical_env, monkeypatch, context
):
    env = physical_env
    async with env["pool"].acquire() as conn:
        if context in ("null_empty_batch", "multi_batch"):
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,batch_number) VALUES('receive','inv-a',$1,3,$2)",
                env["zone"],
                "" if context == "null_empty_batch" else "B",
            )
        elif context == "active_kiz":
            async with conn.transaction():
                await conn.fetch(
                    "SELECT * FROM wms.inventory WHERE location_id=$1 FOR UPDATE", env["zone"]
                )
                await conn.execute(
                    "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('APPLY-KIZ','inv-a',$1,'active','test')",
                    env["zone"],
                )
        elif context == "empty_container":
            await conn.execute(
                "INSERT INTO wms.inventory(product_id,location_id,status,quantity,container_code) VALUES('inv-a',$1,'available',1,'')",
                env["zone"],
            )
        else:
            code = await conn.fetchval(
                "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
            )
            monkeypatch.setattr(settings, "FBS_LOCATION_CODE", code)
    doc, _ = await begin(env)
    doc, _ = await confirm(env, doc)
    for item in await items(env, doc):
        doc = await count_item(env, doc, item, "8")
    before = await physical_state(env["pool"])
    doc, prep = await submit_preview(env, doc)
    assert not prep["can_apply"] and prep["blockers"]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/freeze/ready", json=approval(doc)
    )
    assert response.status_code == 409
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 409
    assert before == await physical_state(env["pool"])


async def test_legacy_adjust_semantics_and_unproven_inventory_source(physical_env):
    env = physical_env
    async with env["pool"].acquire() as conn:
        for outgoing, incoming, quantity in [(None, env["empty"], 3), (env["empty"], None, 1)]:
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity) VALUES('adjust','inv-a',$1,$2,$3)",
                outgoing,
                incoming,
                quantity,
            )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["empty"],
            )
            == 2
        )
        before = await physical_state(env["pool"])
        with pytest.raises(asyncpg.PostgresError) as caught:
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity,source_type,source_id,source_item_id) VALUES('adjust','inv-new',$1,1,'inventory_document',99999,99999)",
                env["empty"],
            )
        assert caught.value.sqlstate == "P7603"
        assert before == await physical_state(env["pool"])


async def test_pre_freeze_receipt_is_in_frozen_basis_not_an_excess(physical_env):
    env = physical_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    doc = await count_item(env, doc, (await items(env, doc))[0], 8)
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,3)",
            env["zone"],
        )
    doc, prep = await ready_document(env, "11", doc)
    assert prep["lines"][0]["adjustment_quantity"] == "-2.00"
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 11
        )


async def make_fbs_item(env):
    async with env["pool"].acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipments(raw_message,total_items,status,source) VALUES('{}',1,'processing','standard') RETURNING shipment_id"
        )
        iid = await conn.fetchval(
            "INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,assembly_tasks,supply_id,account,warehouse_id,delivery_type) VALUES($1,'inv-a',1,'test','[]','s','test',1,'fbs') RETURNING item_id",
            sid,
        )
    return iid


async def fbs_effect(conn, env, item_id):
    from app.infrastructure.database.repositories.fbs_shipment_repository import (
        FbsShipmentRepository,
    )

    repository = FbsShipmentRepository()
    await repository.lock_items_for_processing(conn, item_ids=[item_id])
    mid = await conn.fetchval(
        "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship','inv-a',$1,1) RETURNING movement_id",
        env["zone"],
    )
    await repository.mark_items_success_in_transaction(conn, item_ids=[item_id], movement_id=mid)


@pytest.mark.parametrize("depth", [1, 2])
async def test_fbs_invalidation_proof_accepts_nested_product_group_savepoints(physical_env, depth):
    from contextlib import AsyncExitStack

    env = physical_env
    doc, _ = await ready_document(env)
    iid = await make_fbs_item(env)
    async with env["pool"].acquire() as conn:
        async with AsyncExitStack() as stack:
            for _ in range(depth + 1):
                await stack.enter_async_context(conn.transaction())
            await fbs_effect(conn, env, iid)
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
        assert (
            await conn.fetchval(
                "SELECT state FROM wms.inventory_freezes WHERE document_id=$1", doc["document_id"]
            )
            == "invalidated"
        )


async def test_uncommitted_fbs_cannot_be_missed_by_approval_and_requires_recount(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    iid = await make_fbs_item(env)
    async with env["pool"].acquire() as conn:
        async with conn.transaction():
            await fbs_effect(conn, env, iid)
            response = await env["client"].post(
                f"{BASE}/{doc['document_id']}/apply", json=approval(doc)
            )
            assert response.status_code == 409, response.text
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 409, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='inventory_document'"
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/return-to-count", json=cmd(doc, reason="FBS invalidation")
    )
    doc = response.json()
    doc, _ = await confirm(env, doc)
    doc = await count_item(env, doc, (await items(env, doc))[0], 7)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"], prep
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/freeze/ready", json=approval(doc)
    )
    doc = response.json()["document"]
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 7
        )


async def test_fbs_serializes_after_inflight_apply_without_losing_its_effect(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    iid = await make_fbs_item(env)
    gate_key = 760311
    async with env["pool"].acquire() as gate:
        await gate.execute(
            """CREATE FUNCTION public.pause_inventory_apply() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN IF NEW.source_type='inventory_document' THEN PERFORM pg_advisory_xact_lock(760311); END IF; RETURN NEW; END $$;
         CREATE TRIGGER zz_pause_inventory_apply BEFORE INSERT ON wms.movements
         FOR EACH ROW EXECUTE FUNCTION public.pause_inventory_apply();"""
        )
        await gate.execute("SELECT pg_advisory_lock($1::bigint)", gate_key)
        apply_task = asyncio.create_task(
            env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
        )
        fbs_task = None
        try:
            for _ in range(100):
                if await gate.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=$1::oid AND NOT granted)",
                    gate_key,
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Apply did not reach test gate")

            async def write_fbs():
                async with env["pool"].acquire() as conn:
                    async with conn.transaction():
                        await fbs_effect(conn, env, iid)

            fbs_task = asyncio.create_task(write_fbs())
            await asyncio.sleep(0.05)
            assert not fbs_task.done()
        finally:
            await gate.execute("SELECT pg_advisory_unlock($1::bigint)", gate_key)
            response = await asyncio.wait_for(apply_task, 5)
            if fbs_task:
                await asyncio.wait_for(fbs_task, 5)
        assert response.status_code == 200, response.text
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 7
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_items WHERE status='success'"
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='inventory_document'"
            )
            == 1
        )


async def test_approval_never_waits_for_legacy_location_or_physical_row_locks(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    before = await physical_state(env["pool"])
    for query in (
        "SELECT location_id FROM wms.locations WHERE location_id=$1 FOR UPDATE",
        "SELECT inventory_id FROM wms.inventory WHERE location_id=$1 FOR UPDATE",
    ):
        async with env["pool"].acquire() as conn:
            async with conn.transaction():
                await conn.fetch(query, env["zone"])
                response = await asyncio.wait_for(
                    env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc)), 2
                )
                assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])


async def test_same_uuid_concurrent_apply_and_regular_transfer_after_release(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    request = approval(doc)
    responses = await asyncio.gather(
        *(env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request) for _ in range(2))
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity) VALUES('transfer','inv-a',$1,$2,1)",
            env["zone"],
            env["empty"],
        )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 7
        )


async def test_return_from_ready_preserves_freeze_but_requires_selected_new_count(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/return-to-count", json=cmd(doc, reason="recount again")
    )
    doc = response.json()
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["freeze"]["state"] == "frozen"
    doc = await count_item(env, doc, (await items(env, doc))[0], 9)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]


async def test_unscoped_active_warehouse_task_blocks_pilot_without_blocking_warehouse(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    before = await physical_state(env["pool"])
    request = approval(doc)
    async with env["pool"].acquire() as conn:
        user = await conn.fetchval(
            "INSERT INTO public.users(username,email,hashed_password) VALUES('unknown-scope','unknown@example.invalid','test') RETURNING id"
        )
        async with conn.transaction():
            tid = await conn.fetchval(
                "INSERT INTO wms.tasks(task_type,created_by) VALUES('picking',$1) RETURNING task_id",
                user,
            )
            response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
            assert response.status_code == 409, response.text
            assert response.json()["error_code"] == "INVENTORY_TASK_ADMISSION_BUSY"
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])
    status = (await env["client"].get(f"{BASE}/{doc['document_id']}/freeze")).json()
    assert status["conflicting_operations"][0]["scope_unknown"] is True
    async with env["pool"].acquire() as conn:
        await conn.execute("UPDATE wms.tasks SET status='cancelled' WHERE task_id=$1", tid)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert response.status_code == 200, response.text


async def test_unrelated_holderless_registration_does_not_invalidate_other_sku(physical_env):
    env = physical_env
    doc, _ = await ready_document(env)
    async with env["pool"].acquire() as conn:
        await conn.execute(
            "INSERT INTO wms.kiz(kiz_code,product_id,lifecycle_status,created_by) VALUES('UNRELATED-REGISTERED','inv-b','registered','test')"
        )
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200, response.text


async def test_apply_disabled_does_not_change_ready_document(physical_env, monkeypatch):
    env = physical_env
    doc, _ = await ready_document(env)
    before = await physical_state(env["pool"])
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 503
    assert before == await physical_state(env["pool"])
    assert (await latest(env, doc))["status"] == "pending_approval"


async def test_adjustments_are_visible_in_existing_history_and_integrity(physical_env):
    from datetime import date

    env = physical_env
    doc, _ = await ready_document(env)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 200, response.text
    movement_id = response.json()["movements"][0]["movement_id"]
    response = await env["client"].post("/api/system/validate-integrity")
    assert response.status_code == 200 and response.json() == [], response.text
    response = await env["client"].get(
        "/api/operations-history",
        params=dict(
            date_from=date.today().isoformat(),
            date_to=date.today().isoformat(),
            product_id="inv-a",
            movement_type="adjust",
        ),
    )
    assert response.status_code == 200, response.text
    events = response.json()["items"]
    event = next(e for e in events if e["event_id"].startswith(f"movement:{movement_id}:"))
    response = await env["client"].get("/api/operations-history/" + event["event_id"])
    assert response.status_code == 200, response.text
    assert any(m["source_type"] == "inventory_document" for m in response.json()["movements"])
    from app.infrastructure.database.repositories.system_repository import SystemRepository

    await SystemRepository(env["pool"]).recalculate_inventory(product_id="inv-a")
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 8
        )
        assert (
            await conn.fetchval(
                "SELECT physical_quantity FROM wms.inventory_document_stock_components WHERE document_id=$1",
                doc["document_id"],
            )
            == 10
        )
