"""PostgreSQL acceptance tests for Stage 3B Phase B2.1 container fill."""

import asyncio
from decimal import Decimal

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.dependencies import get_db_pool
from app.api.v1.router import api_router
from app.core.services.container_fill_service import ContainerFillService
from app.core.services.container_operation_idempotency_service import (
    ContainerOperationIdempotencyService,
)
from app.infrastructure.database.repositories.container_operation_repository import (
    ContainerOperationRepository,
)
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def b21_scope(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.products(id,name) VALUES
               ('b21-a','B21 A'),('b21-b','B21 B')"""
        )
        source = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B21-SOURCE',0) "
                "RETURNING location_id,location_code"
            )
        )
        destination = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B21-DESTINATION',0) "
                "RETURNING location_id,location_code"
            )
        )
    return source, destination


@pytest.fixture
async def b21_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def create_empty(client, location_code, qr_code="B21-C"):
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


def fill_payload(container_id, operation_id="fill-1", items=None):
    return {
        "source_system": "manual",
        "external_operation_id": operation_id,
        "author": "operator",
        "container_id": container_id,
        "items": items
        or [
            {
                "external_line_id": "1",
                "product_id": "b21-a",
                "quantity": "4.00",
                "batch_number": None,
            }
        ],
    }


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


async def test_existing_register_empty_is_zero_effect_create_flow(b21_client, kiz_pool, b21_scope):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT status FROM wms.containers WHERE container_id=$1", container_id
            )
            == "empty"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.container_contents WHERE container_id=$1",
                container_id,
            )
            == 0
        )
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == 0


async def test_fill_new_scope_conserves_stock_and_has_structured_ledger(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")

    response = await b21_client.post(
        "/api/container-operations/fill", json=fill_payload(container_id)
    )
    assert response.status_code == 201, response.text
    assert response.json()["items"][0]["quantity"] == "4.00"
    assert len(response.json()["items"][0]["movement_refs"]) == 2

    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b21-a")
        assert state == {
            "loose": Decimal("6"),
            "contained": Decimal("4"),
            "contents": Decimal("4"),
            "status": "open",
        }
        movements = await conn.fetch(
            """SELECT from_location_id,to_location_id,container_code,source_type,
                      source_id,source_item_id
               FROM wms.movements WHERE source_type='container_operation'
               ORDER BY movement_id"""
        )
        assert len(movements) == 2
        assert movements[0]["from_location_id"] == source["location_id"]
        assert movements[0]["to_location_id"] is None
        assert movements[0]["container_code"] is None
        assert movements[1]["from_location_id"] is None
        assert movements[1]["to_location_id"] == source["location_id"]
        assert movements[1]["container_code"] == "B21-C"
        assert movements[0]["source_id"] == movements[1]["source_id"]
        assert movements[0]["source_item_id"] == movements[1]["source_item_id"]


async def test_fill_existing_scope_and_decimal_quantity(b21_client, kiz_pool, b21_scope):
    source, _ = b21_scope
    container_id = await create_empty(
        b21_client, source["location_code"], "B21-EXISTING"
    )
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10.50", "BATCH")

    initial = await b21_client.post(
        "/api/container-operations/fill",
        json=fill_payload(container_id, "fill-existing-initial", [{
            "external_line_id": "1", "product_id": "b21-a",
            "quantity": "4.00", "batch_number": "BATCH",
        }]),
    )
    assert initial.status_code == 201, initial.text

    response = await b21_client.post(
        "/api/container-operations/fill",
        json=fill_payload(container_id, "fill-existing-decimal", [{
            "external_line_id": "1", "product_id": "b21-a",
            "quantity": "1.50", "batch_number": "BATCH",
        }]),
    )
    assert response.status_code == 201, response.text
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b21-a", "BATCH")
        assert state["loose"] == Decimal("5")
        assert state["contained"] == state["contents"] == Decimal("5.5")

async def test_fill_multiple_products_batches_is_atomic(b21_client, kiz_pool, b21_scope):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10", None)
        await add_loose(conn, source["location_id"], "b21-a", "8", "A")
        await add_loose(conn, source["location_id"], "b21-b", "7", None)

    items = [
        {"external_line_id": "3", "product_id": "b21-b", "quantity": "2", "batch_number": None},
        {"external_line_id": "1", "product_id": "b21-a", "quantity": "3", "batch_number": None},
        {"external_line_id": "2", "product_id": "b21-a", "quantity": "4", "batch_number": "A"},
    ]
    response = await b21_client.post(
        "/api/container-operations/fill",
        json=fill_payload(container_id, items=items),
    )
    assert response.status_code == 201, response.text
    async with kiz_pool.acquire() as conn:
        assert (await scope_state(conn, container_id, "b21-a"))["contained"] == 3
        assert (await scope_state(conn, container_id, "b21-a", "A"))["contained"] == 4
        assert (await scope_state(conn, container_id, "b21-b"))["contained"] == 2


@pytest.mark.parametrize("status", ["sealed", "blocked"])
async def test_fill_rejects_closed_container(b21_client, kiz_pool, b21_scope, status):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "UPDATE wms.containers SET status=$2 WHERE container_id=$1",
            container_id,
            status,
        )
        await add_loose(conn, source["location_id"], "b21-a", "10")
    response = await b21_client.post(
        "/api/container-operations/fill", json=fill_payload(container_id)
    )
    assert response.status_code == 409
    async with kiz_pool.acquire() as conn:
        assert (await scope_state(conn, container_id, "b21-a"))["loose"] == 10
        assert await conn.fetchval("SELECT count(*) FROM wms.container_operations") == 0


async def test_missing_container_and_product_are_404(b21_client, b21_scope):
    source, _ = b21_scope
    missing_container = await b21_client.post(
        "/api/container-operations/fill", json=fill_payload(999999)
    )
    assert missing_container.status_code == 404

    container_id = await create_empty(b21_client, source["location_code"])
    payload = fill_payload(container_id)
    payload["items"][0]["product_id"] = "missing"
    missing_product = await b21_client.post("/api/container-operations/fill", json=payload)
    assert missing_product.status_code == 404


async def test_insufficient_and_second_item_failure_roll_back_everything(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")
        await add_loose(conn, source["location_id"], "b21-b", "1")
    payload = fill_payload(
        container_id,
        items=[
            {"external_line_id": "1", "product_id": "b21-a", "quantity": "4", "batch_number": None},
            {"external_line_id": "2", "product_id": "b21-b", "quantity": "2", "batch_number": None},
        ],
    )
    response = await b21_client.post("/api/container-operations/fill", json=payload)
    assert response.status_code == 409
    async with kiz_pool.acquire() as conn:
        assert (await scope_state(conn, container_id, "b21-a"))["loose"] == 10
        assert (await scope_state(conn, container_id, "b21-b"))["loose"] == 1
        assert (
            await conn.fetchval(
                "SELECT status FROM wms.containers WHERE container_id=$1", container_id
            )
            == "empty"
        )
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.container_operations") == 0


async def test_exact_replay_is_order_independent_and_conflict_is_409(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")
        await add_loose(conn, source["location_id"], "b21-b", "10")
    items = [
        {"external_line_id": "2", "product_id": "b21-b", "quantity": "2", "batch_number": None},
        {"external_line_id": "1", "product_id": "b21-a", "quantity": "3", "batch_number": None},
    ]
    payload = fill_payload(container_id, items=items)
    first = await b21_client.post("/api/container-operations/fill", json=payload)
    replay_payload = {**payload, "items": list(reversed(items))}
    replay = await b21_client.post("/api/container-operations/fill", json=replay_payload)
    assert replay.status_code == 201
    assert replay.json() == first.json()

    conflict_payload = fill_payload(container_id, items=[dict(items[0]), dict(items[1])])
    conflict_payload["items"][1]["quantity"] = "4"
    conflict = await b21_client.post("/api/container-operations/fill", json=conflict_payload)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "CONTAINER_IDEMPOTENCY_CONFLICT"
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='container_operation'"
            )
            == 4
        )


@pytest.mark.parametrize(
    "checkpoint",
    ["after_outgoing_movement", "after_contents_update", "before_result_save"],
)
async def test_faults_roll_back_physical_graph(kiz_pool, b21_client, b21_scope, checkpoint):
    source, _ = b21_scope
    container_id = await create_empty(
        b21_client, source["location_code"], f"B21-ROLLBACK-{checkpoint}"
    )
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")

    repository = ContainerOperationRepository(kiz_pool)
    service = ContainerFillService(repository, ContainerOperationIdempotencyService(repository))

    async def fail(name):
        if name == checkpoint:
            raise RuntimeError("injected failure")

    service._checkpoint = fail
    from app.core.schemas.container_operations import ContainerFillRequest

    with pytest.raises(RuntimeError, match="injected failure"):
        await service.fill(ContainerFillRequest.model_validate(fill_payload(container_id)))

    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b21-a")
        assert state["loose"] == 10
        assert state["contained"] == state["contents"] == 0
        assert state["status"] == "empty"
        assert await conn.fetchval("SELECT count(*) FROM wms.container_operations") == 0
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='container_operation'"
            )
            == 0
        )


async def test_concurrent_exact_replay_has_one_physical_fill(b21_client, kiz_pool, b21_scope):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")
    payload = fill_payload(container_id)
    first, second = await asyncio.gather(
        b21_client.post("/api/container-operations/fill", json=payload),
        b21_client.post("/api/container-operations/fill", json=payload),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    async with kiz_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='container_operation'"
            )
            == 2
        )
        assert (await scope_state(conn, container_id, "b21-a"))["contained"] == 4


async def test_concurrent_fills_same_loose_stock_do_not_overdraw(b21_client, kiz_pool, b21_scope):
    source, _ = b21_scope
    first_container = await create_empty(b21_client, source["location_code"], "B21-C1")
    second_container = await create_empty(b21_client, source["location_code"], "B21-C2")
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")
    first, second = await asyncio.gather(
        b21_client.post(
            "/api/container-operations/fill",
            json=fill_payload(
                first_container,
                "fill-c1",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b21-a",
                        "quantity": "7",
                        "batch_number": None,
                    }
                ],
            ),
        ),
        b21_client.post(
            "/api/container-operations/fill",
            json=fill_payload(
                second_container,
                "fill-c2",
                [
                    {
                        "external_line_id": "1",
                        "product_id": "b21-a",
                        "quantity": "7",
                        "batch_number": None,
                    }
                ],
            ),
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    async with kiz_pool.acquire() as conn:
        states = [
            await scope_state(conn, first_container, "b21-a"),
            await scope_state(conn, second_container, "b21-a"),
        ]
        assert sum(state["contained"] for state in states) == 7
        assert max(state["loose"] for state in states) == 3


async def test_fill_remains_supported_while_legacy_move_is_unavailable(
    b21_client, kiz_pool, b21_scope
):
    source, destination = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")
    fill_response = await b21_client.post(
        "/api/container-operations/fill", json=fill_payload(container_id)
    )
    legacy = await b21_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": destination["location_code"]},
    )
    assert fill_response.status_code == 201, fill_response.text
    assert legacy.status_code in {404, 405}

async def test_fill_remains_supported_while_legacy_unpack_is_unavailable(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "6")
    fill_response = await b21_client.post(
        "/api/container-operations/fill",
        json=fill_payload(container_id, items=[{
            "external_line_id": "1", "product_id": "b21-a",
            "quantity": "2", "batch_number": None,
        }]),
    )
    legacy = await b21_client.post(
        f"/api/containers/{container_id}/unpack",
        json={"qr_code": "unused", "product_id": "b21-a", "quantity": 1},
    )
    assert fill_response.status_code == 201, fill_response.text
    assert legacy.status_code in {404, 405}

async def test_fill_races_generic_loose_outgoing_without_negative_stock(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")

    async def generic_outgoing():
        try:
            async with kiz_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO wms.movements(
                               movement_type,product_id,from_location_id,quantity
                           ) VALUES ('transfer','b21-a',$1,8)""",
                        source["location_id"],
                    )
            return "ok"
        except asyncpg.PostgresError:
            return "conflict"

    fill_response, generic_result = await asyncio.gather(
        b21_client.post("/api/container-operations/fill", json=fill_payload(container_id)),
        generic_outgoing(),
    )
    assert (fill_response.status_code, generic_result) in {
        (201, "conflict"),
        (409, "ok"),
    }
    async with kiz_pool.acquire() as conn:
        loose = await conn.fetchval(
            """SELECT COALESCE(sum(quantity),0) FROM wms.inventory
               WHERE product_id='b21-a' AND location_id=$1
                 AND container_code IS NULL""",
            source["location_id"],
        )
        assert loose >= 0
        state = await scope_state(conn, container_id, "b21-a")
        assert state["contained"] == state["contents"]


