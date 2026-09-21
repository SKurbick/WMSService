"""Real PostgreSQL acceptance, idempotency, concurrency and rollback tests for Phase 4."""

import asyncio
from decimal import Decimal

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.core.kiz_errors import KizConflictError, KizTransferConflictError
from app.core.schemas.kiz import KizAssignment, KizTerminalRequest
from app.core.schemas.kiz_operations import KizTransferRequest
from app.core.services.kiz_operation_idempotency_service import KizOperationIdempotencyService
from app.core.services.kiz_service import KizService
from app.core.services.kiz_transfer_service import KizTransferService
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.kiz_operation_repository import KizOperationRepository
from app.infrastructure.database.repositories.kiz_repository import KizRepository
from app.infrastructure.database.repositories.kiz_transfer_repository import KizTransferRepository
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def transfer_stock(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('sku','SKU'),('other','Other')"
        )
        source = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('SOURCE',0) RETURNING location_id,location_code"
        ))
        destination = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('DESTINATION',0) RETURNING location_id,location_code"
        ))
        third = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('THIRD',0) RETURNING location_id,location_code"
        ))
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','sku',$1,10),('receive','other',$1,2)""",
            source["location_id"],
        )
    return source, destination, third


@pytest.fixture
def transfer_service(kiz_pool):
    return KizTransferService(
        KizTransferRepository(kiz_pool),
        KizOperationIdempotencyService(KizOperationRepository()),
    )


@pytest.fixture
def kiz_service(kiz_pool):
    return KizService(KizRepository(kiz_pool))


@pytest.fixture
async def client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def assign(kiz_service, location, *codes, product="sku"):
    for code in codes:
        await kiz_service.assign(KizAssignment(
            kiz_code=code, product_id=product,
            location_code=location["location_code"], author="tester",
        ))


def request(stock, *, key="op-1", quantity=2, codes=("K1", "K2"),
            product="sku", destination=None):
    source, default_destination, _ = stock
    destination = destination or default_destination
    return KizTransferRequest.model_validate({
        "source_system": "manual", "external_operation_id": key, "author": "operator",
        "items": [{
            "external_line_id": "1", "product_id": product,
            "from_location_code": source["location_code"],
            "to_location_code": destination["location_code"],
            "quantity": quantity, "kiz_codes": list(codes),
        }],
    })


async def scope(conn, product, location_id):
    return await conn.fetchval(
        """SELECT quantity FROM wms.inventory WHERE product_id=$1 AND location_id=$2
           AND status='available' AND batch_number IS NULL AND container_code IS NULL""",
        product, location_id,
    )


@pytest.mark.parametrize("quantity,codes", [(2, ("K1", "K2")), (5, ("K1", "K2")), (3, ())])
async def test_identified_mixed_and_unidentified_transfer(
    kiz_pool, transfer_stock, transfer_service, kiz_service, quantity, codes,
):
    await assign(kiz_service, transfer_stock[0], "K1", "K2", "K3")
    result = await transfer_service.transfer(request(transfer_stock, quantity=quantity, codes=codes))
    item = result.items[0]
    assert item.quantity == quantity
    async with kiz_pool.acquire() as conn:
        assert await scope(conn, "sku", transfer_stock[0]["location_id"]) == Decimal(10) - Decimal(quantity)
        assert await scope(conn, "sku", transfer_stock[1]["location_id"]) == Decimal(quantity)
        for code in codes:
            assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code=$1", code) == transfer_stock[1]["location_id"]
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links") == len(codes)
        assert await conn.fetchval("SELECT movement_ref FROM wms.kiz_operation_items") == item.movement_ref
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_location_update_authorizations") == 0


