"""PostgreSQL/API acceptance tests for Stage 3B Phase B3."""

import asyncio
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.dependencies import get_db_pool
from app.api.v1.router import api_router
from app.core.schemas.container_operations import ContainerMoveRequest, ContainerUnpackAllRequest
from app.core.services.container_move_service import ContainerMoveService
from app.core.services.container_operation_idempotency_service import (
    ContainerOperationIdempotencyService,
)
from app.core.services.container_unpack_all_service import ContainerUnpackAllService
from app.infrastructure.database.repositories.container_operation_repository import (
    ContainerOperationRepository,
)
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def b3_scope(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES " "('b3-a','B3 A'),('b3-b','B3 B')"
        )
        warehouse = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B3-WAREHOUSE',0) "
                "RETURNING location_id,location_code"
            )
        )
        source = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,parent_location_id,level) "
                "VALUES ('B3-SOURCE',$1,1) RETURNING location_id,location_code",
                warehouse["location_id"],
            )
        )
        destination = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,parent_location_id,level) "
                "VALUES ('B3-DESTINATION',$1,1) RETURNING location_id,location_code",
                warehouse["location_id"],
            )
        )
        other = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B3-OTHER-WAREHOUSE',0) "
                "RETURNING location_id,location_code"
            )
        )
    return source, destination, other


