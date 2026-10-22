"""Opt-in tests use ONLY local disposable kiz_pool with current WMS triggers."""

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers
from app.shared.config import settings

BASE = "/api/inventory-documents"
ROOT = Path(__file__).resolve().parents[1]


def cmd(document, **changes):
    return dict(
        idempotency_key=str(uuid4()),
        expected_version=document["version"],
        user_name="counter",
        **changes,
    )


@pytest.fixture
async def inventory_env(kiz_pool, monkeypatch):
    async with kiz_pool.acquire() as c:
        existing_functions = dict(
            await c.fetch(
                "SELECT p.oid,pg_get_functiondef(p.oid) FROM pg_proc p "
                "WHERE p.pronamespace='wms'::regnamespace AND p.prokind='f'"
            )
        )
        before = await c.fetchval(
            "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
        )
        await c.execute(
            (ROOT / "scripts/migrations/20261022_add_inventory_documents.sql").read_text()
        )
        assert before == await c.fetchval(
            "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
        )
        for oid, definition in existing_functions.items():
            assert definition == await c.fetchval("SELECT pg_get_functiondef($1::oid)", oid)
        await c.execute(
            "INSERT INTO public.products(id,name) VALUES ('inv-a','A'),('inv-b','B'),('inv-new','New')"
        )
        root = await c.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('INV-WAREHOUSE',0) RETURNING location_id"
        )
        other_root = await c.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('OTHER-WAREHOUSE',0) RETURNING location_id"
        )
        ids = []
        for name, parent, level in [
            ("Zone", root, 1),
            ("Bin", None, 2),
            ("Empty", root, 1),
            ("Inactive", root, 1),
            ("Other", other_root, 1),
        ]:
            if parent is None:
                parent = ids[0]
            ids.append(
                await c.fetchval(
                    "INSERT INTO wms.locations(name,parent_location_id,level) VALUES($1,$2,$3) RETURNING location_id",
                    name,
                    parent,
                    level,
                )
            )
        await c.execute("UPDATE wms.locations SET is_active=false WHERE location_id=$1", ids[3])
        for pid, loc, qty in [
            ("inv-a", ids[0], 10),
            ("inv-a", ids[1], 4),
            ("inv-b", ids[1], 2),
            ("inv-b", ids[3], 1),
        ]:
            await c.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive',$1,$2,$3)",
                pid,
                loc,
                qty,
            )
    monkeypatch.setattr(settings, "INVENTORY_MODULE_ENABLED", True)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield dict(
            pool=kiz_pool,
            client=client,
            root=root,
            other_root=other_root,
            zone=ids[0],
            bin=ids[1],
            empty=ids[2],
            inactive=ids[3],
            outside=ids[4],
        )


async def create(env, *, mode="full", locations=None, product_ids=None, start=True):
    response = await env["client"].post(
        BASE,
        json=dict(
            idempotency_key=str(uuid4()),
            user_name="counter",
            title="Inventory",
            mode=mode,
            warehouse_location_id=env["root"],
            locations=locations or [],
            product_ids=product_ids or [],
        ),
    )
    assert response.status_code == 201, response.text
    document = response.json()
    if start:
        response = await env["client"].post(
            f"{BASE}/{document['document_id']}/start", json=cmd(document)
        )
        assert response.status_code == 200, response.text
        document = response.json()
    return document


async def items(env, document):
    response = await env["client"].get(f"{BASE}/{document['document_id']}/items")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def count_item(env, document, item, quantity):
    base = f"{BASE}/{document['document_id']}/items/{item['item_id']}/attempts"
    response = await env["client"].post(base, json=cmd(document))
    assert response.status_code == 201, response.text
    result = response.json()
    response = await env["client"].post(
        f"{base}/{result['attempt']['attempt_id']}/complete",
        json=cmd(result["document"], actual_quantity=str(quantity)),
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


async def physical_state(pool):
    async with pool.acquire() as c:
        return [
            await c.fetchval(
                f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY {key}), '[]'::jsonb)::text FROM {table} t"
            )
            for table, key in [
                ("wms.inventory", "inventory_id"),
                ("wms.movements", "movement_id,created_at"),
                ("wms.kiz", "kiz_id"),
                ("wms.movement_registry", "movement_ref"),
            ]
        ]


