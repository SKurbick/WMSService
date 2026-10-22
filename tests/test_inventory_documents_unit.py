"""Pure contracts and disabled-module checks: no database access."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.core.schemas.inventory_documents import CountQuantity, CreateDocument
from app.core.services.inventory_document_service import expected_and_conflicts
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers
from app.shared.config import settings


def command(**changes):
    return dict(idempotency_key=str(uuid4()), expected_version=1, user_name="counter", **changes)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "0.001", "10000000000", 0.25, True])
def test_count_quantity_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        CountQuantity(**command(actual_quantity=value))


def test_zero_and_fraction_are_preserved():
    assert CountQuantity(**command(actual_quantity="0")).actual_quantity == 0
    assert CountQuantity(**command(actual_quantity="0.25")).actual_quantity == Decimal("0.25")


@pytest.mark.parametrize(
    "mode,locations,products",
    [
        ("full", [{"location_id": 1}], []),
        ("full", [], ["sku"]),
        ("partial", [], []),
    ],
)
def test_scope_contract(mode, locations, products):
    with pytest.raises(ValidationError):
        CreateDocument(
            idempotency_key=uuid4(),
            user_name="tester",
            title="Count",
            mode=mode,
            warehouse_location_id=1,
            locations=locations,
            product_ids=products,
        )


def test_registered_kiz_is_not_subtracted_and_active_is_excluded():
    pair = dict(
        stocks=[dict(physical_quantity="3", batch_number=None, location_active=True)],
        kiz=[
            dict(lifecycle_status="active"),
            dict(lifecycle_status="active"),
            dict(lifecycle_status="registered"),
        ],
    )
    expected, conflicts = expected_and_conflicts(pair)
    assert expected == 1
    assert "registered_kiz_without_holder" in conflicts
    assert "active_kiz_physical_separation_required" in conflicts


@pytest.mark.parametrize(
    "module,apply,code",
    [
        (False, False, "INVENTORY_MODULE_DISABLED"),
        (True, True, "INVENTORY_APPLY_NOT_IMPLEMENTED"),
    ],
)
async def test_flags_fail_closed_before_pool_access(monkeypatch, module, apply, code):
    monkeypatch.setattr(settings, "INVENTORY_MODULE_ENABLED", module)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", apply)
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    add_exception_handlers(app)

    def forbidden_pool():
        pytest.fail("Disabled inventory must not initialize a pool")

    app.dependency_overrides[get_db_pool] = forbidden_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/inventory-documents")
    assert response.status_code == 503
    assert response.json()["error_code"] == code
