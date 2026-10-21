import json
from pathlib import Path

import asyncpg

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


async def register_codes(pool, client, codes=None):
    ingestion = await KizImportService(KizImportRepository(pool)).ingest(
        json.dumps(
            {
                "supply": {"order_guid": "MERGE-RECEIPT", "organization": "Вектор"},
                "wild_groups": [
                    {
                        "wild": "testwild",
                        "mark_codes": codes if codes is not None else ["MERGE-K1", "MERGE-K2"],
                    }
                ],
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


def prefix_migration(name):
    return (Path(__file__).resolve().parents[1] / "scripts/migrations" / name).read_text()


async def test_literal_prefix_import_activation_fbs_and_replay(kiz_pool, merged_scope):
    client, source, _ = merged_scope
    codes = ["01234567890123456789012A", "01234567890123456789012B"]
    message_id = await register_codes(kiz_pool, client, [code + "I" * 72 for code in codes])
    card = await client.get("/api/kiz/" + codes[0] + "different-tail")
    assert card.status_code == 200, card.text
    assert card.json()["kiz_code"] == codes[0]
    request = dict(
        kiz_codes=[codes[0] + "A" * 8],
        location_code=source["location_code"],
        user_name="tester",
        idempotency_key="prefix-activation",
    )
    activation = await client.post("/api/kiz/activate", json=request)
    assert activation.status_code == 200, activation.text
    request["kiz_codes"] = [codes[0] + "B" * 70]
    replay = await client.post("/api/kiz/activate", json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json() == activation.json()
    payload = shipment()
    for binding, code in zip(payload[0]["kiz_bindings"], codes):
        binding["kiz_code"] = code + "F" * 70
    shipment_response = await client.post("/api/fbs-shipments", json=payload)
    assert shipment_response.status_code == 201, shipment_response.text
    assert shipment_response.json()["status"] == "completed"
    for binding, code in zip(payload[0]["kiz_bindings"], codes):
        binding["kiz_code"] = code + "replay-tail"
    replay = await client.post("/api/fbs-shipments", json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["items"][0]["task_resolution_status"] == "duplicate_only"
    history = await client.get("/api/kiz-history", params={"kiz_code": codes[0] + "suffix"})
    assert history.status_code == 200, history.text
    assert history.json()["kiz_code"] == codes[0]
    receipt_replay = await client.post(f"/api/kiz-import/messages/{message_id}/process")
    assert receipt_replay.status_code == 200, receipt_replay.text
    assert receipt_replay.json()["new_kiz_count"] == 0
    async with kiz_pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 2
        assert await connection.fetchval("SELECT count(*) FROM wms.fbs_kiz_bindings") == 2
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz WHERE lifecycle_status='shipped'"
            )
            == 2
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type='activated'"
            )
            == 1
        )
        assert await connection.fetchval("SELECT max(char_length(kiz_code)) FROM wms.kiz") == 24


@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("kiz_legacy_schema", [True], indirect=True)
async def test_prefix_migration_existing_codes_and_collision_rollback(
    kiz_pool, merged_scope, collision
):
    client, source, _ = merged_scope
    code = "P" * 24 + "original-long-code"
    async with kiz_pool.acquire() as connection:
        kiz_id = await connection.fetchval(
            "INSERT INTO wms.kiz(kiz_code,product_id,location_id,created_by) "
            "VALUES ($1,'testwild',$2,'tester') RETURNING kiz_id",
            code,
            source["location_id"],
        )
        await connection.execute(
            "INSERT INTO wms.fbs_kiz_bindings(task_id,kiz_code,kiz_id,product_id,state) "
            "VALUES (1,$1,$2,'testwild','resolved')",
            code,
            kiz_id,
        )
        if collision:
            await connection.execute(
                "INSERT INTO wms.kiz(kiz_code,product_id,location_id,created_by) "
                "VALUES ($1,'testwild',$2,'tester')",
                "P" * 24 + "other",
                source["location_id"],
            )
            with pytest.raises(asyncpg.RaiseError, match="KIZ_PREFIX_COLLISION"):
                await connection.execute(prefix_migration("20261021_kiz_code_prefix_preflight.sql"))
            await connection.execute("ROLLBACK")
            with pytest.raises(asyncpg.RaiseError, match="KIZ_PREFIX_COLLISION"):
                await connection.execute(prefix_migration("20261021_normalize_kiz_code_prefix.sql"))
            await connection.execute("ROLLBACK")
            assert (
                await connection.fetchval("SELECT kiz_code FROM wms.kiz WHERE kiz_id=$1", kiz_id)
                == code
            )
        else:
            await connection.execute(prefix_migration("20261021_kiz_code_prefix_preflight.sql"))
            await connection.execute(prefix_migration("20261021_normalize_kiz_code_prefix.sql"))
            assert (
                await connection.fetchval("SELECT kiz_code FROM wms.kiz WHERE kiz_id=$1", kiz_id)
                == code[:24]
            )
            assert (
                await connection.fetchval(
                    "SELECT kiz_code FROM wms.fbs_kiz_bindings WHERE task_id=1"
                )
                == code[:24]
            )
            with pytest.raises(asyncpg.PostgresError):
                await connection.execute(
                    "UPDATE wms.kiz SET kiz_code='forbidden' WHERE kiz_id=$1", kiz_id
                )
            with pytest.raises(asyncpg.PostgresError):
                await connection.execute(
                    "UPDATE wms.fbs_kiz_bindings SET kiz_code='forbidden' WHERE task_id=1"
                )
    if not collision:
        literal_code = " \t\n\x1d "
        assigned = await client.post(
            "/api/kiz/assign",
            json=dict(
                kiz_code=literal_code,
                product_id="testwild",
                location_code=source["location_code"],
                author="tester",
            ),
        )
        assert assigned.status_code == 201, assigned.text
        assert assigned.json()["kiz"]["kiz_code"] == literal_code


@pytest.mark.parametrize("kiz_legacy_schema", [True], indirect=True)
async def test_prefix_migration_rejects_binding_collisions(kiz_pool, merged_scope):
    async with kiz_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO wms.fbs_kiz_bindings(task_id,kiz_code,product_id,state) "
            "VALUES (1,$1,'testwild','pending_resolution'),(2,$2,'testwild','pending_resolution')",
            "X" * 24 + "first",
            "X" * 24 + "second",
        )
        with pytest.raises(asyncpg.RaiseError, match="KIZ_BINDING_PREFIX_COLLISION"):
            await connection.execute(prefix_migration("20261021_normalize_kiz_code_prefix.sql"))
        await connection.execute("ROLLBACK")
        assert (
            await connection.fetchval("SELECT min(char_length(kiz_code)) FROM wms.fbs_kiz_bindings")
            > 24
        )


@pytest.mark.parametrize("kiz_legacy_schema", [True], indirect=True)
async def test_real_legacy_activation_replay_after_prefix_migration(kiz_pool, merged_scope):
    from app.core.schemas.kiz_activation import KizActivationRequest
    from app.core.services.kiz_activation_service import KizActivationService
    from app.infrastructure.database.repositories.kiz_activation_repository import (
        KizActivationRepository,
    )

    client, source, _ = merged_scope
    old_code = "Z" * 24 + "legacy-full-code"
    async with kiz_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO wms.kiz(kiz_code,product_id,lifecycle_status,origin_type,origin_reference,created_by) "
            "VALUES ($1,'testwild','registered','receipt_import','MERGE-RECEIPT','tester')",
            old_code,
        )
    request = KizActivationRequest.model_construct(
        kiz_codes=[old_code],
        location_code=source["location_code"],
        user_name="tester",
        idempotency_key="legacy-full-code",
    )
    original = await KizActivationService(KizActivationRepository(kiz_pool)).activate(request)
    async with kiz_pool.acquire() as connection:
        await connection.execute(prefix_migration("20261021_normalize_kiz_code_prefix.sql"))
    replay = await client.post(
        "/api/kiz/activate",
        json=dict(
            kiz_codes=["Z" * 24 + "new-tail"],
            location_code=source["location_code"],
            user_name="tester",
            idempotency_key="legacy-full-code",
        ),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == original["operation_id"]
    assert replay.json()["items"][0]["kiz_code"] == "Z" * 24
    async with kiz_pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type='activated'"
            )
            == 1
        )
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 1


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