@pytest.fixture
async def b3_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def create_empty(client, location_code, qr_code):
    response = await client.post(
        "/api/containers/register",
        json={
            "qr_code": qr_code,
            "container_type": "box",
            "location_code": location_code,
            "contents": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["container_id"]


async def seed_open(client, pool, source, qr_code, scopes):
    container_id = await create_empty(client, source["location_code"], qr_code)
    items = []
    async with pool.acquire() as conn:
        for line, (product, batch, contained, loose) in enumerate(scopes, 1):
            await conn.execute(
                "INSERT INTO wms.inventory(product_id,location_id,quantity,status,batch_number) "
                "VALUES ($1,$2,$3,'available',$4)",
                product,
                source["location_id"],
                Decimal(contained) + Decimal(loose),
                batch,
            )
            items.append(
                {
                    "external_line_id": str(line),
                    "product_id": product,
                    "quantity": str(contained),
                    "batch_number": batch,
                }
            )
    response = await client.post(
        "/api/container-operations/fill",
        json={
            "source_system": "b3-setup",
            "external_operation_id": f"fill-{qr_code}",
            "author": "setup",
            "container_id": container_id,
            "items": items,
        },
    )
    assert response.status_code == 201, response.text
    return container_id


def move_payload(container_id, operation_id="move-1", destination="B3-DESTINATION"):
    return {
        "source_system": "manual",
        "external_operation_id": operation_id,
        "author": "operator",
        "container_id": container_id,
        "to_location_code": destination,
    }


def unpack_payload(container_id, operation_id="unpack-1"):
    return {
        "source_system": "manual",
        "external_operation_id": operation_id,
        "author": "operator",
        "container_id": container_id,
    }


async def inventory_quantity(conn, product, location, qr_code=None, batch=None):
    return await conn.fetchval(
        "SELECT COALESCE((SELECT quantity FROM wms.inventory WHERE product_id=$1 "
        "AND location_id=$2 AND status='available' "
        "AND batch_number IS NOT DISTINCT FROM $3::varchar "
        "AND container_code IS NOT DISTINCT FROM $4::varchar),0)",
        product,
        location,
        batch,
        qr_code,
    )


async def test_move_open_multi_scope_conserves_projection_and_ledger(b3_client, kiz_pool, b3_scope):
    source, destination, _ = b3_scope
    container_id = await seed_open(
        b3_client,
        kiz_pool,
        source,
        "B3-MOVE-MULTI",
        [("b3-a", None, "2", "8"), ("b3-a", "LOT", "3", "4"), ("b3-b", None, "1", "5")],
    )
    response = await b3_client.post(
        "/api/container-operations/move",
        json=move_payload(container_id, destination=destination["location_code"]),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["container_status"] == "open"
    assert len(body["items"]) == 3
    assert all(len(item["movement_refs"]) == 1 for item in body["items"])

    async with kiz_pool.acquire() as conn:
        container = await conn.fetchrow(
            "SELECT location_id,status FROM wms.containers WHERE container_id=$1", container_id
        )
        assert (container["location_id"], container["status"]) == (
            destination["location_id"],
            "open",
        )
        for product, batch, quantity, _ in [
            ("b3-a", None, Decimal("2"), "8"),
            ("b3-a", "LOT", Decimal("3"), "4"),
            ("b3-b", None, Decimal("1"), "5"),
        ]:
            assert (
                await inventory_quantity(
                    conn, product, source["location_id"], "B3-MOVE-MULTI", batch
                )
                == 0
            )
            assert (
                await inventory_quantity(
                    conn, product, destination["location_id"], "B3-MOVE-MULTI", batch
                )
                == quantity
            )
        rows = await conn.fetch(
            """SELECT oi.movement_ref,m.from_location_id,m.to_location_id,m.container_code,
                      m.source_type,m.source_id,m.source_item_id
               FROM wms.container_operations o
               JOIN wms.container_operation_items oi USING(operation_id)
               JOIN wms.movement_registry r ON r.movement_ref=oi.movement_ref
               JOIN wms.movements m ON m.movement_id=r.movement_id
                AND m.created_at=r.movement_created_at
               WHERE o.operation_id=$1 ORDER BY oi.external_line_id""",
            body["operation_id"],
        )
        assert len(rows) == 3
        assert all(row["from_location_id"] == source["location_id"] for row in rows)
        assert all(row["to_location_id"] == destination["location_id"] for row in rows)
        assert all(row["source_type"] == "container_operation" for row in rows)
        assert all(row["source_id"] == body["operation_id"] for row in rows)
        assert len({row["source_item_id"] for row in rows}) == 3


async def test_empty_same_location_and_sealed_move_semantics(b3_client, kiz_pool, b3_scope):
    source, destination, _ = b3_scope
    empty_id = await create_empty(b3_client, source["location_code"], "B3-EMPTY-MOVE")
    moved = await b3_client.post(
        "/api/container-operations/move",
        json=move_payload(empty_id, "empty", destination["location_code"]),
    )
    assert moved.status_code == 201 and moved.json()["items"] == []

    replay_noop_payload = move_payload(empty_id, "same", destination["location_code"])
    noop = await b3_client.post("/api/container-operations/move", json=replay_noop_payload)
    replay = await b3_client.post("/api/container-operations/move", json=replay_noop_payload)
    assert noop.status_code == 201 and noop.json()["items"] == []
    assert replay.json() == noop.json()

    sealed_id = await seed_open(
        b3_client, kiz_pool, source, "B3-SEALED", [("b3-a", None, "2", "3")]
    )
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "UPDATE wms.containers SET status='sealed' WHERE container_id=$1", sealed_id
        )
    sealed = await b3_client.post(
        "/api/container-operations/move",
        json=move_payload(sealed_id, "sealed", destination["location_code"]),
    )
    assert sealed.status_code == 201 and sealed.json()["container_status"] == "sealed"
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='container_operation' "
                "AND source_id IN (SELECT operation_id FROM wms.container_operations "
                "WHERE operation_type='move' AND container_id=$1)",
                empty_id,
            )
            == 0
        )


async def test_move_replay_conflict_blocked_cross_warehouse_and_missing(
    b3_client, kiz_pool, b3_scope
):
    source, destination, other = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-REPLAY", [("b3-a", None, "2", "3")]
    )
    payload = move_payload(container_id, destination=destination["location_code"])
    first = await b3_client.post("/api/container-operations/move", json=payload)
    replay = await b3_client.post("/api/container-operations/move", json=payload)
    assert replay.status_code == 201 and replay.json() == first.json()
    conflict = await b3_client.post(
        "/api/container-operations/move",
        json={**payload, "to_location_code": source["location_code"]},
    )
    assert conflict.status_code == 409

    blocked_id = await create_empty(b3_client, source["location_code"], "B3-BLOCKED")
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "UPDATE wms.containers SET status='blocked' WHERE container_id=$1", blocked_id
        )
    assert (
        await b3_client.post(
            "/api/container-operations/move",
            json=move_payload(blocked_id, "blocked", destination["location_code"]),
        )
    ).status_code == 409
    cross_id = await create_empty(b3_client, source["location_code"], "B3-CROSS")
    assert (
        await b3_client.post(
            "/api/container-operations/move",
            json=move_payload(cross_id, "cross", other["location_code"]),
        )
    ).status_code == 409
    assert (
        await b3_client.post("/api/container-operations/move", json=move_payload(999999, "missing"))
    ).status_code == 404
    assert (
        await b3_client.post(
            "/api/container-operations/move",
            json=move_payload(cross_id, "missing-location", "NO-SUCH-LOCATION"),
        )
    ).status_code == 404


