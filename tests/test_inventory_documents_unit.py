"""Pure contracts and disabled-module checks: no database access."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.core.schemas.inventory_documents import (
    POSTGRES_BIGINT_MAX,
    ApplicationCommand,
    Command,
    CountQuantity,
    CreateDocument,
    FoundItem,
    ReturnToCount,
)
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
        (False, True, "INVENTORY_MODULE_DISABLED"),
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


@pytest.mark.parametrize("revision", [0, -1, 2**31, POSTGRES_BIGINT_MAX + 1])
def test_application_revision_matches_postgresql_integer(revision):
    with pytest.raises(ValidationError):
        ApplicationCommand(**command(submitted_revision=revision))


async def test_disabled_apply_rejected_before_pool_access(monkeypatch):
    monkeypatch.setattr(settings, "INVENTORY_MODULE_ENABLED", True)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    add_exception_handlers(app)

    def forbidden_pool():
        pytest.fail("Disabled apply must not initialize a pool")

    app.dependency_overrides[get_db_pool] = forbidden_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/inventory-documents/1/apply", json=command(submitted_revision=2)
        )
    assert response.status_code == 503
    assert response.json()["error_code"] == "INVENTORY_APPLY_DISABLED"


def test_inventory_openapi_has_stage1_and_preparatory_operations():
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    paths = app.openapi()["paths"]
    operations = [
        (path, method)
        for path, methods in paths.items()
        if path.startswith("/api/inventory-documents")
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == 32
    for suffix in ("application-preview", "apply", "application"):
        assert f"/api/inventory-documents/{{document_id}}/{suffix}" in paths
    for suffix in (
        "identity-accounting",
        "identities",
        "identity-counts",
        "identity-counts/{count_id}/scans",
        "identity-counts/{count_id}/complete",
    ):
        assert f"/api/inventory-documents/{{document_id}}/{suffix}" in paths


@pytest.mark.parametrize("value", [POSTGRES_BIGINT_MAX + 1, -(2**63) - 1])
@pytest.mark.parametrize(
    "parameter",
    [
        "document_path",
        "item_path",
        "attempt_path",
        "warehouse_query",
        "offset_query",
        "warehouse_body",
        "selected_location_body",
        "found_location_body",
        "recount_item_body",
        "version_body",
    ],
)
async def test_bigint_range_rejected_before_sql(monkeypatch, parameter, value):
    monkeypatch.setattr(settings, "INVENTORY_MODULE_ENABLED", True)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    add_exception_handlers(app)

    class ForbiddenPool:
        def acquire(self):
            pytest.fail("Out-of-range input must not execute PostgreSQL queries")

    app.dependency_overrides[get_db_pool] = lambda: ForbiddenPool()
    base = "/api/inventory-documents"
    method, url, kwargs = "GET", base, {}
    if parameter == "document_path":
        url = f"{base}/{value}"
    elif parameter == "item_path":
        method, url = "POST", f"{base}/1/items/{value}/attempts"
        kwargs["json"] = command()
    elif parameter == "attempt_path":
        method, url = "PUT", f"{base}/1/items/1/attempts/{value}/progress"
        kwargs["json"] = command(actual_quantity="0")
    elif parameter in ("warehouse_query", "offset_query"):
        key = "warehouse_location_id" if parameter == "warehouse_query" else "offset"
        kwargs["params"] = {key: value}
    elif parameter in ("warehouse_body", "selected_location_body"):
        method = "POST"
        kwargs["json"] = dict(
            idempotency_key=str(uuid4()),
            user_name="counter",
            title="Count",
            mode="partial",
            warehouse_location_id=value if parameter == "warehouse_body" else 1,
            locations=[{"location_id": value if parameter == "selected_location_body" else 1}],
        )
    elif parameter == "found_location_body":
        method, url = "POST", f"{base}/1/items"
        kwargs["json"] = command(product_id="sku", location_id=value)
    elif parameter == "recount_item_body":
        method, url = "POST", f"{base}/1/return-to-count"
        kwargs["json"] = command(reason="Recount", item_ids=[value])
    else:
        method, url = "POST", f"{base}/1/start"
        kwargs["json"] = dict(command(), expected_version=value)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(method, url, **kwargs)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]


def test_bigint_upper_boundary_is_valid_in_body_contracts():
    document = CreateDocument(
        idempotency_key=uuid4(),
        user_name="counter",
        title="Count",
        mode="partial",
        warehouse_location_id=POSTGRES_BIGINT_MAX,
        locations=[{"location_id": POSTGRES_BIGINT_MAX}],
    )
    assert document.warehouse_location_id == POSTGRES_BIGINT_MAX
    assert document.locations[0].location_id == POSTGRES_BIGINT_MAX
    assert Command(**dict(command(), expected_version=POSTGRES_BIGINT_MAX)).expected_version == (
        POSTGRES_BIGINT_MAX
    )
    assert FoundItem(**command(product_id="sku", location_id=POSTGRES_BIGINT_MAX)).location_id == (
        POSTGRES_BIGINT_MAX
    )
    assert ReturnToCount(**command(reason="Recount", item_ids=[POSTGRES_BIGINT_MAX])).item_ids == [
        POSTGRES_BIGINT_MAX
    ]
