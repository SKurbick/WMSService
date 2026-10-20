"""Real PostgreSQL acceptance, concurrency and rollback tests for KIZ Phase 5."""

import asyncio
from decimal import Decimal

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.core.kiz_errors import KizConflictError, KizShipConflictError
from app.core.schemas.kiz import KizAssignment, KizTerminalRequest
from app.core.schemas.kiz_operations import KizShipRequest, KizTransferRequest
from app.core.services.kiz_operation_idempotency_service import (
    KizOperationIdempotencyService,
)
from app.core.services.kiz_service import KizService
from app.core.services.kiz_ship_service import KizShipService
from app.core.services.kiz_transfer_service import KizTransferService
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.kiz_operation_repository import (
    KizOperationRepository,
)
from app.infrastructure.database.repositories.kiz_repository import KizRepository
from app.infrastructure.database.repositories.kiz_ship_repository import KizShipRepository
from app.infrastructure.database.repositories.kiz_transfer_repository import (
    KizTransferRepository,
)
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def ship_stock(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('sku','SKU'),('other','Other')"
        )
        source = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('SOURCE',0) "
            "RETURNING location_id,location_code"
        ))
        destination = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('DESTINATION',0) "
            "RETURNING location_id,location_code"
        ))
        third = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('THIRD',0) "
            "RETURNING location_id,location_code"
        ))
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','sku',$1,10),('receive','other',$1,2)""",
            source["location_id"],
        )
    return source, destination, third


@pytest.fixture
def ship_service(kiz_pool):
    return KizShipService(
        KizShipRepository(kiz_pool),
        KizOperationIdempotencyService(KizOperationRepository()),
    )


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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


async def assign(kiz_service, location, *codes, product="sku"):
    for code in codes:
        await kiz_service.assign(KizAssignment(
            kiz_code=code,
            product_id=product,
            location_code=location["location_code"],
            author="tester",
        ))


def request(stock, *, key="ship-1", quantity=2, codes=("K1", "K2"),
            product="sku", source=None):
    source = source or stock[0]
    return KizShipRequest.model_validate({
        "source_system": "manual",
        "external_operation_id": key,
        "author": "operator",
        "items": [{
            "external_line_id": "1",
            "product_id": product,
            "from_location_code": source["location_code"],
            "quantity": quantity,
            "kiz_codes": list(codes),
        }],
    })


def transfer_request(stock, *, key="transfer-1", codes=("K1",)):
    source, destination, _ = stock
    return KizTransferRequest.model_validate({
        "source_system": "manual",
        "external_operation_id": key,
        "author": "operator",
        "items": [{
            "external_line_id": "1",
            "product_id": "sku",
            "from_location_code": source["location_code"],
            "to_location_code": destination["location_code"],
            "quantity": 1,
            "kiz_codes": list(codes),
        }],
    })


async def physical(conn, product, location_id):
    return await conn.fetchval(
        """SELECT quantity FROM wms.inventory
           WHERE product_id=$1 AND location_id=$2 AND status='available'
             AND batch_number IS NULL AND container_code IS NULL""",
        product, location_id,
    )


async def counts(conn):
    return dict(
        movements=await conn.fetchval(
            "SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'"
        ),
        operations=await conn.fetchval("SELECT count(*) FROM wms.kiz_operations"),
        links=await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links"),
        shipped_events=await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_events WHERE event_type='shipped'"
        ),
    )


@pytest.mark.parametrize(
    "quantity,codes,expected_physical,expected_identified,expected_unidentified",
    [
        (2, ("K1", "K2"), 8, 1, 7),
        (5, ("K1", "K2"), 5, 1, 4),
        (3, (), 7, 3, 4),
    ],
)
async def test_identified_mixed_and_unidentified_ship(
    kiz_pool, ship_stock, ship_service, kiz_service, quantity, codes,
    expected_physical, expected_identified, expected_unidentified,
):
    await assign(kiz_service, ship_stock[0], "K1", "K2", "K3")
    result = await ship_service.ship(
        request(ship_stock, quantity=quantity, codes=codes)
    )
    assert result.operation_type == "ship"
    assert result.items[0].quantity == quantity
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) == Decimal(
            expected_physical
        )
        assert await conn.fetchval(
            """SELECT count(*) FROM wms.kiz WHERE product_id='sku'
               AND location_id=$1 AND lifecycle_status='active'""",
            ship_stock[0]["location_id"],
        ) == expected_identified
        assert expected_physical - expected_identified == expected_unidentified
        for code in codes:
            row = await conn.fetchrow(
                """SELECT lifecycle_status,location_id,closed_at
                   FROM wms.kiz WHERE kiz_code=$1""", code
            )
            assert row["lifecycle_status"] == "shipped"
            assert row["location_id"] is None
            assert row["closed_at"] is not None
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.movements WHERE source_type='kiz_operation'"
        ) == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links") == len(codes)
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_events WHERE event_type='shipped'"
        ) == len(codes)
        assert await conn.fetchval(
            "SELECT movement_ref FROM wms.kiz_operation_items"
        ) == result.items[0].movement_ref
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_shipment_authorizations"
        ) == 0