async def test_unpack_all_multi_scope_conserves_and_creates_pairs(b3_client, kiz_pool, b3_scope):
    source, _, _ = b3_scope
    container_id = await seed_open(
        b3_client,
        kiz_pool,
        source,
        "B3-UNPACK",
        [("b3-a", None, "2", "8"), ("b3-b", "LOT", "3", "4")],
    )
    response = await b3_client.post(
        "/api/container-operations/unpack-all", json=unpack_payload(container_id)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["container_status"] == "empty" and len(body["items"]) == 2
    assert all(len(item["movement_refs"]) == 2 for item in body["items"])
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_contents WHERE container_id=$1 AND status='active'",
                container_id,
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory WHERE container_code='B3-UNPACK'"
            )
            == 0
        )
        assert await inventory_quantity(conn, "b3-a", source["location_id"]) == 10
        assert await inventory_quantity(conn, "b3-b", source["location_id"], batch="LOT") == 7
        links = await conn.fetch(
            """SELECT oi.outgoing_movement_ref,oi.incoming_movement_ref,
                      outgoing.source_type,outgoing.source_id,outgoing.source_item_id,
                      incoming.source_item_id AS incoming_source_item_id
               FROM wms.container_operation_items oi
               JOIN wms.movement_registry ro ON ro.movement_ref=oi.outgoing_movement_ref
               JOIN wms.movements outgoing ON outgoing.movement_id=ro.movement_id
                AND outgoing.created_at=ro.movement_created_at
               JOIN wms.movement_registry ri ON ri.movement_ref=oi.incoming_movement_ref
               JOIN wms.movements incoming ON incoming.movement_id=ri.movement_id
                AND incoming.created_at=ri.movement_created_at
               WHERE oi.operation_id=$1""",
            body["operation_id"],
        )
        assert len(links) == 2
        assert all(row["source_type"] == "container_operation" for row in links)
        assert all(row["source_id"] == body["operation_id"] for row in links)
        assert all(row["source_item_id"] == row["incoming_source_item_id"] for row in links)


async def test_unpack_replay_conflict_and_invalid_statuses(b3_client, kiz_pool, b3_scope):
    source, _, _ = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-UNPACK-REPLAY", [("b3-a", None, "2", "3")]
    )
    payload = unpack_payload(container_id)
    first = await b3_client.post("/api/container-operations/unpack-all", json=payload)
    replay = await b3_client.post("/api/container-operations/unpack-all", json=payload)
    assert replay.status_code == 201 and replay.json() == first.json()
    other_id = await create_empty(b3_client, source["location_code"], "B3-UNPACK-OTHER")
    assert (
        await b3_client.post(
            "/api/container-operations/unpack-all", json={**payload, "container_id": other_id}
        )
    ).status_code == 409

    for status in ("empty", "sealed", "blocked"):
        candidate = await create_empty(b3_client, source["location_code"], f"B3-UNPACK-{status}")
        if status != "empty":
            async with kiz_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE wms.containers SET status=$2 WHERE container_id=$1", candidate, status
                )
        response = await b3_client.post(
            "/api/container-operations/unpack-all",
            json=unpack_payload(candidate, f"invalid-{status}"),
        )
        assert response.status_code == 409