async def test_full_scope_includes_empty_parent_and_inactive_stock(inventory_env):
    env = inventory_env
    document = await create(env)
    scope = (await env["client"].get(f"{BASE}/{document['document_id']}/scope")).json()
    assert {r["location_id"] for r in scope} == {
        env[k] for k in ("root", "zone", "bin", "empty", "inactive")
    }
    rows = await items(env, document)
    assert len(rows) == 4
    inactive = next(r for r in rows if r["location_id"] == env["inactive"])
    assert "inactive_location" in inactive["conflicts"]
    assert all(r["actual_quantity"] is None and r["count_status"] == "not_counted" for r in rows)


@pytest.mark.parametrize(
    "selection,sku,expected",
    [
        ("zone", [], 3),
        ("direct_zone", [], 1),
        (None, ["inv-a"], 2),
        ("zone", ["inv-b"], 1),
        ("empty", [], 0),
    ],
)
async def test_partial_filters(inventory_env, selection, sku, expected):
    env = inventory_env
    locations = (
        []
        if selection is None
        else [
            dict(
                location_id=env["zone" if selection == "direct_zone" else selection],
                include_descendants=selection != "direct_zone",
            )
        ]
    )
    document = await create(env, mode="partial", locations=locations, product_ids=sku)
    assert len(await items(env, document)) == expected
    if selection is None:
        assert document["location_count"] == 5  # SKU-only claims even empty addresses.


async def test_scope_is_frozen_after_new_location_and_tree_move(inventory_env):
    env = inventory_env
    document = await create(env, mode="partial", locations=[dict(location_id=env["zone"])])
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.locations(name,parent_location_id,level) VALUES('New 02',$1,2)",
            env["zone"],
        )
        await c.execute(
            "UPDATE wms.locations SET parent_location_id=$2 WHERE location_id=$1",
            env["bin"],
            env["other_root"],
        )
    scope = (await env["client"].get(f"{BASE}/{document['document_id']}/scope")).json()
    assert {r["location_id"] for r in scope} == {env["zone"], env["bin"]}
    response = await env["client"].patch(
        f"{BASE}/{document['document_id']}",
        json=dict(**cmd(document), title="Changed", mode="full", warehouse_location_id=env["root"]),
    )
    assert response.status_code == 409


async def test_concurrent_overlapping_start_is_atomic_and_claims_release(inventory_env):
    env = inventory_env
    first = await create(env, mode="partial", product_ids=["inv-a"], start=False)
    second = await create(env, mode="partial", product_ids=["inv-b"], start=False)
    responses = await asyncio.gather(
        *[
            env["client"].post(f"{BASE}/{d['document_id']}/start", json=cmd(d))
            for d in (first, second)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    loser = second if winner["document_id"] == first["document_id"] else first
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_location_claims WHERE document_id=$1",
                loser["document_id"],
            )
            == 0
        )
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_document_locations WHERE document_id=$1",
                loser["document_id"],
            )
            == 0
        )
    close = await env["client"].post(
        f"{BASE}/{winner['document_id']}/close-without-adjustment",
        json=cmd(winner, reason="No apply"),
    )
    assert close.status_code == 200, close.text
    response = await env["client"].post(f"{BASE}/{loser['document_id']}/start", json=cmd(loser))
    assert response.status_code == 200, response.text


async def test_nonoverlapping_concurrent_documents(inventory_env):
    env = inventory_env
    docs = [
        await create(
            env,
            mode="partial",
            locations=[dict(location_id=env[k], include_descendants=False)],
            start=False,
        )
        for k in ("zone", "bin")
    ]
    responses = await asyncio.gather(
        *[env["client"].post(f"{BASE}/{d['document_id']}/start", json=cmd(d)) for d in docs]
    )
    assert [r.status_code for r in responses] == [200, 200]


async def test_zero_recounts_submission_decisions_and_no_physical_writes(inventory_env):
    env = inventory_env
    before = await physical_state(env["pool"])
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    rows = await items(env, doc)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    assert response.status_code == 409
    doc = await count_item(env, doc, rows[0], 0)
    row = (await items(env, doc))[0]
    assert Decimal(row["actual_quantity"]) == 0 and row["count_status"] == "counted"
    doc = await count_item(env, doc, row, 8)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    assert response.status_code == 200, response.text
    doc = response.json()["document"]
    assert doc["status"] == "pending_approval"
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/return-to-count", json=cmd(doc, reason="Check again")
    )
    assert response.status_code == 200
    doc = response.json()
    assert (
        await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    ).status_code == 409
    doc = await count_item(env, doc, row, 9)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    doc = response.json()["document"]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/close-without-adjustment", json=cmd(doc, reason="Stage 1")
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed_without_adjustment"
    history = (await env["client"].get(f"{BASE}/{doc['document_id']}/history")).json()
    assert any(e["action"] == "return_to_count" for e in history["items"])
    attempts = (
        await env["client"].get(f"{BASE}/{doc['document_id']}/items/{row['item_id']}/attempts")
    ).json()
    assert [Decimal(a["actual_quantity"]) for a in attempts["items"]] == [0, 8, 9]
    assert before == await physical_state(env["pool"])