async def test_physical_history_and_existing_reads(
    client, kiz_pool, ship_stock, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    response = await client.post(
        "/api/kiz-operations/ship",
        json=request(ship_stock, quantity=1, codes=("K1",)).model_dump(mode="json"),
    )
    assert response.status_code == 201
    movement_ref = response.json()["items"][0]["movement_ref"]
    card = await client.get("/api/kiz/K1")
    listing = await client.get("/api/kiz", params={"lifecycle_status": "shipped"})
    events = await client.get("/api/kiz/K1/events")
    summary = await client.get(
        "/api/kiz/stock-summary",
        params={"product_id": "sku", "location_code": ship_stock[0]["location_code"]},
    )
    integrity = await client.get("/api/system/kiz-integrity")
    assert card.status_code == listing.status_code == events.status_code == 200
    assert card.json()["lifecycle_status"] == "shipped"
    assert card.json()["location_id"] is None
    assert card.json()["closed_at"] is not None
    assert any(row["kiz_code"] == "K1" for row in listing.json()["items"])
    shipped = [row for row in events.json()["items"] if row["event_type"] == "shipped"]
    assert len(shipped) == 1 and shipped[0]["movement_ref"] == movement_ref
    assert summary.json()["identified_quantity"] == 0
    assert integrity.status_code == 200
    assert integrity.json() == []
    async with kiz_pool.acquire() as conn:
        movement = await conn.fetchrow(
            """SELECT m.movement_type,m.product_id,m.from_location_id,
                      m.to_location_id,m.quantity
               FROM wms.movement_registry r JOIN wms.movements m
                 ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
               WHERE r.movement_ref=$1""", movement_ref
        )
        assert tuple(movement) == (
            "ship", "sku", ship_stock[0]["location_id"], None, Decimal(1)
        )


async def test_multi_item_commits_atomically(kiz_pool, ship_stock, ship_service):
    payload = request(ship_stock, quantity=2, codes=()).model_dump()
    payload["items"].append({
        "external_line_id": "2",
        "product_id": "other",
        "from_location_code": ship_stock[0]["location_code"],
        "quantity": 1,
        "kiz_codes": [],
    })
    result = await ship_service.ship(KizShipRequest.model_validate(payload))
    assert len(result.items) == 2
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) == 8
        assert await physical(conn, "other", ship_stock[0]["location_id"]) == 1
        assert (await counts(conn))["movements"] == 2


async def test_multi_item_failure_rolls_back_all(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    payload = request(ship_stock, quantity=1, codes=("K1",)).model_dump()
    payload["items"].append({
        "external_line_id": "2",
        "product_id": "other",
        "from_location_code": ship_stock[0]["location_code"],
        "quantity": 3,
        "kiz_codes": [],
    })
    with pytest.raises(KizShipConflictError):
        await ship_service.ship(KizShipRequest.model_validate(payload))
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) == 10
        state = await conn.fetchrow(
            "SELECT lifecycle_status,location_id FROM wms.kiz WHERE kiz_code='K1'"
        )
        assert tuple(state) == ("active", ship_stock[0]["location_id"])
        assert (await counts(conn)) == {
            "movements": 0, "operations": 0, "links": 0, "shipped_events": 0
        }


@pytest.mark.parametrize("case", [
    "unknown", "wrong_product", "wrong_location", "error", "deactivated",
    "already_shipped", "physical", "unidentified",
])
async def test_business_conflicts_are_atomic(
    kiz_pool, ship_stock, ship_service, transfer_service, kiz_service, case,
):
    source = ship_stock[0]
    await assign(kiz_service, source, "K1", "K2", "K3")
    data = request(ship_stock, quantity=1, codes=("K1",))
    if case == "unknown":
        data = request(ship_stock, quantity=1, codes=("MISSING",))
    elif case == "wrong_product":
        data = request(ship_stock, quantity=1, codes=("K1",), product="other")
    elif case == "wrong_location":
        await transfer_service.transfer(transfer_request(ship_stock, codes=("K1",)))
    elif case in {"error", "deactivated"}:
        await kiz_service.terminate(
            "K1", case, KizTerminalRequest(author="x", reason="test")
        )
    elif case == "already_shipped":
        await ship_service.ship(
            request(ship_stock, key="first", quantity=1, codes=("K1",))
        )
        data = request(ship_stock, key="second", quantity=1, codes=("K1",))
    elif case == "physical":
        data = request(ship_stock, quantity=11, codes=())
    elif case == "unidentified":
        data = request(ship_stock, quantity=8, codes=())

    async with kiz_pool.acquire() as conn:
        before_physical = await physical(conn, "sku", source["location_id"])
        before = await counts(conn)
    with pytest.raises(KizShipConflictError):
        await ship_service.ship(data)
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", source["location_id"]) == before_physical
        assert await counts(conn) == before


