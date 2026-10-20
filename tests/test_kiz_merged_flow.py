import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.exceptions import KizConflictError
from app.core.kiz_errors import KizConflictError as RegistryKizConflictError
from app.core.kiz_errors import write_conflict_code
from app.core.services.kiz_import_service import KizImportService
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.kiz_import_repository import KizImportRepository
from app.middleware.error_handler import add_exception_handlers
from app.shared.config import settings


def test_kiz_conflict_contract_is_shared_by_both_development_lines():
    assert KizConflictError is RegistryKizConflictError
    error = KizConflictError("Конфликт", diagnostics={"physical": 3})
    assert write_conflict_code(error) == "KIZ_CONFLICT"
    assert error.diagnostics == {"physical": 3}


@pytest.fixture
async def merged_scope(kiz_pool, monkeypatch):
    async with kiz_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO public.products(id,name) VALUES ('testwild','Merge test')"
        )
        locations = []
        for name in ("MERGE-SOURCE", "MERGE-DESTINATION"):
            row = await connection.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ($1,0) "
                "RETURNING location_id,location_code",
                name,
            )
            locations.append(dict(row))
        await connection.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) "
            "VALUES ('receive','testwild',$1,3)",
            locations[0]["location_id"],
        )
        await connection.execute(
            "INSERT INTO wms.receipt_items(guid,product_id,quantity) "
            "VALUES ('MERGE-RECEIPT','testwild',3)"
        )
        await connection.execute(
            "INSERT INTO public.assembly_task(task_id,vendor_code,date) "
            "SELECT task_id,'testwild',CURRENT_DATE FROM generate_series(1,3) AS task_id"
        )
    monkeypatch.setattr(settings, "KIZ_IMPORT_RECEIPT_LOCATION_CODE", locations[0]["location_code"])
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", locations[0]["location_code"])
    monkeypatch.setattr(settings, "FBS_TASK_PROCESSING_MODE", "task_level")
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    application = FastAPI()
    application.include_router(api_router, prefix="/api")
    application.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(application)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client, locations[0], locations[1]


async def register_codes(pool, client):
    ingestion = await KizImportService(KizImportRepository(pool)).ingest(
        json.dumps(
            {
                "supply": {"order_guid": "MERGE-RECEIPT", "organization": "Вектор"},
                "wild_groups": [{"wild": "testwild", "mark_codes": ["MERGE-K1", "MERGE-K2"]}],
            }
        ).encode(),
        exchange_name="orders",
        routing_key="orders.kiz.imported",
        rabbit_message_id=None,
        correlation_id=None,
        headers={},
    )
    response = await client.post(f"/api/kiz-import/messages/{ingestion.message_id}/process")
    assert response.status_code == 200, response.text
    assert response.json()["new_kiz_count"] == 2
    return ingestion.message_id


def shipment():
    return [
        {
            "author": "tester",
            "supply_id": "MERGE-FBS",
            "product_id": "testwild",
            "warehouse_id": 1,
            "delivery_type": "fbs",
            "account": "Вектор",
            "quantity": 3,
            "assembly_tasks": ["1", "2", "3"],
            "kiz_bindings": [
                {"assembly_task": "1", "kiz_code": "MERGE-K1"},
                {"assembly_task": "2", "kiz_code": "MERGE-K2"},
            ],
        }
    ]