async def test_progress_idempotency_stale_version_and_concurrent_replay(inventory_env):
    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    row = (await items(env, doc))[0]
    url = f"{BASE}/{doc['document_id']}/items/{row['item_id']}/attempts"
    request = cmd(doc)
    responses = await asyncio.gather(*[env["client"].post(url, json=request) for _ in range(2)])
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    result = responses[0].json()
    progress_url = f"{url}/{result['attempt']['attempt_id']}/progress"
    progress = cmd(result["document"], actual_quantity="0")
    response = await env["client"].put(progress_url, json=progress)
    assert response.status_code == 200, response.text
    assert (await env["client"].put(progress_url, json=progress)).json() == response.json()
    changed = dict(progress, actual_quantity="3")
    assert (await env["client"].put(progress_url, json=changed)).status_code == 409
    assert (
        await env["client"].put(progress_url, json=cmd(doc, actual_quantity="2"))
    ).status_code == 409


async def test_found_items_scope_and_receipt_after_start(inventory_env):
    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["empty"])], product_ids=["inv-new"]
    )
    url = f"{BASE}/{doc['document_id']}/items"
    for pid, loc in [("inv-a", env["empty"]), ("inv-new", env["outside"])]:
        assert (
            await env["client"].post(url, json=cmd(doc, product_id=pid, location_id=loc))
        ).status_code == 409
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-new',$1,3)",
            env["empty"],
        )
    response = await env["client"].post(
        url, json=cmd(doc, product_id="inv-new", location_id=env["empty"])
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert Decimal(result["item"]["initial_expected_quantity"]) == 0
    doc = result["document"]
    duplicate = await env["client"].post(
        url, json=cmd(doc, product_id="inv-new", location_id=env["empty"])
    )
    assert duplicate.status_code == 409
    doc = await count_item(env, doc, result["item"], 3)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == 3
    assert line["ambiguous"] and not line["safe_for_apply"]
    assert len(line["movements_since_start"]) == 1


async def test_multibatch_and_mixed_kiz_snapshot(inventory_env):
    env = inventory_env
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,batch_number) VALUES('receive','inv-a',$1,5,'BATCH-A')",
            env["zone"],
        )
    # Exercise actual current KIZ write/guard flow on the isolated database.
    for code in ("INV-K1", "INV-K2"):
        async with env["pool"].acquire() as c:
            async with c.transaction():
                await c.fetchrow(
                    "SELECT * FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1 AND batch_number IS NULL FOR UPDATE",
                    env["zone"],
                )
                await c.execute(
                    "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES($1,'inv-a',$2,'active','counter')",
                    code,
                    env["zone"],
                )
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    row = (await items(env, doc))[0]
    assert Decimal(row["initial_expected_quantity"]) == 13
    assert "multi_batch_allocation_undefined" in row["conflicts"]
    assert "active_kiz_physical_separation_required" in row["conflicts"]
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_document_stock_components WHERE document_id=$1",
                doc["document_id"],
            )
            == 2
        )
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_document_kiz_snapshot WHERE document_id=$1",
                doc["document_id"],
            )
            == 2
        )


async def test_movement_during_count_and_unverified_ship(inventory_env):
    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    row = (await items(env, doc))[0]
    base = f"{BASE}/{doc['document_id']}/items/{row['item_id']}/attempts"
    started = (await env["client"].post(base, json=cmd(doc))).json()
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship','inv-a',$1,2)",
            env["zone"],
        )
    completed = await env["client"].post(
        f"{base}/{started['attempt']['attempt_id']}/complete",
        json=cmd(started["document"], actual_quantity="8"),
    )
    assert completed.status_code == 200, completed.text
    assert "state_changed_during_count" in completed.json()["attempt"]["conflicts"]
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/reconcile", json=cmd(completed.json()["document"])
    )
    line = response.json()["lines"][0]
    assert "shipment_physical_time_unverified" in line["conflicts"]
    assert line["requires_recount"] and not line["safe_for_apply"]