async def test_http_replay_lost_response_and_conflict(
    client, kiz_pool, ship_stock, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1", "K2")
    payload = request(ship_stock).model_dump(mode="json")
    first = await client.post("/api/kiz-operations/ship", json=payload)
    replay = await client.post("/api/kiz-operations/ship", json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    payload["items"][0]["quantity"] = 3
    conflict = await client.post("/api/kiz-operations/ship", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "KIZ_IDEMPOTENCY_CONFLICT"
    async with kiz_pool.acquire() as conn:
        assert await counts(conn) == {
            "movements": 1, "operations": 1, "links": 2, "shipped_events": 2
        }
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) == 8


@pytest.mark.parametrize("checkpoint", [
    "after_operation_creation",
    "after_kiz_lifecycle_transition",
    "after_movement_insert",
    "after_movement_ref",
    "after_movement_attachment",
    "after_kiz_movement_links",
    "after_shipped_events",
    "before_result_payload",
])
async def test_fault_injection_rolls_back_everything(
    kiz_pool, ship_stock, ship_service, kiz_service, monkeypatch, checkpoint,
):
    await assign(kiz_service, ship_stock[0], "K1")
    async with kiz_pool.acquire() as conn:
        before_movements = await conn.fetchval("SELECT count(*) FROM wms.movements")
        before_registry = await conn.fetchval("SELECT count(*) FROM wms.movement_registry")

    async def fail(name):
        if name == checkpoint:
            raise RuntimeError("fault injection")

    monkeypatch.setattr(ship_service, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="fault injection"):
        await ship_service.ship(
            request(ship_stock, quantity=1, codes=("K1",))
        )
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) == 10
        state = await conn.fetchrow(
            """SELECT lifecycle_status,location_id,closed_at
               FROM wms.kiz WHERE kiz_code='K1'"""
        )
        assert tuple(state) == ("active", ship_stock[0]["location_id"], None)
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == before_movements
        assert await conn.fetchval("SELECT count(*) FROM wms.movement_registry") == before_registry
        assert await counts(conn) == {
            "movements": 0, "operations": 0, "links": 0, "shipped_events": 0
        }
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operation_items") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_shipment_authorizations"
        ) == 0


async def test_direct_update_and_incomplete_protocol_are_rejected(
    kiz_pool, ship_stock, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError) as direct:
            await conn.execute(
                """UPDATE wms.kiz SET lifecycle_status='shipped',
                   location_id=NULL,closed_at=now() WHERE kiz_code='K1'"""
            )
        assert direct.value.sqlstate == "P7501"

        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                operation_id = await conn.fetchval(
                    """INSERT INTO wms.kiz_operations(
                         operation_type,source_system,external_operation_id,
                         request_fingerprint,author)
                       VALUES ('ship','manual','incomplete',$1,'x')
                       RETURNING operation_id""",
                    "a" * 64,
                )
                item_id = await conn.fetchval(
                    """INSERT INTO wms.kiz_operation_items(operation_id,external_line_id)
                       VALUES ($1,'1') RETURNING operation_item_id""", operation_id
                )
                kiz_id = await conn.fetchval(
                    "SELECT kiz_id FROM wms.kiz WHERE kiz_code='K1'"
                )
                await conn.execute(
                    "SELECT wms.ship_kiz($1,$2,'sku',$3,1)",
                    item_id, kiz_id, ship_stock[0]["location_id"],
                )
                await conn.execute(
                    """UPDATE wms.kiz_operations SET result_payload=
                       '{"items":[]}'::jsonb WHERE operation_id=$1""", operation_id
                )
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT lifecycle_status,location_id FROM wms.kiz WHERE kiz_code='K1'"
        )
        assert tuple(state) == ("active", ship_stock[0]["location_id"])