@pytest.mark.parametrize(
    "kind,checkpoint",
    [
        ("move", "after_first_movement"),
        ("move", "after_location_update"),
        ("unpack", "after_first_scope_movements"),
        ("unpack", "after_status_update"),
    ],
)
async def test_fault_injection_rolls_back_complete_graph(
    b3_client, kiz_pool, b3_scope, kind, checkpoint
):
    source, destination, _ = b3_scope
    container_id = await seed_open(
        b3_client,
        kiz_pool,
        source,
        f"B3-ROLLBACK-{kind}-{checkpoint}",
        [("b3-a", None, "2", "3")],
    )
    repository = ContainerOperationRepository(kiz_pool)
    idempotency = ContainerOperationIdempotencyService(repository)
    if kind == "move":
        service = ContainerMoveService(repository, idempotency)
        request = ContainerMoveRequest.model_validate(
            move_payload(container_id, f"rollback-{checkpoint}", destination["location_code"])
        )
        call = service.move
    else:
        service = ContainerUnpackAllService(repository, idempotency)
        request = ContainerUnpackAllRequest.model_validate(
            unpack_payload(container_id, f"rollback-{checkpoint}")
        )
        call = service.unpack_all

    async def fail(name):
        if name == checkpoint:
            raise RuntimeError("injected failure")

    service._checkpoint = fail
    with pytest.raises(RuntimeError, match="injected failure"):
        await call(request)
    async with kiz_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT location_id,status FROM wms.containers WHERE container_id=$1", container_id
        )
        assert row["location_id"] == source["location_id"] and row["status"] == "open"
        assert (
            await inventory_quantity(
                conn, "b3-a", source["location_id"], f"B3-ROLLBACK-{kind}-{checkpoint}"
            )
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operations WHERE source_system='manual'"
            )
            == 0
        )


async def test_concurrent_exact_move_replay_has_one_effect(b3_client, kiz_pool, b3_scope):
    source, destination, _ = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-CONCURRENT", [("b3-a", None, "2", "3")]
    )
    payload = move_payload(container_id, destination=destination["location_code"])
    first, second = await asyncio.gather(
        b3_client.post("/api/container-operations/move", json=payload),
        b3_client.post("/api/container-operations/move", json=payload),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operation_items WHERE operation_id=$1",
                first.json()["operation_id"],
            )
            == 1
        )


async def test_legacy_move_route_is_unavailable(b3_client, kiz_pool, b3_scope):
    source, destination, _ = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-NO-LEGACY", [("b3-a", None, "2", "3")]
    )
    response = await b3_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": destination["location_code"]},
    )
    assert response.status_code in {404, 405}

async def assert_container_projection(conn, container_id):
    container = await conn.fetchrow(
        "SELECT qr_code,location_id FROM wms.containers WHERE container_id=$1", container_id
    )
    mismatch = await conn.fetchval(
        """SELECT
               EXISTS (
                   SELECT 1 FROM wms.container_contents cc
                   LEFT JOIN wms.inventory i
                     ON i.product_id=cc.product_id AND i.location_id=$2
                    AND i.status='available'
                    AND i.batch_number IS NOT DISTINCT FROM cc.batch_number
                    AND i.container_code=$3
                   WHERE cc.container_id=$1 AND cc.status='active'
                     AND (i.inventory_id IS NULL OR i.quantity IS DISTINCT FROM cc.quantity)
               ) OR EXISTS (
                   SELECT 1 FROM wms.inventory i
                   LEFT JOIN wms.container_contents cc
                     ON cc.container_id=$1 AND cc.product_id=i.product_id
                    AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
                    AND cc.status='active'
                   WHERE i.container_code=$3
                     AND (i.location_id<>$2 OR i.status<>'available'
                          OR cc.content_id IS NULL OR cc.quantity IS DISTINCT FROM i.quantity)
               )""",
        container_id,
        container["location_id"],
        container["qr_code"],
    )
    assert mismatch is False