async def test_completed_attempt_and_initial_snapshot_are_immutable(inventory_env):
    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    row = (await items(env, doc))[0]
    doc = await count_item(env, doc, row, 10)
    async with env["pool"].acquire() as c:
        for sql in [
            "UPDATE wms.inventory_document_stock_components SET physical_quantity=0 WHERE document_id=$1",
            "UPDATE wms.inventory_document_items SET initial_expected_quantity=0 WHERE document_id=$1",
            "UPDATE wms.inventory_count_attempts SET actual_quantity=0 WHERE document_id=$1",
            "DELETE FROM wms.inventory_count_attempts WHERE document_id=$1",
        ]:
            with pytest.raises(asyncpg.CheckViolationError):
                async with c.transaction():
                    await c.execute(sql, doc["document_id"])


async def test_draft_preview_edit_and_creation_replay(inventory_env):
    env = inventory_env
    payload = dict(
        idempotency_key=str(uuid4()),
        user_name="counter",
        title="Draft",
        mode="full",
        warehouse_location_id=env["root"],
    )
    first = await env["client"].post(BASE, json=payload)
    replay = await env["client"].post(BASE, json=payload)
    assert first.json() == replay.json()
    doc = first.json()
    preview = await env["client"].post(f"{BASE}/{doc['document_id']}/preview-scope")
    assert preview.status_code == 200, preview.text
    assert preview.json()["positive_item_count"] == 4
    assert "inactive_locations_included" in preview.json()["conflicts"]
    patch = dict(
        **cmd(doc),
        title="Partial",
        mode="partial",
        warehouse_location_id=env["root"],
        locations=[dict(location_id=env["empty"])],
    )
    response = await env["client"].patch(f"{BASE}/{doc['document_id']}", json=patch)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["mode"] == "partial"
    started = await env["client"].post(f"{BASE}/{doc['document_id']}/start", json=cmd(doc))
    assert started.status_code == 200
    assert started.json()["item_count"] == 0
    submitted = await env["client"].post(
        f"{BASE}/{doc['document_id']}/submit", json=cmd(started.json())
    )
    assert submitted.status_code == 200
    page = await env["client"].get(
        BASE, params={"status": "pending_approval", "limit": 1, "offset": 0}
    )
    assert page.json()["total"] == 1 and len(page.json()["items"]) == 1


async def test_concurrent_create_returns_single_document(inventory_env):
    env = inventory_env
    payload = dict(
        idempotency_key=str(uuid4()),
        user_name="counter",
        title="Draft",
        mode="full",
        warehouse_location_id=env["root"],
    )
    responses = await asyncio.gather(*[env["client"].post(BASE, json=payload) for _ in range(2)])
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_documents") == 1


async def test_warehouse_and_selection_validation(inventory_env):
    env = inventory_env
    for root, location, expected in [
        (env["zone"], env["bin"], 422),
        (env["root"], env["outside"], 409),
    ]:
        response = await env["client"].post(
            BASE,
            json=dict(
                idempotency_key=str(uuid4()),
                user_name="counter",
                title="Invalid",
                mode="partial",
                warehouse_location_id=root,
                locations=[dict(location_id=location)],
            ),
        )
        assert response.status_code == expected, response.text


async def test_stock_after_completed_count_is_preliminary_not_adjusted(inventory_env):
    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    item = (await items(env, doc))[0]
    doc = await count_item(env, doc, item, 8)
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,3)",
            env["zone"],
        )
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert Decimal(line["initial_expected_quantity"]) == 10
    assert Decimal(line["actual_quantity"]) == 8
    assert Decimal(line["initial_discrepancy"]) == -2
    assert Decimal(line["current_expected_quantity"]) == 13
    assert line["requires_recount"] and line["ambiguous"] and not line["safe_for_apply"]
    # Stage 1 does not turn actual 8 into a claimed physical target 11 or adjustment -5.
    assert "actual_adjusted" not in line


async def test_late_commit_with_old_created_at_is_detected(inventory_env):
    env = inventory_env
    async with env["pool"].acquire() as writer:
        transaction = writer.transaction()
        await transaction.start()
        try:
            await writer.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,3)",
                env["zone"],
            )
            doc = await create(
                env,
                mode="partial",
                locations=[dict(location_id=env["zone"], include_descendants=False)],
            )
            assert Decimal((await items(env, doc))[0]["initial_expected_quantity"]) == 10
            await transaction.commit()
        except BaseException:
            await transaction.rollback()
            raise
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == 13
    assert len(line["movements_since_start"]) == 1