async def activate_and_transfer(client, source, destination):
    response = await client.post(
        "/api/kiz/activate",
        json={
            "kiz_codes": ["MERGE-K1"],
            "location_code": source["location_code"],
            "user_name": "tester",
            "idempotency_key": "merge-activate",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["activated_count"] == 1
    guarded = await client.post(
        "/api/movements",
        json=[
            {
                "movement_type": "transfer",
                "product_id": "testwild",
                "from_location_code": source["location_code"],
                "to_location_code": destination["location_code"],
                "quantity": 3,
            }
        ],
    )
    assert guarded.status_code == 409, guarded.text
    assert guarded.json()["error_code"] == "KIZ_CONFLICT"
    response = await client.post(
        "/api/kiz-operations/transfer",
        json={
            "source_system": "test",
            "external_operation_id": "merge-transfer",
            "author": "tester",
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "testwild",
                    "from_location_code": source["location_code"],
                    "to_location_code": destination["location_code"],
                    "quantity": 1,
                    "kiz_codes": ["MERGE-K1"],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text


async def test_receipt_activation_transfer_multisource_fbs_and_history(kiz_pool, merged_scope):
    client, source, destination = merged_scope
    message_id = await register_codes(kiz_pool, client)
    card = await client.get("/api/kiz/MERGE-K1")
    assert card.status_code == 200, card.text
    assert card.json()["lifecycle_status"] == "registered"
    assert card.json()["organization"] == "Вектор"
    listing = await client.get("/api/kiz", params={"lifecycle_status": "registered"})
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 2
    summary = await client.get(
        "/api/kiz/stock-summary",
        params={
            "product_id": "testwild",
            "location_code": source["location_code"],
        },
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["identified_quantity"] == 0
    assert float(summary.json()["unidentified_quantity"]) == 3
    await activate_and_transfer(client, source, destination)
    events = await client.get("/api/kiz/MERGE-K1/events")
    assert events.status_code == 200, events.text
    assert [event["event_type"] for event in events.json()["items"]] == ["registered", "activated"]
    response = await client.post("/api/fbs-shipments", json=shipment())
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    shipment_id = response.json()["shipment_id"]
    async with kiz_pool.acquire() as connection:
        results = await connection.fetch(
            "SELECT task_id,movement_ref,source_location_id FROM wms.fbs_shipment_task_results "
            "WHERE shipment_id=$1 ORDER BY task_id",
            shipment_id,
        )
        assert len(results) == 3
        assert [row["source_location_id"] for row in results] == [
            destination["location_id"],
            source["location_id"],
            source["location_id"],
        ]
        assert len({row["movement_ref"] for row in results}) == 2
        assert (
            await connection.fetchval("SELECT count(*) FROM public.assembly_task WHERE is_shipped")
            == 3
        )
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 4
        assert await connection.fetchval("SELECT coalesce(sum(quantity),0) FROM wms.inventory") == 0
    history = await client.get("/api/kiz-history", params={"kiz_code": "MERGE-K1"})
    assert history.status_code == 200, history.text
    assert history.json()["current_state"]["lifecycle_status"] == "shipped"
    assert [entry["event_type"] for entry in history.json()["timeline"]] == [
        "registered",
        "activated",
        "transfer",
        "ship",
    ]
    for endpoint in ("/api/kiz-import/integrity", "/api/system/kiz-integrity"):
        integrity = await client.get(endpoint)
        assert integrity.status_code == 200, integrity.text
        if isinstance(integrity.json(), dict):
            assert integrity.json()["is_valid"]
        else:
            assert integrity.json() == []
    replay = await client.post("/api/fbs-shipments", json=shipment())
    assert replay.status_code == 201, replay.text
    assert replay.json()["items"][0]["task_resolution_status"] == "duplicate_only"
    replay_results = await client.get(
        f"/api/fbs-shipments/{replay.json()['shipment_id']}/task-results"
    )
    assert replay_results.status_code == 200, replay_results.text
    async with kiz_pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_task_results "
                "WHERE shipment_id=$1 AND outcome='duplicate_skipped'",
                replay.json()["shipment_id"],
            )
            == 3
        )
    receipt_replay = await client.post(f"/api/kiz-import/messages/{message_id}/process")
    assert receipt_replay.status_code == 200, receipt_replay.text
    assert receipt_replay.json()["new_kiz_count"] == 0
    assert receipt_replay.json()["terminal_existing_count"] == 2
    async with kiz_pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 4
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type='shipped'"
            )
            == 2
        )


async def test_multisource_shortage_rolls_back_kiz_and_physical_effects(kiz_pool, merged_scope):
    client, source, destination = merged_scope
    await register_codes(kiz_pool, client)
    await activate_and_transfer(client, source, destination)
    response = await client.post(
        "/api/movements",
        json=[
            {
                "movement_type": "transfer",
                "product_id": "testwild",
                "from_location_code": source["location_code"],
                "to_location_code": destination["location_code"],
                "quantity": 1,
            }
        ],
    )
    assert response.status_code == 201, response.text
    response = await client.post("/api/fbs-shipments", json=shipment())
    assert response.status_code in {201, 409}, response.text
    assert response.json()["status"] == "processing"
    async with kiz_pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 3
        assert await connection.fetchval("SELECT sum(quantity) FROM wms.inventory") == 3
        assert (
            await connection.fetchval("SELECT count(*) FROM public.assembly_task WHERE is_shipped")
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type='shipped'"
            )
            == 0
        )
        rows = await connection.fetch(
            "SELECT kiz_code,lifecycle_status FROM wms.kiz ORDER BY kiz_code"
        )
        assert [(row["kiz_code"], row["lifecycle_status"]) for row in rows] == [
            ("MERGE-K1", "active"),
            ("MERGE-K2", "registered"),
        ]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.fbs_shipment_task_results "
                "WHERE outcome='pending_retry' AND last_error LIKE 'KIZ_CONFLICT:%'"
            )
            == 3
        )
