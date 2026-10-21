from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.v1.dependencies import get_kiz_report_service
from app.api.v1.endpoints.kiz_reports import router
from app.api.v1.endpoints.kiz_reports import SUMMARY_EXAMPLE
from app.core.schemas.kiz_reports import (
    KizSummaryCounts,
    KizSummaryQuery,
    KizSummaryResponse,
)
from app.core.services.kiz_report_service import KizReportService
from app.infrastructure.database.queries.kiz_reports import grouped_queries


def counters(**updates):
    result = {field: 0 for field in KizSummaryCounts.model_fields}
    return result | updates


@pytest.mark.parametrize(
    "dimensions",
    [
        "organization,product_id",
        ["organization", "product_id"],
        ["organization, product_id"],
    ],
)
def test_group_by_accepts_csv_and_repeated_values(dimensions):
    assert KizSummaryQuery(group_by=dimensions).group_by == ["organization", "product_id"]


@pytest.mark.parametrize(
    "parameters",
    [
        {"group_by": []},
        {"group_by": ""},
        {"group_by": "organization,"},
        {"group_by": "organization,organization"},
        {"group_by": "unknown"},
        {"group_by": "organization); DROP TABLE wms.kiz; --"},
        {"organization": "СТАРТ", "organization_missing": True},
        {"organization": ""},
        {"organization": "bad\x00text"},
        {"product_id": ""},
        {"location_code": ""},
        {"container_qr_code": ""},
        {"origin_reference": ""},
        {"container_id": 0},
        {"container_id": -1},
        {"origin_type": "unknown"},
        {"lifecycle_status": "unregistered"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"unknown_filter": "value"},
    ],
)
def test_invalid_query_is_rejected(parameters):
    with pytest.raises(ValidationError):
        KizSummaryQuery(**parameters)


def test_organization_missing_is_tristate_and_false_can_accompany_exact_organization():
    assert KizSummaryQuery().organization_missing is None
    assert KizSummaryQuery(organization_missing=True).organization_missing is True
    assert KizSummaryQuery(organization="СТАРТ", organization_missing=False).organization == "СТАРТ"


def test_repository_group_names_are_whitelisted():
    with pytest.raises(KeyError):
        grouped_queries(["organization); DROP TABLE wms.kiz; --"])


async def test_summary_uses_one_readonly_repeatable_read_transaction():
    state = {"in_transaction": False}

    class Connection:
        @asynccontextmanager
        async def transaction(self, **options):
            assert options == {"isolation": "repeatable_read", "readonly": True}
            state["in_transaction"] = True
            yield
            state["in_transaction"] = False

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    class Repository:
        pool = Pool()

        async def summarize(self, connection, query):
            assert state["in_transaction"]
            return counters(), 0, []

    response = await KizReportService(Repository()).summary(KizSummaryQuery())
    assert response.total_groups == 0
    assert response.totals.total_count == 0
    assert not state["in_transaction"]


@pytest.fixture
def report_app():
    application = FastAPI()
    application.include_router(router, prefix="/api")
    return application


@pytest.mark.parametrize(
    "parameters",
    [
        {"organization": "СТАРТ", "organization_missing": "true"},
        {"group_by": "organization,organization"},
        {"group_by": "unknown"},
        {"limit": "201"},
        {"offset": "-1"},
        {"container_id": "0"},
        {"organization_missing": "not-a-bool"},
        {"unknown_filter": "value"},
    ],
)
async def test_http_invalid_query_is_422_before_summary(report_app, parameters):
    service = AsyncMock()
    report_app.dependency_overrides[get_kiz_report_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=report_app), base_url="http://test"
    ) as client:
        response = await client.get("/api/kiz-reports/summary", params=parameters)
    assert response.status_code == 422, response.text
    assert "detail" in response.json()
    service.summary.assert_not_awaited()


@pytest.mark.parametrize(
    "parameters",
    [
        None,
        {"group_by": "organization,product_id"},
        [("group_by", "organization"), ("group_by", "product_id")],
    ],
)
async def test_http_defaults_and_group_formats(report_app, parameters):
    service = AsyncMock()
    service.summary.return_value = KizSummaryResponse(
        group_by=["organization", "product_id"],
        filters={},
        total_groups=0,
        limit=50,
        offset=0,
        totals=counters(),
        items=[],
    )
    report_app.dependency_overrides[get_kiz_report_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=report_app), base_url="http://test"
    ) as client:
        response = await client.get("/api/kiz-reports/summary", params=parameters)
    assert response.status_code == 200, response.text
    query = service.summary.await_args.args[0]
    assert query.group_by == ["organization", "product_id"]
    assert query.organization_missing is None


def test_openapi_has_russian_query_and_complete_counter_schemas(report_app):
    schema = report_app.openapi()
    operation = schema["paths"]["/api/kiz-reports/summary"]["get"]
    assert "Сводка" in operation["summary"] or "сводку" in operation["summary"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert "organization_missing" in parameters
    assert "group_by" in parameters
    for parameter in parameters.values():
        assert parameter.get("description")
        assert not parameter.get("required", False)
    fields = schema["components"]["schemas"]["KizSummaryCounts"]["properties"]
    assert set(fields) == set(counters())
    assert all(field.get("description") for field in fields.values())
    assert set(operation["responses"]) == {"200", "422", "500"}
    KizSummaryResponse.model_validate(SUMMARY_EXAMPLE)
    documented = operation["responses"]["200"]["content"]["application/json"]["example"]
    assert KizSummaryResponse.model_validate(documented) == KizSummaryResponse.model_validate(
        SUMMARY_EXAMPLE
    )


async def test_backend_failure_returns_controlled_500_without_partial_report(report_app):
    from app.middleware.error_handler import add_exception_handlers

    add_exception_handlers(report_app)
    service = AsyncMock()
    service.summary.side_effect = RuntimeError("private database failure")
    report_app.dependency_overrides[get_kiz_report_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=report_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/kiz-reports/summary")
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert "items" not in response.json()
    assert "private database failure" not in response.text