@pytest.mark.parametrize("changed_field", ["quantity", "product", "batch", "container"])
async def test_idempotency_conflict_covers_all_physical_intent_fields(
    b21_client, kiz_pool, b21_scope, changed_field
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"], "B21-IDEM-1")
    other_container_id = await create_empty(b21_client, source["location_code"], "B21-IDEM-2")
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")

    original = fill_payload(container_id, "idem-conflict")
    first = await b21_client.post("/api/container-operations/fill", json=original)
    assert first.status_code == 201

    changed = fill_payload(container_id, "idem-conflict")
    if changed_field == "quantity":
        changed["items"][0]["quantity"] = "5"
    elif changed_field == "product":
        changed["items"][0]["product_id"] = "b21-b"
    elif changed_field == "batch":
        changed["items"][0]["batch_number"] = "OTHER"
    else:
        changed["container_id"] = other_container_id

    conflict = await b21_client.post("/api/container-operations/fill", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "CONTAINER_IDEMPOTENCY_CONFLICT"


async def test_concurrent_different_fills_same_container_are_serialized(
    b21_client, kiz_pool, b21_scope
):
    source, _ = b21_scope
    container_id = await create_empty(b21_client, source["location_code"])
    async with kiz_pool.acquire() as conn:
        await add_loose(conn, source["location_id"], "b21-a", "10")

    item = [
        {
            "external_line_id": "1",
            "product_id": "b21-a",
            "quantity": "7",
            "batch_number": None,
        }
    ]
    first, second = await asyncio.gather(
        b21_client.post(
            "/api/container-operations/fill",
            json=fill_payload(container_id, "same-container-1", item),
        ),
        b21_client.post(
            "/api/container-operations/fill",
            json=fill_payload(container_id, "same-container-2", item),
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    async with kiz_pool.acquire() as conn:
        state = await scope_state(conn, container_id, "b21-a")
        assert state["contained"] == state["contents"] == 7
        assert state["loose"] == 3
