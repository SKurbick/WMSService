from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import fbs_shipments as endpoint
from app.core.enums import FbsShipmentSource


class FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class FakeHttpShipmentRepository:
    def __init__(self):
        self.created = []
        self.validation_failures = []

    async def create_shipment(self, conn, **kwargs):
        self.created.append(kwargs)
        return 901

    async def mark_validation_failed(self, conn, shipment_id, error_message):
        self.validation_failures.append((shipment_id, error_message))

    async def get_shipment_by_id(self, conn, shipment_id):
        now = datetime.now(timezone.utc)
        return {
            "shipment_id": shipment_id,
            "received_at": now,
            "raw_message": self.created[0]["raw_message"],
            "total_items": self.created[0]["total_items"],
            "status": "completed",
            "source": "http_api",
            "error_message": None,
            "completed_at": now,
        }

    async def get_items_by_shipment_id(self, conn, shipment_id):
        now = datetime.now(timezone.utc)
        return [
            {
                "item_id": 902,
                "product_id": "SKU-1",
                "quantity": 1,
                "author": "http-client",
                "supply_id": "SUP-1",
                "account": "account",
                "assembly_tasks": ["10"],
                "status": "success",
                "error_message": None,
                "retry_count": 0,
                "movement_id": 903,
                "created_at": now,
                "updated_at": now,
            }
        ]


def valid_payload():
    return [
        {
            "author": "http-client",
            "supply_id": "SUP-1",
            "product_id": "SKU-1",
            "warehouse_id": 1,
            "delivery_type": "fbs",
            "account": "account",
            "quantity": 1,
            "assembly_tasks": ["10"],
        }
    ]


def test_http_create_route_is_registered():
    methods_by_path = {route.path: route.methods for route in endpoint.router.routes}
    assert "POST" in methods_by_path[""]


def test_http_create_openapi_uses_existing_item_schema():
    route = next(
        route
        for route in endpoint.router.routes
        if route.path == "" and "POST" in route.methods
    )
    schema = route.openapi_extra["requestBody"]["content"]["application/json"]["schema"]

    assert schema["type"] == "array"
    assert schema["minItems"] == 1
    assert schema["items"] == endpoint.WriteOffAccordingToFBS.model_json_schema()
    request_example = route.openapi_extra["requestBody"]["content"]["application/json"][
        "example"
    ]
    assert request_example[0]["product_id"] == "testwild"

    documented_responses = route.responses
    assert set(documented_responses) == {201, 422, 500}
    success_examples = documented_responses[201]["content"]["application/json"]["examples"]
    assert set(success_examples) == {
        "completed",
        "pending_retry",
        "partially_completed",
        "failed",
    }
    assert (
        success_examples["completed"]["value"]["items"][0]["product_id"]
        == "testwild"
    )
    validation_examples = documented_responses[422]["content"]["application/json"]["examples"]
    assert set(validation_examples) == {"domain_validation_failed", "invalid_json"}


@pytest.mark.asyncio
async def test_http_create_uses_common_pipeline_and_http_source(monkeypatch):
    repo = FakeHttpShipmentRepository()
    calls = []

    async def fake_handle(items, pool, **kwargs):
        calls.append((items, pool, kwargs))
        return kwargs["shipment_id"]

    monkeypatch.setattr(endpoint, "FbsShipmentRepository", lambda: repo)
    monkeypatch.setattr(endpoint, "handle_write_off_fbs", fake_handle)

    pool = FakePool()
    result = await endpoint.create_shipment_via_http(raw=valid_payload(), pool=pool)

    assert repo.created[0]["source"] == "http_api"
    assert repo.created[0]["total_items"] == 1
    assert calls[0][0][0].product_id == "SKU-1"
    assert calls[0][1] is pool
    assert calls[0][2]["shipment_id"] == 901
    assert calls[0][2]["source"] is FbsShipmentSource.HTTP_API
    assert result.shipment_id == 901
    assert result.source is FbsShipmentSource.HTTP_API


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"not": "an array"},
        [
            {
                "author": "http-client",
                "supply_id": "SUP-1",
                "product_id": "SKU-1",
                "warehouse_id": 1,
                "delivery_type": "fbs",
                "account": "account",
                "quantity": 2,
                "assembly_tasks": ["10"],
            }
        ],
    ],
)
async def test_http_create_saves_validation_failure_with_shipment_id(monkeypatch, payload):
    repo = FakeHttpShipmentRepository()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("business handler must not run for invalid payload")

    monkeypatch.setattr(endpoint, "FbsShipmentRepository", lambda: repo)
    monkeypatch.setattr(endpoint, "handle_write_off_fbs", fail_if_called)

    with pytest.raises(HTTPException) as caught:
        await endpoint.create_shipment_via_http(raw=payload, pool=FakePool())

    assert caught.value.status_code == 422
    assert caught.value.detail["shipment_id"] == 901
    assert repo.created[0]["raw_message"] == payload
    assert repo.created[0]["source"] == "http_api"
    assert repo.validation_failures[0][0] == 901