async def test_http_response_replay_and_conflict(client, kiz_pool, transfer_stock, kiz_service):
    await assign(kiz_service, transfer_stock[0], "K1", "K2")
    payload = request(transfer_stock).model_dump(mode="json")
    first = await client.post("/api/kiz-operations/transfer", json=payload)
    replay = await client.post("/api/kiz-operations/transfer", json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    payload["items"][0]["quantity"] = 3
    conflict = await client.post("/api/kiz-operations/transfer", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "KIZ_IDEMPOTENCY_CONFLICT"
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 1


async def test_multiple_items_are_all_or_nothing(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    payload = request(transfer_stock, quantity=1, codes=("K1",)).model_dump()
    payload["items"].append({
        "external_line_id": "2", "product_id": "sku",
        "from_location_code": transfer_stock[0]["location_code"],
        "to_location_code": transfer_stock[2]["location_code"],
        "quantity": 10, "kiz_codes": [],
    })
    with pytest.raises(KizTransferConflictError):
        await transfer_service.transfer(KizTransferRequest.model_validate(payload))
    async with kiz_pool.acquire() as conn:
        assert await scope(conn, "sku", transfer_stock[0]["location_id"]) == 10
        assert await scope(conn, "sku", transfer_stock[1]["location_id"]) is None
        assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='K1'") == transfer_stock[0]["location_id"]
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 0


@pytest.mark.parametrize("case", [
    "unknown", "other_product", "other_location", "error", "deactivated", "shipped",
    "physical", "unidentified",
])
async def test_business_conflicts_are_atomic(
    kiz_pool, transfer_stock, transfer_service, kiz_service, case,
):
    source, _, third = transfer_stock
    await assign(kiz_service, source, "K1", "K2", "K3")
    data = request(transfer_stock, codes=("K1",))
    if case == "unknown":
        data = request(transfer_stock, codes=("MISSING",))
    elif case == "other_product":
        data = request(transfer_stock, codes=("K1",), product="other")
    elif case == "other_location":
        async with kiz_pool.acquire() as conn:
            await conn.execute("UPDATE wms.inventory SET quantity=quantity+1 WHERE product_id='sku' AND location_id=$1", source["location_id"])
        await transfer_service.transfer(request(transfer_stock, key="move-first", quantity=1, codes=("K1",), destination=third))
        data = request(transfer_stock, key="op-1", quantity=1, codes=("K1",))
    elif case in {"error", "deactivated"}:
        await kiz_service.terminate("K1", case, KizTerminalRequest(author="x", reason="test"))
    elif case == "shipped":
        async with kiz_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,
                       origin_type,assigned_at,closed_at,created_by,metadata)
                   VALUES ('SHIP','sku',NULL,'shipped','warehouse_assignment',now(),now(),'x','{}')"""
            )
        data = request(transfer_stock, codes=("SHIP",))
    elif case == "physical":
        data = request(transfer_stock, quantity=11, codes=())
    elif case == "unidentified":
        data = request(transfer_stock, quantity=8, codes=())

    async with kiz_pool.acquire() as conn:
        before = await scope(conn, "sku", source["location_id"])
        before_movements = await conn.fetchval("SELECT count(*) FROM wms.movements")
    with pytest.raises(KizTransferConflictError):
        await transfer_service.transfer(data)
    async with kiz_pool.acquire() as conn:
        assert await scope(conn, "sku", source["location_id"]) == before
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == before_movements
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == (1 if case == "other_location" else 0)


@pytest.mark.parametrize("checkpoint", [
    "after_operation_creation", "after_kiz_location_update", "after_movement_insert",
    "after_movement_ref", "after_movement_attachment", "after_kiz_movement_links",
    "before_result_payload",
])
async def test_fault_injection_rolls_everything_back(
    kiz_pool, transfer_stock, transfer_service, kiz_service, monkeypatch, checkpoint,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    original = transfer_service._checkpoint
    async with kiz_pool.acquire() as conn:
        before_movements = await conn.fetchval("SELECT count(*) FROM wms.movements")
        before_registry = await conn.fetchval("SELECT count(*) FROM wms.movement_registry")

    async def fail(name):
        if name == checkpoint:
            raise RuntimeError("fault injection")

    monkeypatch.setattr(transfer_service, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="fault injection"):
        await transfer_service.transfer(request(transfer_stock, quantity=1, codes=("K1",)))
    async with kiz_pool.acquire() as conn:
        assert await scope(conn, "sku", transfer_stock[0]["location_id"]) == 10
        assert await scope(conn, "sku", transfer_stock[1]["location_id"]) is None
        assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='K1'") == transfer_stock[0]["location_id"]
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == before_movements
        assert await conn.fetchval("SELECT count(*) FROM wms.movement_registry") == before_registry
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operation_items") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links") == 0
    monkeypatch.setattr(transfer_service, "_checkpoint", original)
    await transfer_service.transfer(request(transfer_stock, quantity=1, codes=("K1",)))


async def test_direct_location_update_and_incomplete_protocol_are_rejected(
    kiz_pool, transfer_stock, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as direct:
            await conn.execute("UPDATE wms.kiz SET location_id=$1 WHERE kiz_code='K1'", transfer_stock[1]["location_id"])
        assert direct.value.sqlstate == "P7501"

        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                operation_id = await conn.fetchval(
                    """INSERT INTO wms.kiz_operations(operation_type,source_system,
                       external_operation_id,request_fingerprint,author)
                       VALUES ('transfer','manual','incomplete',$1,'x') RETURNING operation_id""",
                    "a" * 64,
                )
                item_id = await conn.fetchval(
                    "INSERT INTO wms.kiz_operation_items(operation_id,external_line_id) VALUES ($1,'1') RETURNING operation_item_id",
                    operation_id,
                )
                kiz_id = await conn.fetchval("SELECT kiz_id FROM wms.kiz WHERE kiz_code='K1'")
                await conn.execute(
                    "SELECT wms.transfer_kiz_location($1,$2,'sku',$3,$4)", item_id, kiz_id,
                    transfer_stock[0]["location_id"], transfer_stock[1]["location_id"],
                )
                await conn.execute("UPDATE wms.kiz_operations SET result_payload='{}' WHERE operation_id=$1", operation_id)


async def test_concurrent_same_kiz_has_one_winner(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    one = request(transfer_stock, key="one", quantity=1, codes=("K1",))
    two = request(transfer_stock, key="two", quantity=1, codes=("K1",), destination=transfer_stock[2])
    results = await asyncio.gather(
        transfer_service.transfer(one), transfer_service.transfer(two), return_exceptions=True
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, (KizConflictError, asyncpg.PostgresError)) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1


async def test_concurrent_exact_replay_has_one_physical_effect(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    data = request(transfer_stock, quantity=1, codes=("K1",))
    first, second = await asyncio.gather(
        transfer_service.transfer(data), transfer_service.transfer(data)
    )
    assert first == second
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1


async def test_concurrent_same_stock_different_kiz_preserves_invariant(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1", "K2")
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "UPDATE wms.inventory SET quantity=3 WHERE product_id='sku' AND location_id=$1",
            transfer_stock[0]["location_id"],
        )
    one = request(transfer_stock, key="one", quantity=2, codes=("K1",))
    two = request(transfer_stock, key="two", quantity=2, codes=("K2",), destination=transfer_stock[2])
    results = await asyncio.gather(
        transfer_service.transfer(one), transfer_service.transfer(two), return_exceptions=True
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        violations = await conn.fetchval(
            """SELECT count(*) FROM (
                 SELECT k.product_id,k.location_id,count(*) AS identified
                 FROM wms.kiz k WHERE lifecycle_status='active'
                 GROUP BY k.product_id,k.location_id
               ) k LEFT JOIN wms.inventory i ON i.product_id=k.product_id
                 AND i.location_id=k.location_id AND i.status='available'
                 AND i.batch_number IS NULL AND i.container_code IS NULL
               WHERE k.identified > COALESCE(i.quantity,0)"""
        )
        assert violations == 0


async def test_concurrent_opposite_transfers_complete_without_deadlock(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    source, destination, _ = transfer_stock
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','sku',$1,2)""", destination["location_id"],
        )
    await assign(kiz_service, source, "K1")
    await assign(kiz_service, destination, "K2")
    one = request(transfer_stock, key="one", quantity=1, codes=("K1",))
    two = KizTransferRequest.model_validate({
        "source_system": "manual", "external_operation_id": "two", "author": "operator",
        "items": [{"external_line_id": "1", "product_id": "sku",
            "from_location_code": destination["location_code"],
            "to_location_code": source["location_code"], "quantity": 1, "kiz_codes": ["K2"]}],
    })
    await asyncio.wait_for(asyncio.gather(
        transfer_service.transfer(one), transfer_service.transfer(two)
    ), timeout=5)
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='K1'") == destination["location_id"]
        assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='K2'") == source["location_id"]


async def test_concurrent_transfer_and_legacy_aggregate_transfer(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    source, destination, third = transfer_stock
    await assign(kiz_service, source, "K1")
    async with kiz_pool.acquire() as conn:
        await conn.execute("UPDATE wms.inventory SET quantity=2 WHERE product_id='sku' AND location_id=$1", source["location_id"])

    async def legacy():
        async with kiz_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity)
                       VALUES ('transfer','sku',$1,$2,1)""", source["location_id"], third["location_id"],
                )

    await asyncio.gather(
        transfer_service.transfer(request(transfer_stock, quantity=1, codes=("K1",))), legacy()
    )
    async with kiz_pool.acquire() as conn:
        assert await scope(conn, "sku", source["location_id"]) is None
        assert await conn.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='K1'") == destination["location_id"]


async def test_concurrent_transfer_and_assignment_preserve_touch_protocol(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    source = transfer_stock[0]
    async with kiz_pool.acquire() as conn:
        await conn.execute("UPDATE wms.inventory SET quantity=2 WHERE product_id='sku' AND location_id=$1", source["location_id"])
    await asyncio.gather(
        transfer_service.transfer(request(transfer_stock, quantity=1, codes=())),
        assign(kiz_service, source, "NEW"),
    )
    summary = await kiz_service.summary("sku", source["location_code"])
    assert summary["physical_quantity"] == summary["identified_quantity"] == 1


async def test_concurrent_transfer_and_deactivate_has_one_state_change(
    kiz_pool, transfer_stock, transfer_service, kiz_service,
):
    await assign(kiz_service, transfer_stock[0], "K1")
    results = await asyncio.gather(
        transfer_service.transfer(request(transfer_stock, quantity=1, codes=("K1",))),
        kiz_service.terminate("K1", "deactivated", KizTerminalRequest(author="x", reason="test")),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchrow("SELECT lifecycle_status,location_id FROM wms.kiz WHERE kiz_code='K1'")
        assert (state["lifecycle_status"] == "active" and state["location_id"] == transfer_stock[1]["location_id"]) \
            or (state["lifecycle_status"] == "deactivated" and state["location_id"] == transfer_stock[0]["location_id"])


async def test_concurrent_idempotency_conflict_has_one_intent(
    kiz_pool, transfer_stock, transfer_service,
):
    one = request(transfer_stock, key="same", quantity=1, codes=())
    two = request(transfer_stock, key="same", quantity=2, codes=())
    results = await asyncio.gather(
        transfer_service.transfer(one), transfer_service.transfer(two), return_exceptions=True
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(value.__class__.__name__ == "KizOperationIdempotencyConflictError" for value in results) == 1
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'") == 1