async def test_concurrent_ship_same_kiz_has_one_winner(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    results = await asyncio.gather(
        ship_service.ship(request(ship_stock, key="one", quantity=1, codes=("K1",))),
        ship_service.ship(request(ship_stock, key="two", quantity=1, codes=("K1",))),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, (KizConflictError, asyncpg.PostgresError))
               for value in results) == 1
    async with kiz_pool.acquire() as conn:
        assert (await counts(conn))["movements"] == 1


async def test_concurrent_ship_and_transfer_same_kiz_have_one_effect(
    kiz_pool, ship_stock, ship_service, transfer_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    results = await asyncio.gather(
        ship_service.ship(request(ship_stock, key="ship", quantity=1, codes=("K1",))),
        transfer_service.transfer(transfer_request(ship_stock, codes=("K1",))),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        assert (await counts(conn))["movements"] == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_movement_links WHERE kiz_id="
            "(SELECT kiz_id FROM wms.kiz WHERE kiz_code='K1')"
        ) == 1


async def test_concurrent_ship_and_deactivate_have_one_transition(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    results = await asyncio.gather(
        ship_service.ship(request(ship_stock, quantity=1, codes=("K1",))),
        kiz_service.terminate(
            "K1", "deactivated", KizTerminalRequest(author="x", reason="test")
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT lifecycle_status FROM wms.kiz WHERE kiz_code='K1'"
        )
        assert state in {"shipped", "deactivated"}


async def test_concurrent_ship_and_assignment_preserve_touch_protocol(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """UPDATE wms.inventory SET quantity=2
               WHERE product_id='sku' AND location_id=$1""",
            ship_stock[0]["location_id"],
        )
    await asyncio.gather(
        ship_service.ship(request(ship_stock, quantity=1, codes=())),
        assign(kiz_service, ship_stock[0], "NEW"),
    )
    summary = await kiz_service.summary("sku", ship_stock[0]["location_code"])
    assert summary["physical_quantity"] == summary["identified_quantity"] == 1


async def test_concurrent_ship_and_legacy_outgoing_use_distinct_stock(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """UPDATE wms.inventory SET quantity=2
               WHERE product_id='sku' AND location_id=$1""",
            ship_stock[0]["location_id"],
        )

    async def legacy():
        async with kiz_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO wms.movements(
                         movement_type,product_id,from_location_id,quantity)
                       VALUES ('ship','sku',$1,1)""",
                    ship_stock[0]["location_id"],
                )

    await asyncio.gather(
        ship_service.ship(request(ship_stock, quantity=1, codes=("K1",))),
        legacy(),
    )
    async with kiz_pool.acquire() as conn:
        assert await physical(conn, "sku", ship_stock[0]["location_id"]) is None
        assert await conn.fetchval(
            "SELECT lifecycle_status FROM wms.kiz WHERE kiz_code='K1'"
        ) == "shipped"


async def test_concurrent_shared_stock_different_kiz_preserves_invariant(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1", "K2")
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """UPDATE wms.inventory SET quantity=3
               WHERE product_id='sku' AND location_id=$1""",
            ship_stock[0]["location_id"],
        )
    results = await asyncio.gather(
        ship_service.ship(request(ship_stock, key="one", quantity=2, codes=("K1",))),
        ship_service.ship(request(ship_stock, key="two", quantity=2, codes=("K2",))),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with kiz_pool.acquire() as conn:
        physical_value = await physical(conn, "sku", ship_stock[0]["location_id"])
        identified = await conn.fetchval(
            """SELECT count(*) FROM wms.kiz WHERE product_id='sku'
               AND location_id=$1 AND lifecycle_status='active'""",
            ship_stock[0]["location_id"],
        )
        assert identified <= (physical_value or 0)


async def test_concurrent_exact_replay_has_one_physical_effect(
    kiz_pool, ship_stock, ship_service, kiz_service,
):
    await assign(kiz_service, ship_stock[0], "K1")
    data = request(ship_stock, quantity=1, codes=("K1",))
    first, replay = await asyncio.gather(
        ship_service.ship(data), ship_service.ship(data)
    )
    assert first == replay
    async with kiz_pool.acquire() as conn:
        assert await counts(conn) == {
            "movements": 1, "operations": 1, "links": 1, "shipped_events": 1
        }


async def test_concurrent_conflicting_replay_has_one_intent(
    kiz_pool, ship_stock, ship_service,
):
    results = await asyncio.gather(
        ship_service.ship(request(ship_stock, key="same", quantity=1, codes=())),
        ship_service.ship(request(ship_stock, key="same", quantity=2, codes=())),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(
        value.__class__.__name__ == "KizOperationIdempotencyConflictError"
        for value in results
    ) == 1
    async with kiz_pool.acquire() as conn:
        assert (await counts(conn))["operations"] == 1
        assert (await counts(conn))["movements"] == 1
