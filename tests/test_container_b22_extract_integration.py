"""PostgreSQL acceptance tests for Stage 3B Phase B2.2 container extract."""

import asyncio
from decimal import Decimal

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.dependencies import get_db_pool
from app.api.v1.router import api_router
from app.core.exceptions import ContainerOperationConflictError
from app.core.schemas.container_operations import ContainerExtractRequest
from app.core.services.container_extract_service import ContainerExtractService
from app.core.services.container_operation_idempotency_service import (
    ContainerOperationIdempotencyService,
)
from app.infrastructure.database.repositories.container_operation_repository import (
    ContainerOperationRepository,
)
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def b22_scope(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.products(id,name) VALUES
               ('b22-a','B22 A'),('b22-b','B22 B')"""
        )
        source = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B22-SOURCE',0) "
                "RETURNING location_id,location_code"
            )
        )
        destination = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B22-DESTINATION',0) "
                "RETURNING location_id,location_code"
            )
        )
    return source, destination


@pytest.fixture
async def b22_client(kiz_pool):
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


async def add_loose(conn, location_id, product_id, quantity, batch_number=None):
    await conn.execute(
        """INSERT INTO wms.inventory(
               product_id,location_id,quantity,status,batch_number,container_code
           ) VALUES ($1,$2,$3,'available',$4,NULL)""",
        product_id,
        location_id,
        Decimal(quantity),
        batch_number,
    )


def fill_payload(container_id, operation_id, items):
    return {
        "source_system": "b22-setup",
        "external_operation_id": operation_id,
        "author": "setup",
        "container_id": container_id,
        "items": items,
    }


def extract_payload(container_id, operation_id="extract-1", items=None):
    return {
        "source_system": "manual",
        "external_operation_id": operation_id,
        "author": "operator",
        "container_id": container_id,
        "items": items
        or [
            {
                "external_line_id": "1",
                "product_id": "b22-a",
                "quantity": "2.00",
                "batch_number": None,
            }
        ],
    }


async def seed_open_container(client, pool, source, qr_code, scopes):
    """scopes: (product_id, batch_number, contained, loose_after)."""
    container_id = await create_empty(client, source["location_code"], qr_code)
    items = []
    async with pool.acquire() as conn:
        for line, (product_id, batch_number, contained, loose_after) in enumerate(scopes, 1):
            await add_loose(
                conn,
                source["location_id"],
                product_id,
                Decimal(contained) + Decimal(loose_after),
                batch_number,
            )
            items.append(
                {
                    "external_line_id": str(line),
                    "product_id": product_id,
                    "quantity": str(contained),
                    "batch_number": batch_number,
                }
            )
    response = await client.post(
        "/api/container-operations/fill",
        json=fill_payload(container_id, f"setup-{qr_code}", items),
    )
    assert response.status_code == 201, response.text
    return container_id


async def scope_state(conn, container_id, product_id, batch_number=None):
    container = await conn.fetchrow(
        "SELECT qr_code,location_id,status FROM wms.containers WHERE container_id=$1",
        container_id,
    )
    return dict(
        await conn.fetchrow(
            """SELECT
                 COALESCE((SELECT quantity FROM wms.inventory
                   WHERE product_id=$1 AND location_id=$2 AND status='available'
                     AND batch_number IS NOT DISTINCT FROM $3::varchar
                     AND container_code IS NULL),0) loose,
                 COALESCE((SELECT quantity FROM wms.inventory
                   WHERE product_id=$1 AND location_id=$2 AND status='available'
                     AND batch_number IS NOT DISTINCT FROM $3::varchar
                     AND container_code=$4),0) contained,
                 COALESCE((SELECT quantity FROM wms.container_contents
                   WHERE container_id=$5 AND product_id=$1
                     AND batch_number IS NOT DISTINCT FROM $3::varchar
                     AND status='active'),0) contents,
                 $6::varchar status""",
            product_id,
            container["location_id"],
            batch_number,
            container["qr_code"],
            container_id,
            container["status"],
        )
    )


async def extract_movement_count(conn):
    return await conn.fetchval(
        """SELECT count(*) FROM wms.movements m
           JOIN wms.container_operations o
             ON o.operation_id=m.source_id AND m.source_type='container_operation'
           WHERE o.operation_type='extract'"""
    )


async def test_partial_extract_conserves_stock_and_has_structured_ledger(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-PARTIAL", [("b22-a", None, "5", "10")]
    )

    response = await b22_client.post(
        "/api/container-operations/extract", json=extract_payload(container_id)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["operation_type"] == "extract"
    assert body["container_status"] == "open"
    assert body["items"][0]["quantity"] == "2.00"
    assert len(body["items"][0]["movement_refs"]) == 2

    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state == {
            "loose": Decimal("12"),
            "contained": Decimal("3"),
            "contents": Decimal("3"),
            "status": "open",
        }
        movements = await conn.fetch(
            """SELECT r.movement_ref,m.from_location_id,m.to_location_id,
                      m.container_code,m.source_type,m.source_id,m.source_item_id
               FROM wms.container_operations o
               JOIN wms.container_operation_items oi ON oi.operation_id=o.operation_id
               JOIN wms.movement_registry r
                 ON r.movement_ref IN (oi.outgoing_movement_ref,oi.incoming_movement_ref)
               JOIN wms.movements m ON m.movement_id=r.movement_id
                 AND m.created_at=r.movement_created_at
               WHERE o.operation_type='extract'
               ORDER BY CASE WHEN r.movement_ref=oi.outgoing_movement_ref THEN 1 ELSE 2 END"""
        )
        assert len(movements) == 2
        assert movements[0]["from_location_id"] == source["location_id"]
        assert movements[0]["to_location_id"] is None
        assert movements[0]["container_code"] == "B22-PARTIAL"
        assert movements[1]["from_location_id"] is None
        assert movements[1]["to_location_id"] == source["location_id"]
        assert movements[1]["container_code"] is None
        assert movements[0]["source_id"] == movements[1]["source_id"]
        assert movements[0]["source_item_id"] == movements[1]["source_item_id"]
        assert [row["movement_ref"] for row in movements] == body["items"][0]["movement_refs"]


async def test_full_extract_deletes_last_current_scope_and_sets_empty(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-FULL", [("b22-a", None, "3", "12")]
    )
    payload = extract_payload(container_id)
    payload["items"][0]["quantity"] = "3.00"
    response = await b22_client.post("/api/container-operations/extract", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["container_status"] == "empty"

    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state == {
            "loose": Decimal("15"),
            "contained": Decimal("0"),
            "contents": Decimal("0"),
            "status": "empty",
        }
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_contents WHERE container_id=$1",
                container_id,
            )
            == 0
        )


async def test_full_extract_one_scope_keeps_other_scope_and_container_open(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        "B22-MIXED",
        [("b22-a", None, "3", "2"), ("b22-b", None, "4", "1")],
    )
    payload = extract_payload(container_id)
    payload["items"][0]["quantity"] = "3"
    response = await b22_client.post("/api/container-operations/extract", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["container_status"] == "open"

    async with kiz_pool.acquire() as conn:
        first = await scope_state(conn, container_id, "b22-a")
        second = await scope_state(conn, container_id, "b22-b")
        assert first["contained"] == first["contents"] == 0
        assert first["loose"] == 5
        assert second["contained"] == second["contents"] == 4
        assert second["loose"] == 1
        assert second["status"] == "open"


async def test_exact_batch_null_batch_and_decimal_are_isolated(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        "B22-BATCH",
        [
            ("b22-a", "A", "3", "1"),
            ("b22-a", "B", "4", "2"),
            ("b22-a", None, "2.50", "1.50"),
        ],
    )
    response = await b22_client.post(
        "/api/container-operations/extract",
        json=extract_payload(
            container_id,
            "extract-batches",
            [
                {
                    "external_line_id": "1",
                    "product_id": "b22-a",
                    "quantity": "2",
                    "batch_number": "A",
                },
                {
                    "external_line_id": "2",
                    "product_id": "b22-a",
                    "quantity": "1.25",
                    "batch_number": None,
                },
            ],
        ),
    )
    assert response.status_code == 201, response.text
    async with kiz_pool.acquire() as conn:
        batch_a = await scope_state(conn, container_id, "b22-a", "A")
        batch_b = await scope_state(conn, container_id, "b22-a", "B")
        null_batch = await scope_state(conn, container_id, "b22-a", None)
        assert (batch_a["loose"], batch_a["contained"], batch_a["contents"]) == (3, 1, 1)
        assert (batch_b["loose"], batch_b["contained"], batch_b["contents"]) == (2, 4, 4)
        assert (null_batch["loose"], null_batch["contained"], null_batch["contents"]) == (
            Decimal("2.75"),
            Decimal("1.25"),
            Decimal("1.25"),
        )


@pytest.mark.parametrize("status", ["empty", "sealed", "blocked"])
async def test_extract_rejects_non_open_container(b22_client, kiz_pool, b22_scope, status):
    source, _ = b22_scope
    if status == "empty":
        container_id = await create_empty(b22_client, source["location_code"], "B22-EMPTY")
    else:
        container_id = await seed_open_container(
            b22_client, kiz_pool, source, f"B22-{status}", [("b22-a", None, "5", "1")]
        )
        async with kiz_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wms.containers SET status=$2 WHERE container_id=$1",
                container_id,
                status,
            )
    response = await b22_client.post(
        "/api/container-operations/extract", json=extract_payload(container_id)
    )
    assert response.status_code == 409
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operations WHERE operation_type='extract'"
            )
            == 0
        )


async def test_missing_container_product_scope_and_insufficient_are_safe(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    missing_container = await b22_client.post(
        "/api/container-operations/extract", json=extract_payload(999999)
    )
    assert missing_container.status_code == 404

    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-VALIDATION", [("b22-a", None, "5", "1")]
    )
    missing_product_payload = extract_payload(container_id, "missing-product")
    missing_product_payload["items"][0]["product_id"] = "missing"
    assert (
        await b22_client.post("/api/container-operations/extract", json=missing_product_payload)
    ).status_code == 404

    missing_scope_payload = extract_payload(container_id, "missing-scope")
    missing_scope_payload["items"][0]["batch_number"] = "OTHER"
    assert (
        await b22_client.post("/api/container-operations/extract", json=missing_scope_payload)
    ).status_code == 409

    insufficient_payload = extract_payload(container_id, "insufficient")
    insufficient_payload["items"][0]["quantity"] = "6"
    assert (
        await b22_client.post("/api/container-operations/extract", json=insufficient_payload)
    ).status_code == 409
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state["loose"] == 1
        assert state["contained"] == state["contents"] == 5
        assert await extract_movement_count(conn) == 0


async def test_multi_item_second_failure_rolls_back_everything(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        "B22-ATOMIC",
        [("b22-a", None, "4", "1"), ("b22-b", None, "1", "2")],
    )
    response = await b22_client.post(
        "/api/container-operations/extract",
        json=extract_payload(
            container_id,
            "atomic",
            [
                {
                    "external_line_id": "1",
                    "product_id": "b22-a",
                    "quantity": "2",
                    "batch_number": None,
                },
                {
                    "external_line_id": "2",
                    "product_id": "b22-b",
                    "quantity": "2",
                    "batch_number": None,
                },
            ],
        ),
    )
    assert response.status_code == 409
    async with kiz_pool.acquire() as conn:
        assert (await scope_state(conn, container_id, "b22-a"))["contained"] == 4
        assert (await scope_state(conn, container_id, "b22-b"))["contained"] == 1
        assert await extract_movement_count(conn) == 0
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operations WHERE operation_type='extract'"
            )
            == 0
        )


async def test_exact_replay_is_order_independent_and_conflict_is_409(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        "B22-IDEMPOTENT",
        [("b22-a", None, "5", "1"), ("b22-b", None, "4", "2")],
    )
    items = [
        {"external_line_id": "2", "product_id": "b22-b", "quantity": "1", "batch_number": None},
        {"external_line_id": "1", "product_id": "b22-a", "quantity": "2", "batch_number": None},
    ]
    payload = extract_payload(container_id, "idem", items)
    first = await b22_client.post("/api/container-operations/extract", json=payload)
    replay = await b22_client.post(
        "/api/container-operations/extract",
        json={**payload, "items": list(reversed(items))},
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()

    changed = extract_payload(container_id, "idem", [dict(item) for item in items])
    changed["items"][1]["quantity"] = "3"
    conflict = await b22_client.post("/api/container-operations/extract", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "CONTAINER_IDEMPOTENCY_CONFLICT"
    async with kiz_pool.acquire() as conn:
        assert await extract_movement_count(conn) == 4
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operations WHERE operation_type='extract'"
            )
            == 1
        )


@pytest.mark.parametrize(
    "checkpoint",
    [
        "after_outgoing_movement",
        "after_incoming_movement",
        "after_contents_update",
        "after_status_update",
        "before_result_save",
    ],
)
async def test_faults_roll_back_entire_extract_graph(b22_client, kiz_pool, b22_scope, checkpoint):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        f"B22-ROLLBACK-{checkpoint}",
        [("b22-a", None, "3", "2")],
    )
    repository = ContainerOperationRepository(kiz_pool)
    service = ContainerExtractService(repository, ContainerOperationIdempotencyService(repository))

    async def fail(name):
        if name == checkpoint:
            raise RuntimeError("injected failure")

    service._checkpoint = fail
    payload = extract_payload(container_id, f"rollback-{checkpoint}")
    payload["items"][0]["quantity"] = "3"
    with pytest.raises(RuntimeError, match="injected failure"):
        await service.extract(ContainerExtractRequest.model_validate(payload))

    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state == {
            "loose": Decimal("2"),
            "contained": Decimal("3"),
            "contents": Decimal("3"),
            "status": "open",
        }
        assert await extract_movement_count(conn) == 0
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_operations WHERE operation_type='extract'"
            )
            == 0
        )


async def test_concurrent_exact_replay_has_one_physical_extract(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-CONCURRENT-REPLAY", [("b22-a", None, "5", "1")]
    )
    payload = extract_payload(container_id)
    first, second = await asyncio.gather(
        b22_client.post("/api/container-operations/extract", json=payload),
        b22_client.post("/api/container-operations/extract", json=payload),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    async with kiz_pool.acquire() as conn:
        assert await extract_movement_count(conn) == 2
        assert (await scope_state(conn, container_id, "b22-a"))["contained"] == 3


async def test_concurrent_extracts_same_scope_do_not_overdraw(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-OVERDRAW", [("b22-a", None, "5", "0")]
    )
    first, second = await asyncio.gather(
        b22_client.post(
            "/api/container-operations/extract",
            json=extract_payload(
                container_id,
                "overdraw-1",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b22-a",
                        "quantity": "4",
                        "batch_number": None,
                    }
                ],
            ),
        ),
        b22_client.post(
            "/api/container-operations/extract",
            json=extract_payload(
                container_id,
                "overdraw-2",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b22-a",
                        "quantity": "4",
                        "batch_number": None,
                    }
                ],
            ),
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state["loose"] == 4
        assert state["contained"] == state["contents"] == 1
        assert await extract_movement_count(conn) == 2


async def test_extract_serializes_with_fill_same_container(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-FILL-RACE", [("b22-a", None, "5", "5")]
    )
    extract, fill = await asyncio.gather(
        b22_client.post(
            "/api/container-operations/extract",
            json=extract_payload(
                container_id,
                "extract-race",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b22-a",
                        "quantity": "4",
                        "batch_number": None,
                    }
                ],
            ),
        ),
        b22_client.post(
            "/api/container-operations/fill",
            json=fill_payload(
                container_id,
                "fill-race",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b22-a",
                        "quantity": "4",
                        "batch_number": None,
                    }
                ],
            ),
        ),
    )
    assert extract.status_code == fill.status_code == 201
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state["loose"] == 5
        assert state["contained"] == state["contents"] == 5


async def test_extract_remains_supported_while_legacy_unpack_is_unavailable(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-NO-LEGACY-UNPACK", [("b22-a", None, "5", "0")]
    )
    extract = await b22_client.post(
        "/api/container-operations/extract", json=extract_payload(container_id)
    )
    legacy = await b22_client.post(
        f"/api/containers/{container_id}/unpack",
        json={"qr_code": "B22-NO-LEGACY-UNPACK", "product_id": "b22-a", "quantity": 1},
    )
    assert extract.status_code == 201, extract.text
    assert legacy.status_code in {404, 405}

async def test_extract_remains_supported_while_legacy_move_is_unavailable(
    b22_client, kiz_pool, b22_scope
):
    source, destination = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-NO-LEGACY-MOVE", [("b22-a", None, "5", "0")]
    )
    extract = await b22_client.post(
        "/api/container-operations/extract", json=extract_payload(container_id)
    )
    legacy = await b22_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": destination["location_code"]},
    )
    assert extract.status_code == 201, extract.text
    assert legacy.status_code in {404, 405}

async def test_db_guard_rejects_generic_contained_writer(
    b22_client, kiz_pool, b22_scope
):
    source, _ = b22_scope
    await seed_open_container(
        b22_client, kiz_pool, source, "B22-GENERIC-GUARD", [("b22-a", None, "5", "0")]
    )
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity,container_code) "
                "VALUES ('transfer','b22-a',$1,1,'B22-GENERIC-GUARD')",
                source["location_id"],
            )

@pytest.mark.parametrize("changed_field", ["quantity", "product", "batch", "container"])
async def test_idempotency_conflict_covers_all_extract_intent_fields(
    b22_client, kiz_pool, b22_scope, changed_field
):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, f"B22-CONFLICT-{changed_field}", [("b22-a", None, "5", "1")]
    )
    other_container_id = await create_empty(
        b22_client, source["location_code"], f"B22-CONFLICT-OTHER-{changed_field}"
    )
    original = extract_payload(container_id, "intent-conflict")
    first = await b22_client.post("/api/container-operations/extract", json=original)
    assert first.status_code == 201, first.text

    changed = extract_payload(container_id, "intent-conflict")
    if changed_field == "quantity":
        changed["items"][0]["quantity"] = "1"
    elif changed_field == "product":
        changed["items"][0]["product_id"] = "b22-b"
    elif changed_field == "batch":
        changed["items"][0]["batch_number"] = "OTHER"
    else:
        changed["container_id"] = other_container_id

    conflict = await b22_client.post("/api/container-operations/extract", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "CONTAINER_IDEMPOTENCY_CONFLICT"
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state["loose"] == 3
        assert state["contained"] == state["contents"] == 3
        assert await extract_movement_count(conn) == 2


async def test_extract_serializes_with_fill_different_scope(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client,
        kiz_pool,
        source,
        "B22-FILL-DIFFERENT",
        [("b22-a", None, "5", "0"), ("b22-b", None, "1", "4")],
    )
    extract, fill = await asyncio.gather(
        b22_client.post(
            "/api/container-operations/extract",
            json=extract_payload(container_id, "extract-different"),
        ),
        b22_client.post(
            "/api/container-operations/fill",
            json=fill_payload(
                container_id,
                "fill-different",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b22-b",
                        "quantity": "4",
                        "batch_number": None,
                    }
                ],
            ),
        ),
    )
    assert extract.status_code == fill.status_code == 201
    async with kiz_pool.acquire() as conn:
        first = await scope_state(conn, container_id, "b22-a")
        second = await scope_state(conn, container_id, "b22-b")
        assert (first["loose"], first["contained"], first["contents"]) == (2, 3, 3)
        assert (second["loose"], second["contained"], second["contents"]) == (0, 5, 5)


async def test_extract_observes_generic_loose_destination_change(b22_client, kiz_pool, b22_scope):
    source, _ = b22_scope
    container_id = await seed_open_container(
        b22_client, kiz_pool, source, "B22-LOOSE-RACE", [("b22-a", None, "5", "5")]
    )
    repository = ContainerOperationRepository(kiz_pool)
    service = ContainerExtractService(repository, ContainerOperationIdempotencyService(repository))
    container_locked = asyncio.Event()
    release = asyncio.Event()

    async def pause(name):
        if name == "after_container_lock":
            container_locked.set()
            await release.wait()

    service._checkpoint = pause
    task = asyncio.create_task(
        service.extract(ContainerExtractRequest.model_validate(extract_payload(container_id)))
    )
    await container_locked.wait()
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO wms.movements(
                   movement_type,product_id,from_location_id,quantity,container_code
               ) VALUES ('transfer','b22-a',$1,4,NULL)""",
            source["location_id"],
        )
    release.set()
    result = await task
    assert result.container_status == "open"
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b22-a")
        assert state["loose"] == 3
        assert state["contained"] == state["contents"] == 3
