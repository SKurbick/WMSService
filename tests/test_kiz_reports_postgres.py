import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def report_scope(kiz_pool):
    application = FastAPI()
    application.include_router(api_router, prefix="/api")
    application.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(application)
    async with kiz_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO public.products(id,name) VALUES ('testwild','Report A'),('testwild2','Report B')"
        )
        locations = []
        for name in ("REPORT-SOURCE", "REPORT-DESTINATION"):
            locations.append(
                dict(
                    await connection.fetchrow(
                        "INSERT INTO wms.locations(name,level) VALUES ($1,0) RETURNING location_id,location_code",
                        name,
                    )
                )
            )
        await connection.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) "
            "VALUES ('receive','testwild',$1,4),('receive','testwild2',$2,2)",
            locations[0]["location_id"],
            locations[1]["location_id"],
        )
        records = [
            ("REPORT-R", "testwild", "СТАРТ", "registered", None, "receipt_import", "REPORT-R1"),
            (
                "REPORT-A",
                "testwild",
                "СТАРТ",
                "active",
                locations[0]["location_id"],
                "receipt_import",
                "REPORT-R1",
            ),
            ("REPORT-S", "testwild", "СТАРТ", "shipped", None, "receipt_import", "REPORT-R1"),
            (
                "REPORT-E",
                "testwild",
                "СТАРТ",
                "error",
                locations[0]["location_id"],
                "receipt_import",
                "REPORT-R1",
            ),
            (
                "REPORT-D",
                "testwild",
                "СТАРТ",
                "deactivated",
                locations[0]["location_id"],
                "receipt_import",
                "REPORT-R1",
            ),
            (
                "REPORT-A2",
                "testwild2",
                "СТАРТ",
                "active",
                locations[1]["location_id"],
                "warehouse_assignment",
                None,
            ),
            ("REPORT-V", "testwild", "ВЕКТОР", "registered", None, "receipt_import", "REPORT-R2"),
            (
                "REPORT-NULL-A",
                "testwild",
                None,
                "active",
                locations[0]["location_id"],
                "warehouse_assignment",
                None,
            ),
            ("REPORT-NULL-R", "testwild", None, "registered", None, "receipt_import", "REPORT-R3"),
        ]
        await connection.executemany(
            "INSERT INTO wms.kiz(kiz_code,product_id,organization,lifecycle_status,location_id,origin_type,origin_reference,closed_at,created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,CASE WHEN $4::varchar IN ('error','deactivated','shipped') THEN now() END,'tester')",
            records,
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        container = await client.post(
            "/api/containers/register",
            json=dict(
                qr_code="REPORT-BOX",
                container_type="box",
                location_code=locations[0]["location_code"],
                contents=[],
            ),
        )
        assert container.status_code == 201, container.text
        container_id = container.json()["container_id"]
        fill = await client.post(
            "/api/container-operations/fill",
            json=dict(
                source_system="report-test",
                external_operation_id="fill",
                author="tester",
                container_id=container_id,
                items=[
                    dict(
                        external_line_id="1",
                        product_id="testwild",
                        quantity="1",
                        kiz_codes=["REPORT-A"],
                    )
                ],
            ),
        )
        assert fill.status_code == 201, fill.text
        yield client, locations, container_id


async def fetch_summary(scope, parameters=None):
    response = await scope[0].get("/api/kiz-reports/summary", params=parameters)
    assert response.status_code == 200, response.text
    return response.json()


async def test_summary_default_all_statuses_and_pagination(report_scope):
    response = await fetch_summary(report_scope)
    assert response["group_by"] == ["organization", "product_id"]
    assert response["total_groups"] == 4
    assert response["totals"] == dict(
        total_count=9,
        registered_count=3,
        active_count=3,
        active_loose_count=2,
        active_container_count=1,
        shipped_count=1,
        error_count=1,
        deactivated_count=1,
    )
    assert response["items"][0]["group"] == {"organization": None, "product_id": "testwild"}
    assert sum(item["counts"]["total_count"] for item in response["items"]) == 9
    page = await fetch_summary(report_scope, {"limit": 1, "offset": 1})
    assert page["total_groups"] == 4
    assert page["totals"] == response["totals"]
    assert page["items"] == response["items"][1:2]
    empty_page = await fetch_summary(report_scope, {"offset": 200})
    assert empty_page["items"] == []
    assert empty_page["totals"] == response["totals"]
    assert empty_page["total_groups"] == 4


@pytest.mark.parametrize("missing,total", [(True, 2), (False, 7)])
async def test_organization_missing_true_and_false(report_scope, missing, total):
    response = await fetch_summary(report_scope, {"organization_missing": str(missing).lower()})
    assert response["totals"]["total_count"] == total
    assert all((item["group"]["organization"] is None) == missing for item in response["items"])


async def test_organization_product_origin_and_status_filters(report_scope):
    response = await fetch_summary(
        report_scope, dict(organization="СТАРТ", group_by="lifecycle_status")
    )
    assert response["total_groups"] == 5
    assert response["totals"]["total_count"] == 6
    response = await fetch_summary(
        report_scope,
        dict(
            organization="СТАРТ",
            product_id="testwild2",
            lifecycle_status="active",
            origin_type="warehouse_assignment",
        ),
    )
    assert response["totals"]["total_count"] == 1
    assert response["totals"]["active_loose_count"] == 1
    response = await fetch_summary(
        report_scope, dict(origin_reference="REPORT-R2", origin_type="receipt_import")
    )
    assert response["totals"]["registered_count"] == 1
    assert response["totals"]["total_count"] == 1
    assert response["items"][0]["group"]["organization"] == "ВЕКТОР"


async def test_location_scope_includes_loose_and_container_current_holder(report_scope):
    response = await fetch_summary(
        report_scope,
        dict(
            location_code=report_scope[1][0]["location_code"],
            lifecycle_status="active",
            group_by="location_code,container_id",
        ),
    )
    assert response["totals"]["active_count"] == 2
    assert response["totals"]["active_loose_count"] == 1
    assert response["totals"]["active_container_count"] == 1
    assert response["items"][0]["group"]["container_id"] is None
    assert response["items"][1]["group"]["container_id"] == report_scope[2]


async def test_container_id_qr_filters_and_grouping(report_scope):
    response = await fetch_summary(
        report_scope,
        dict(
            container_id=report_scope[2],
            container_qr_code="REPORT-BOX",
            group_by="container_id,container_qr_code",
        ),
    )
    assert response["total_groups"] == 1
    assert response["totals"]["active_container_count"] == 1
    assert response["items"][0]["group"] == {
        "container_id": report_scope[2],
        "container_qr_code": "REPORT-BOX",
    }
    response = await fetch_summary(
        report_scope, dict(container_id=report_scope[2], container_qr_code="wrong")
    )
    assert response["total_groups"] == 0


@pytest.mark.parametrize(
    "parameters",
    [
        {"organization": "missing"},
        {"product_id": "missing"},
        {"location_code": "missing"},
        {"container_id": 999999},
        {"origin_reference": "missing"},
        {"organization": "старт"},
        {"organization": "' OR TRUE --"},
    ],
)
async def test_empty_filters_return_zero_counters(report_scope, parameters):
    response = await fetch_summary(report_scope, parameters)
    assert response["total_groups"] == 0
    assert response["items"] == []
    assert set(response["totals"].values()) == {0}


async def test_summary_has_no_identity_stock_movement_or_event_side_effects(kiz_pool, report_scope):
    async def snapshot():
        async with kiz_pool.acquire() as connection:
            return await connection.fetchval(
                "SELECT jsonb_build_object("
                "'kiz',(SELECT jsonb_agg(to_jsonb(k) ORDER BY kiz_id) FROM wms.kiz k),"
                "'inventory',(SELECT jsonb_agg(to_jsonb(i) ORDER BY inventory_id) FROM wms.inventory i),"
                "'movements',(SELECT count(*) FROM wms.movements),"
                "'events',(SELECT count(*) FROM wms.kiz_events),"
                "'operations',(SELECT count(*) FROM wms.kiz_operations))::text"
            )

    before = json.loads(await snapshot())
    await fetch_summary(report_scope)
    await fetch_summary(report_scope, {"group_by": "origin_type", "organization_missing": "true"})
    assert json.loads(await snapshot()) == before


async def test_summary_stays_consistent_when_another_transaction_registers_kiz(
    kiz_pool, report_scope, monkeypatch
):
    from app.core.schemas.kiz_reports import KizSummaryQuery
    from app.core.services.kiz_report_service import KizReportService
    from app.infrastructure.database.repositories.kiz_report_repository import KizReportRepository

    repository = KizReportRepository(kiz_pool)
    original_summary = repository.summarize

    async def summarize_after_concurrent_insert(connection, query):
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz") == 9
        async with kiz_pool.acquire() as writer:
            await writer.execute(
                "INSERT INTO wms.kiz(kiz_code,product_id,organization,lifecycle_status,origin_type,origin_reference,created_by) "
                "VALUES ('REPORT-CONCURRENT','testwild','CONCURRENT','registered','receipt_import','REPORT-CONCURRENT','tester')"
            )
        return await original_summary(connection, query)

    monkeypatch.setattr(repository, "summarize", summarize_after_concurrent_insert)
    response = await KizReportService(repository).summary(KizSummaryQuery())
    assert response.totals.total_count == 9
    assert response.total_groups == 4
    assert sum(item.counts.total_count for item in response.items) == 9
    current = await fetch_summary(report_scope)
    assert current["totals"]["total_count"] == 10
    assert current["total_groups"] == 5