async def test_kiz_change_without_movement_changes_eligibility(inventory_env):
    from app.core.schemas.kiz import KizAssignment
    from app.core.services.kiz_service import KizService
    from app.infrastructure.database.repositories.kiz_repository import KizRepository

    env = inventory_env
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    async with env["pool"].acquire() as c:
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
        before_movements = await c.fetchval("SELECT count(*) FROM wms.movements")
    await KizService(KizRepository(env["pool"])).assign(
        KizAssignment(
            kiz_code="INVENTORY-ASSIGNMENT", product_id="inv-a", location_code=code, author="tester"
        )
    )
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == 9
    assert "kiz_eligibility_changed" in line["conflicts"]
    assert line["movements_since_start"] == []
    assert len(line["kiz_events_since_start"]) == 1
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == before_movements


async def test_registered_holderless_is_context_not_capacity(inventory_env):
    env = inventory_env
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.kiz(kiz_code,product_id,lifecycle_status,created_by) VALUES('INVENTORY-REGISTERED','inv-a','registered','tester')"
        )
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    item = (await items(env, doc))[0]
    assert Decimal(item["initial_expected_quantity"]) == 10
    assert "registered_kiz_without_holder" in item["conflicts"]


async def test_initial_snapshot_failure_rolls_back_claims(inventory_env, monkeypatch):
    from app.infrastructure.database.repositories.inventory_document_repository import (
        InventoryDocumentRepository,
    )

    env = inventory_env
    doc = await create(env, start=False)

    async def fail(*args, **kwargs):
        from app.core.inventory_document_errors import InventoryDocumentError

        raise InventoryDocumentError("Simulated bounded evidence failure")

    monkeypatch.setattr(InventoryDocumentRepository, "capture", fail)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/start", json=cmd(doc))
    assert response.status_code == 409
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_location_claims") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_document_locations") == 0
        assert (
            await c.fetchval(
                "SELECT status FROM wms.inventory_documents WHERE document_id=$1",
                doc["document_id"],
            )
            == "draft"
        )


async def test_activation_and_deactivation_without_physical_movement(inventory_env):
    from app.core.schemas.kiz import KizTerminalRequest
    from app.core.services.kiz_service import KizService
    from app.infrastructure.database.repositories.kiz_repository import KizRepository

    env = inventory_env
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.kiz(kiz_code,product_id,lifecycle_status,created_by) VALUES('INV-ACTIVATION','inv-a','registered','tester')"
        )
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
        before = await c.fetchval("SELECT count(*) FROM wms.movements")
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    response = await env["client"].post(
        "/api/kiz/activate",
        json=dict(
            idempotency_key="inventory-activation",
            user_name="tester",
            kiz_codes=["INV-ACTIVATION"],
            location_code=code,
        ),
    )
    assert response.status_code == 200, response.text
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["lines"][0]["current_expected_quantity"]) == 9
    assert "kiz_eligibility_changed" in response.json()["lines"][0]["conflicts"]
    doc = response.json()["document"]
    await KizService(KizRepository(env["pool"])).terminate(
        "INV-ACTIVATION", "deactivated", KizTerminalRequest(author="tester", reason="Retest")
    )
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == 10
    assert len(line["kiz_events_since_start"]) == 2
    assert line["movements_since_start"] == []
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == before


async def test_container_extraction_is_loose_incoming_not_zero_net(inventory_env):
    env = inventory_env
    async with env["pool"].acquire() as c:
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
    response = await env["client"].post(
        "/api/containers/register",
        json=dict(qr_code="INV-CONTAINER", container_type="box", location_code=code, contents=[]),
    )
    assert response.status_code == 201, response.text
    container_id = response.json()["container_id"]
    payload = dict(
        source_system="test",
        external_operation_id="inventory-fill",
        author="tester",
        container_id=container_id,
        items=[dict(external_line_id="one", product_id="inv-a", quantity="3")],
    )
    response = await env["client"].post("/api/container-operations/fill", json=payload)
    assert response.status_code == 201, response.text
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    assert Decimal((await items(env, doc))[0]["initial_expected_quantity"]) == 7
    payload.update(
        external_operation_id="inventory-extract",
        items=[dict(external_line_id="two", product_id="inv-a", quantity="2")],
    )
    response = await env["client"].post("/api/container-operations/extract", json=payload)
    assert response.status_code == 201, response.text
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == 9
    assert len(line["movements_since_start"]) == 1
    assert line["movements_since_start"][0]["source_type"] == "container_operation"