@pytest.mark.parametrize(
    "first_kind,second_kind,expected_statuses",
    [
        ("move", "fill", {201, 409}),
        ("move", "extract", {201}),
        ("move", "move", {201}),
        ("move", "unpack", {201}),
        ("unpack", "fill", {201}),
        ("unpack", "extract", {201, 409}),
        ("unpack", "unpack", {201, 409}),
    ],
)
async def test_controlled_operation_race_matrix_serializes_on_container(
    b3_client, kiz_pool, b3_scope, first_kind, second_kind, expected_statuses
):
    source, destination, _ = b3_scope
    qr_code = f"B3-RACE-{first_kind}-{second_kind}"
    container_id = await seed_open(b3_client, kiz_pool, source, qr_code, [("b3-a", None, "2", "5")])

    def request(kind, suffix):
        if kind == "move":
            return b3_client.post(
                "/api/container-operations/move",
                json=move_payload(container_id, f"race-{suffix}", destination["location_code"]),
            )
        if kind == "unpack":
            return b3_client.post(
                "/api/container-operations/unpack-all",
                json=unpack_payload(container_id, f"race-{suffix}"),
            )
        if kind == "fill":
            return b3_client.post(
                "/api/container-operations/fill",
                json={
                    "source_system": "manual",
                    "external_operation_id": f"race-{suffix}",
                    "author": "operator",
                    "container_id": container_id,
                    "items": [
                        {
                            "external_line_id": "1",
                            "product_id": "b3-a",
                            "quantity": "1",
                            "batch_number": None,
                        }
                    ],
                },
            )
        return b3_client.post(
            "/api/container-operations/extract",
            json={
                "source_system": "manual",
                "external_operation_id": f"race-{suffix}",
                "author": "operator",
                "container_id": container_id,
                "items": [
                    {
                        "external_line_id": "1",
                        "product_id": "b3-a",
                        "quantity": "1",
                        "batch_number": None,
                    }
                ],
            },
        )

    first, second = await asyncio.gather(
        request(first_kind, "first"), request(second_kind, "second")
    )
    assert first.status_code in expected_statuses, first.text
    assert second.status_code in expected_statuses, second.text
    assert 201 in {first.status_code, second.status_code}
    async with kiz_pool.acquire() as conn:
        await assert_container_projection(conn, container_id)
        total = await conn.fetchval(
            "SELECT COALESCE(sum(quantity),0) FROM wms.inventory WHERE product_id='b3-a'"
        )
        assert total == 7


async def test_controlled_move_is_the_only_public_move_path(
    b3_client, kiz_pool, b3_scope
):
    source, destination, _ = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-CONTROLLED-ONLY", [("b3-a", None, "2", "5")]
    )
    controlled = await b3_client.post(
        "/api/container-operations/move",
        json=move_payload(container_id, "controlled-only", destination["location_code"]),
    )
    legacy = await b3_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": source["location_code"]},
    )
    assert controlled.status_code == 201, controlled.text
    assert legacy.status_code in {404, 405}
    async with kiz_pool.acquire() as conn:
        await assert_container_projection(conn, container_id)

async def test_unpack_all_is_the_only_public_full_unpack_path(
    b3_client, kiz_pool, b3_scope
):
    source, _, _ = b3_scope
    container_id = await seed_open(
        b3_client, kiz_pool, source, "B3-UNPACK-CONTROLLED-ONLY", [("b3-a", None, "2", "5")]
    )
    controlled = await b3_client.post(
        "/api/container-operations/unpack-all",
        json=unpack_payload(container_id, "controlled-only-unpack"),
    )
    legacy = await b3_client.post(
        f"/api/containers/{container_id}/unpack",
        json={"qr_code": "B3-UNPACK-CONTROLLED-ONLY", "product_id": "b3-a", "quantity": 1},
    )
    assert controlled.status_code == 201, controlled.text
    assert legacy.status_code in {404, 405}

async def test_other_container_operation_is_not_globally_serialized(b3_client, kiz_pool, b3_scope):
    source, destination, _ = b3_scope
    first_id = await seed_open(
        b3_client, kiz_pool, source, "B3-PARALLEL-1", [("b3-a", None, "2", "5")]
    )
    second_id = await seed_open(
        b3_client, kiz_pool, source, "B3-PARALLEL-2", [("b3-b", None, "2", "5")]
    )
    repository = ContainerOperationRepository(kiz_pool)
    service = ContainerMoveService(repository, ContainerOperationIdempotencyService(repository))
    locked = asyncio.Event()
    release = asyncio.Event()

    async def pause(name):
        if name == "after_container_lock":
            locked.set()
            await release.wait()

    service._checkpoint = pause
    first_task = asyncio.create_task(
        service.move(
            ContainerMoveRequest.model_validate(
                move_payload(first_id, "parallel-1", destination["location_code"])
            )
        )
    )
    await asyncio.wait_for(locked.wait(), timeout=2)
    second = await asyncio.wait_for(
        b3_client.post(
            "/api/container-operations/move",
            json=move_payload(second_id, "parallel-2", destination["location_code"]),
        ),
        timeout=2,
    )
    assert second.status_code == 201, second.text
    release.set()
    first = await asyncio.wait_for(first_task, timeout=2)
    assert first.operation_type == "move"
