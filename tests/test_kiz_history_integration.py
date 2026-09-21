"""PostgreSQL acceptance tests for the Stage 3A KIZ history read model."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
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
async def history_stock(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute("INSERT INTO public.products(id,name) VALUES ('sku','SKU')")
        locations = []
        for name in ("HISTORY-A", "HISTORY-B", "HISTORY-C"):
            locations.append(
                dict(
                    await conn.fetchrow(
                        "INSERT INTO wms.locations(name,level) VALUES ($1,0) "
                        "RETURNING location_id,location_code",
                        name,
                    )
                )
            )
        await conn.execute(
            """INSERT INTO wms.movements(
                   movement_type,product_id,to_location_id,quantity
               ) VALUES ('receive','sku',$1,30)""",
            locations[0]["location_id"],
        )
    return locations


@pytest.fixture
def kiz_service(kiz_pool):
    return KizService(KizRepository(kiz_pool))


@pytest.fixture
def transfer_service(kiz_pool):
    return KizTransferService(
        KizTransferRepository(kiz_pool),
        KizOperationIdempotencyService(KizOperationRepository()),
    )


@pytest.fixture
def ship_service(kiz_pool):
    return KizShipService(
        KizShipRepository(kiz_pool),
        KizOperationIdempotencyService(KizOperationRepository()),
    )


@pytest.fixture
async def history_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def assign(service, location, *codes):
    for code in codes:
        await service.assign(
            KizAssignment(
                kiz_code=code,
                product_id="sku",
                location_code=location["location_code"],
                author="tester",
            )
        )


async def transfer(service, source, destination, code, key, *, quantity=1, codes=None):
    selected = list(codes if codes is not None else [code])
    return await service.transfer(
        KizTransferRequest.model_validate(
            {
                "source_system": "history-test",
                "external_operation_id": key,
                "author": "tester",
                "items": [
                    {
                        "external_line_id": "1",
                        "product_id": "sku",
                        "from_location_code": source["location_code"],
                        "to_location_code": destination["location_code"],
                        "quantity": quantity,
                        "kiz_codes": selected,
                    }
                ],
            }
        )
    )


async def ship(service, source, code, key, *, quantity=1):
    return await service.ship(
        KizShipRequest.model_validate(
            {
                "source_system": "history-test",
                "external_operation_id": key,
                "author": "tester",
                "items": [
                    {
                        "external_line_id": "1",
                        "product_id": "sku",
                        "from_location_code": source["location_code"],
                        "quantity": quantity,
                        "kiz_codes": [code],
                    }
                ],
            }
        )
    )


async def read_history(client, code):
    response = await client.get("/api/kiz-history", params={"kiz_code": code})
    assert response.status_code == 200, response.text
    return response.json()


async def test_assignment_only_has_one_lifecycle_entry(
    history_stock, kiz_service, history_client
):
    await assign(kiz_service, history_stock[0], "ONLY-ASSIGNMENT")

    history = await read_history(history_client, "ONLY-ASSIGNMENT")

    assert history["product_id"] == "sku"
    assert history["current_state"]["lifecycle_status"] == "active"
    assert [entry["event_type"] for entry in history["timeline"]] == ["assigned"]
    assert history["timeline"][0]["movement_ref"] is None
    assert history["timeline"][0]["location_code"] == history_stock[0]["location_code"]


async def test_assignment_then_transfer(
    history_stock, kiz_service, transfer_service, history_client
):
    await assign(kiz_service, history_stock[0], "ONE-TRANSFER")
    result = await transfer(
        transfer_service,
        history_stock[0],
        history_stock[1],
        "ONE-TRANSFER",
        "one-transfer",
    )

    history = await read_history(history_client, "ONE-TRANSFER")

    assert [entry["event_type"] for entry in history["timeline"]] == [
        "assigned",
        "transfer",
    ]
    movement = history["timeline"][1]
    assert movement["movement_ref"] == result.items[0].movement_ref
    assert movement["from_location_code"] == history_stock[0]["location_code"]
    assert movement["to_location_code"] == history_stock[1]["location_code"]


async def test_multiple_transfers_are_chronological(
    history_stock, kiz_service, transfer_service, history_client
):
    await assign(kiz_service, history_stock[0], "MULTI-TRANSFER")
    first = await transfer(
        transfer_service,
        history_stock[0],
        history_stock[1],
        "MULTI-TRANSFER",
        "multi-transfer-1",
    )
    second = await transfer(
        transfer_service,
        history_stock[1],
        history_stock[2],
        "MULTI-TRANSFER",
        "multi-transfer-2",
    )

    history = await read_history(history_client, "MULTI-TRANSFER")

    assert [entry["event_type"] for entry in history["timeline"]] == [
        "assigned",
        "transfer",
        "transfer",
    ]
    assert [entry["movement_ref"] for entry in history["timeline"][1:]] == [
        first.items[0].movement_ref,
        second.items[0].movement_ref,
    ]
    assert history["timeline"][2]["to_location_code"] == history_stock[2]["location_code"]


async def test_transfer_then_ship_is_merged_and_shipped_state_is_readable(
    history_stock, kiz_service, transfer_service, ship_service, history_client
):
    await assign(kiz_service, history_stock[0], "TRANSFER-SHIP")
    await transfer(
        transfer_service,
        history_stock[0],
        history_stock[1],
        "TRANSFER-SHIP",
        "before-ship",
    )
    ship_result = await ship(
        ship_service, history_stock[1], "TRANSFER-SHIP", "ship-after-transfer"
    )

    history = await read_history(history_client, "TRANSFER-SHIP")

    assert [entry["event_type"] for entry in history["timeline"]] == [
        "assigned",
        "transfer",
        "ship",
    ]
    assert all(entry["event_type"] != "shipped" for entry in history["timeline"])
    shipment = history["timeline"][2]
    assert shipment["movement_ref"] == ship_result.items[0].movement_ref
    assert shipment["kiz_event_id"] is not None
    assert shipment["from_status"] == "active"
    assert shipment["to_status"] == "shipped"
    assert shipment["from_location_code"] == history_stock[1]["location_code"]
    assert shipment["to_location_code"] is None
    assert history["current_state"]["lifecycle_status"] == "shipped"
    assert history["current_state"]["location_id"] is None
    assert history["current_state"]["location_code"] is None


@pytest.mark.parametrize(
    ("code", "status", "event_type"),
    [
        ("ERROR-KIZ", "error", "marked_as_error"),
        ("DEACTIVATED-KIZ", "deactivated", "deactivated"),
    ],
)
async def test_terminal_lifecycle_event_has_location_author_and_reason(
    history_stock, kiz_service, history_client, code, status, event_type
):
    await assign(kiz_service, history_stock[0], code)
    await kiz_service.terminate(
        code,
        status,
        KizTerminalRequest(author="auditor", reason="history test"),
    )

    history = await read_history(history_client, code)

    assert [entry["event_type"] for entry in history["timeline"]] == [
        "assigned",
        event_type,
    ]
    terminal = history["timeline"][1]
    assert terminal["movement_ref"] is None
    assert terminal["location_code"] == history_stock[0]["location_code"]
    assert terminal["author"] == "auditor"
    assert terminal["reason"] == "history test"


async def test_mixed_movement_quantity_is_shared_physical_quantity(
    history_stock, kiz_service, transfer_service, history_client
):
    await assign(kiz_service, history_stock[0], "MIXED-K1", "MIXED-K2")
    result = await transfer(
        transfer_service,
        history_stock[0],
        history_stock[1],
        "MIXED-K1",
        "mixed-transfer",
        quantity=5,
        codes=["MIXED-K1", "MIXED-K2"],
    )

    first = await read_history(history_client, "MIXED-K1")
    second = await read_history(history_client, "MIXED-K2")
    first_movement = first["timeline"][1]
    second_movement = second["timeline"][1]

    assert first_movement["movement_ref"] == result.items[0].movement_ref
    assert second_movement["movement_ref"] == result.items[0].movement_ref
    assert first_movement["quantity"] == "5.00"
    assert second_movement["quantity"] == "5.00"


async def test_unknown_kiz_returns_project_404(history_client):
    response = await history_client.get(
        "/api/kiz-history", params={"kiz_code": "UNKNOWN-KIZ"}
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "KIZ_NOT_FOUND"