async def test_fbs_location_is_ambiguous_even_before_delayed_writeoff(inventory_env, monkeypatch):
    env = inventory_env
    async with env["pool"].acquire() as c:
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", code)
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    item = (await items(env, doc))[0]
    doc = await count_item(env, doc, item, 8)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    line = response.json()["lines"][0]
    assert line["movements_since_start"] == []
    assert "configured_fbs_location_physical_boundary_unverified" in line["conflicts"]
    assert line["ambiguous"] and not line["safe_for_apply"]
    doc = response.json()["document"]
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity) VALUES('ship','inv-a',$1,2)",
            env["zone"],
        )
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/reconcile", json=cmd(doc))
    line = response.json()["lines"][0]
    assert Decimal(line["current_expected_quantity"]) == Decimal(line["actual_quantity"]) == 8
    assert line["ambiguous"] and line["requires_recount"] and not line["safe_for_apply"]


async def test_legacy_adjust_contract_after_additive_migration(inventory_env, monkeypatch):
    env = inventory_env
    monkeypatch.setattr(settings, "INVENTORY_MODULE_ENABLED", False)
    async with env["pool"].acquire() as c:
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
    for side, quantity in [("from_location_code", 2), ("to_location_code", 3)]:
        response = await env["client"].post(
            "/api/movements",
            json=[
                dict(movement_type="adjust", product_id="inv-a", quantity=quantity, **{side: code})
            ],
        )
        assert response.status_code == 201, response.text
        assert response.json()["created"][0]["movement_type"] == "adjust"
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1 AND batch_number IS NULL",
                env["zone"],
            )
            == 11
        )


async def test_decimal_actual_and_null_empty_ambiguity(inventory_env):
    env = inventory_env
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,batch_number) VALUES('receive','inv-a',$1,3,'')",
            env["zone"],
        )
    doc = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    item = (await items(env, doc))[0]
    assert "null_empty_collision" in item["conflicts"]
    doc = await count_item(env, doc, item, "12.25")
    assert Decimal((await items(env, doc))[0]["actual_quantity"]) == Decimal("12.25")


async def test_snapshot_does_not_lock_live_kiz_writer(inventory_env, monkeypatch):
    from app.core.schemas.kiz import KizAssignment, KizTerminalRequest
    from app.core.services.kiz_service import KizService
    from app.infrastructure.database.repositories.kiz_repository import KizRepository
    from app.infrastructure.database.repositories.inventory_document_repository import (
        InventoryDocumentRepository,
    )

    env = inventory_env
    kiz_service = KizService(KizRepository(env["pool"]))
    async with env["pool"].acquire() as c:
        code = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
    await kiz_service.assign(
        KizAssignment(
            kiz_code="INV-SNAPSHOT-LOCK", product_id="inv-a", location_code=code, author="tester"
        )
    )
    doc = await create(
        env,
        mode="partial",
        locations=[dict(location_id=env["zone"], include_descendants=False)],
        start=False,
    )
    snapshot_ready, writer_finished = asyncio.Event(), asyncio.Event()
    original = InventoryDocumentRepository.document

    async def pause_before_commit(repository, conn, document_id):
        result = await original(repository, conn, document_id)
        if result and result["status"] == "counting" and not snapshot_ready.is_set():
            snapshot_ready.set()
            await asyncio.wait_for(writer_finished.wait(), 5)
        return result

    monkeypatch.setattr(InventoryDocumentRepository, "document", pause_before_commit)
    start = asyncio.create_task(
        env["client"].post(f"{BASE}/{doc['document_id']}/start", json=cmd(doc))
    )
    try:
        await asyncio.wait_for(snapshot_ready.wait(), 5)
        await asyncio.wait_for(
            kiz_service.terminate(
                "INV-SNAPSHOT-LOCK",
                "deactivated",
                KizTerminalRequest(author="tester", reason="Normal operation"),
            ),
            3,
        )
    finally:
        writer_finished.set()
        response = await start
    assert response.status_code == 200, response.text
    # Snapshot sees the state at its transaction boundary, not the later KIZ state.
    row = (await items(env, response.json()))[0]
    assert Decimal(row["initial_expected_quantity"]) == 9
