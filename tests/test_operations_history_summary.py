from collections import Counter
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_operations_history_service
from app.api.v1.endpoints.operations_history import router
from app.core.exceptions import OperationsHistoryValidationError
from app.core.services.operations_history_service import OperationsHistoryService
from app.infrastructure.database.repositories.operations_history_repository import (
    OperationsHistoryRepository,
)
from app.middleware.error_handler import add_exception_handlers


class SummaryRepository:
    def __init__(self, rows=(), location_exists=True):
        self.rows = rows
        self.location_exists = location_exists
        self.arguments = None

    async def get_summary(self, *args):
        self.arguments = args
        return self.location_exists, self.rows


@pytest.fixture
def summary_client():
    repository = SummaryRepository(
        [
            dict(
                period_start=date(2026, 7, 1),
                source_type="movement",
                operation_type="ship",
                author="FBS-service",
                status=None,
                operations_count=205,
            ),
            dict(
                period_start=date(2026, 7, 3),
                source_type="fbs_shipment",
                operation_type="fbs_shipment",
                author=None,
                status="completed",
                operations_count=1,
            ),
        ]
    )
    application = FastAPI()
    application.include_router(router, prefix="/api")
    add_exception_handlers(application)
    application.dependency_overrides[
        get_operations_history_service
    ] = lambda: OperationsHistoryService(repository)
    return TestClient(application), repository


def test_summary_route_calendar_counts_and_authors(summary_client):
    client, repository = summary_client
    response = client.get(
        "/api/operations-history/summary", params=dict(date_from="2026-07-01", date_to="2026-07-03")
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["group_by"] == "day"
    assert payload["total_operations"] == 206
    assert len(payload["periods"]) == 3
    assert payload["periods"][1] == dict(period_start="2026-07-02", total_operations=0, groups=[])
    assert payload["periods"][0]["groups"][0]["author"] == "FBS-service"
    assert payload["periods"][0]["groups"][0]["operation_name"] == "Отгрузка"
    assert payload["periods"][2]["groups"][0]["author"] is None
    assert "limit" not in payload and "offset" not in payload
    assert repository.arguments == (
        date(2026, 7, 1),
        date(2026, 7, 3),
        None,
        None,
        None,
        None,
        None,
        None,
        "day",
    )


def test_summary_monthly_partial_calendar_and_filter_forwarding(summary_client):
    client, repository = summary_client
    repository.rows = []
    response = client.get(
        "/api/operations-history/summary",
        params=dict(
            date_from="2026-01-15",
            date_to="2026-03-10",
            group_by="month",
            source_type="movement",
            operation_type="ship",
            product_id="sku",
            location_id=7,
            author="operator",
            status="completed",
        ),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["group_by"] == "month"
    assert payload["total_operations"] == 0
    assert [period["period_start"] for period in payload["periods"]] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
    ]
    assert repository.arguments == (
        date(2026, 1, 15),
        date(2026, 3, 10),
        "movement",
        "ship",
        "sku",
        7,
        "operator",
        "completed",
        "month",
    )


@pytest.mark.parametrize(
    "override,code",
    [
        ({"date_from": "2026-07-04"}, 400),
        ({"date_from": "2024-01-01"}, 400),
        ({"source_type": "receipt"}, 400),
        ({"operation_type": "write_off"}, 400),
        ({"group_by": "year"}, 422),
        ({"date_from": "bad"}, 422),
        ({"date_to": None}, 422),
        ({"location_id": "bad"}, 422),
    ],
)
def test_summary_validation(summary_client, override, code):
    client, repository = summary_client
    params = dict(date_from="2026-07-01", date_to="2026-07-03")
    params.update(override)
    response = client.get("/api/operations-history/summary", params=params)
    assert response.status_code == code
    assert repository.arguments is None
    if code == 400:
        assert response.json()["error_code"] == "OPERATIONS_HISTORY_VALIDATION_ERROR"


def test_summary_missing_location(summary_client):
    client, repository = summary_client
    repository.location_exists = False
    response = client.get(
        "/api/operations-history/summary",
        params=dict(date_from="2026-07-01", date_to="2026-07-03", location_id=999),
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "LOCATION_NOT_FOUND"


@pytest.mark.parametrize("group,expected", [("day", 366), ("month", 12)])
async def test_summary_leap_year_calendar(group, expected):
    result = await OperationsHistoryService(SummaryRepository()).get_summary(
        date(2024, 1, 1), date(2024, 12, 31), group_by=group
    )
    assert len(result.periods) == expected
    assert result.total_operations == 0
    with pytest.raises(OperationsHistoryValidationError, match="group_by"):
        await OperationsHistoryService(SummaryRepository()).get_summary(
            date(2026, 1, 1), date(2026, 1, 1), group_by="year"
        )


@pytest.mark.parametrize("group", ["day", "month"])
async def test_summary_calendar_at_maximum_date(group):
    result = await OperationsHistoryService(SummaryRepository()).get_summary(
        date(9999, 12, 31), date(9999, 12, 31), group_by=group
    )
    assert len(result.periods) == 1


async def test_summary_unknown_historical_type_uses_original_name():
    repository = SummaryRepository(
        [
            dict(
                period_start=date(2026, 7, 1),
                source_type="movement",
                operation_type="legacy_operation",
                author=None,
                status=None,
                operations_count=1,
            )
        ]
    )
    result = await OperationsHistoryService(repository).get_summary(
        date(2026, 7, 1), date(2026, 7, 1)
    )
    assert result.periods[0].groups[0].operation_name == "legacy_operation"


async def test_summary_postgres_matches_journal_and_preserves_fbs_authors(kiz_pool):
    """Actual event union: four sources, linked movements, mixed authors and every filter."""
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('a','A'),('b','B'),('bulk','Bulk')"
        )
        location = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('SUMMARY-A',0) RETURNING location_id,location_code"
        )
        other = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('SUMMARY-B',0) RETURNING location_id"
        )
        loc = location["location_id"]
        await conn.execute(
            """
            INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,user_name,created_at)
            VALUES ('receive','a',$1,100,'Alice','2026-01-14 20:59:59+00'),
                   ('receive','a',$1,20,'Alice','2026-01-14 21:00:00+00'),
                   ('receive','a',$1,1,' ','2026-02-10 20:59:59+00'),
                   ('receive','a',$1,10,'Alice','2026-02-10 21:00:00+00')
        """,
            loc,
        )
        await conn.execute(
            """
            INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity,user_name,created_at)
            VALUES ('ship','a',$1,2,'Alice','2026-01-31 20:59:59+00')
        """,
            loc,
        )
        movement_id = await conn.fetchval(
            """
            INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity,user_name,created_at)
            VALUES ('ship','a',$1,2,'Bob','2026-01-31 21:00:00+00') RETURNING movement_id
        """,
            loc,
        )
        await conn.execute(
            """
            INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity,created_at)
            VALUES ('transfer','a',$1,$2,1,'2026-02-03 09:00:00+00')
        """,
            loc,
            other,
        )
        kit = await conn.fetchval(
            """
            INSERT INTO wms.kit_operations(operation_type,kit_product_id,quantity,location_id,location_code,author,status,created_at)
            VALUES ('assembly','b',1,$1,$2,'Alice','completed','2026-01-20 09:00:00+00') RETURNING operation_id
        """,
            loc,
            location["location_code"],
        )
        await conn.execute(
            """
            INSERT INTO wms.kit_operation_items(operation_id,role,product_id,quantity_per_kit,total_quantity)
            VALUES ($1,'kit_result','b',1,1),($1,'component_consumption','a',1,1)
        """,
            kit,
        )
        permission = await conn.fetchval(
            """
            INSERT INTO wms.operation_locations(operation_code,location_id,location_code,scope)
            VALUES ('re_sorting_operations',$1,$2,'direct') RETURNING operation_location_id
        """,
            loc,
            location["location_code"],
        )
        resort = await conn.fetchval(
            """
            INSERT INTO wms.re_sorting_operations(operation_location_id,from_product_id,to_product_id,quantity,location_id,location_code,reason,author,status,created_at)
            VALUES ($1,'a','b',1,$2,$3,'test','Bob','completed','2026-02-04 09:00:00+00') RETURNING operation_id
        """,
            permission,
            loc,
            location["location_code"],
        )
        await conn.execute(
            """
            INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,source_type,source_id,created_at)
            VALUES ('kit_assembly','b',$1,1,'kit_operation',$2,'2026-01-20 09:00:00+00'),
                   ('adjust','b',$1,1,'re_sorting_operation',$3,'2026-02-04 09:00:00+00')
        """,
            loc,
            kit,
            resort,
        )
        shipment = await conn.fetchval(
            """
            INSERT INTO wms.fbs_shipments(raw_message,total_items,status,received_at)
            VALUES ('[]',2,'completed','2026-02-02 09:00:00+00') RETURNING shipment_id
        """
        )
        await conn.execute(
            """
            INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,supply_id,account,assembly_tasks,warehouse_id,delivery_type,status,movement_id)
            VALUES ($1,'a',1,'Alice','s','test','[]',1,'test','success',$2),
                   ($1,'a',1,'Bob','s','test','[]',1,'test','success',$2)
        """,
            shipment,
            movement_id,
        )
        await conn.execute(
            """
            INSERT INTO wms.fbs_shipments(raw_message,total_items,status,received_at)
            VALUES ('[]',0,'validation_failed','2026-02-05 09:00:00+00')
        """
        )
    service = OperationsHistoryService(OperationsHistoryRepository(kiz_pool))
    start, end = date(2026, 1, 15), date(2026, 2, 10)
    cases = [
        ({}, 8),
        ({"author": "Alice"}, 4),
        ({"author": "alice"}, 0),
        ({"source_type": "movement"}, 4),
        ({"source_type": "kit_operation"}, 1),
        ({"source_type": "re_sorting_operation"}, 1),
        ({"source_type": "fbs_shipment"}, 2),
        ({"operation_type": "ship"}, 1),
        ({"product_id": "b"}, 2),
        ({"location_id": loc}, 6),
        ({"status": "completed"}, 3),
        ({"status": "validation_failed"}, 1),
    ]
    for filters, expected in cases:
        journal = await service.get_operations(start, end, limit=1, **filters)
        summary = await service.get_summary(start, end, **filters)
        monthly = await service.get_summary(start, end, group_by="month", **filters)
        assert (
            journal.total == summary.total_operations == monthly.total_operations == expected
        ), filters
        assert sum(p.total_operations for p in summary.periods) == expected
        assert all(
            sum(g.operations_count for g in p.groups) == p.total_operations for p in summary.periods
        )
        assert len(summary.periods) == 27 and len(monthly.periods) == 2
        complete = await service.get_operations(start, end, limit=200, **filters)
        for grouped, group_by in [(summary, "day"), (monthly, "month")]:
            expected_groups = Counter()
            for event in complete.items:
                day = event.created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
                bucket = day if group_by == "day" else day.replace(day=1)
                expected_groups[
                    (bucket, event.source_type, event.operation_type, event.author, event.status)
                ] += 1
            actual_groups = {
                (
                    period.period_start,
                    group.source_type,
                    group.operation_type,
                    group.author,
                    group.status,
                ): group.operations_count
                for period in grouped.periods
                for group in period.groups
            }
            assert actual_groups == expected_groups, (filters, group_by)
    filtered = await service.get_summary(start, end, author="Alice", source_type="fbs_shipment")
    group = next(group for period in filtered.periods for group in period.groups)
    assert group.author is None and group.operations_count == 1
    result = await service.get_summary(start, end)
    assert result.periods[0].total_operations == 1  # Jan 15 Moscow, lower bound included
    assert result.periods[-1].total_operations == 1  # Feb 10 Moscow, upper bound excluded
    assert any(g.author == " " for p in result.periods for g in p.groups)  # No author normalization
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,created_at)
            SELECT 'receive','bulk',$1,1,'2026-02-06 09:00:00+00'::timestamptz
            FROM generate_series(1,205)
        """,
            loc,
        )
    unpaged = await service.get_summary(start, end)
    assert unpaged.total_operations == 213
    bulk = await service.get_summary(start, end, product_id="bulk")
    assert bulk.total_operations == 205
    assert (await service.get_operations(start, end, limit=1)).total == 213
    async with kiz_pool.acquire() as conn:
        common_author_shipment = await conn.fetchval(
            """
            INSERT INTO wms.fbs_shipments(raw_message,total_items,status,received_at)
            VALUES ('[]',2,'failed','2026-02-07 09:00:00+00') RETURNING shipment_id
        """
        )
        await conn.execute(
            """
            INSERT INTO wms.fbs_shipment_items(shipment_id,product_id,quantity,author,supply_id,account,assembly_tasks,warehouse_id,delivery_type,status)
            VALUES ($1,'a',1,'FBS-service','s','test','[]',1,'test','failed'),
                   ($1,'b',1,'FBS-service','s','test','[]',1,'test','failed')
        """,
            common_author_shipment,
        )
    common = await service.get_summary(start, end, source_type="fbs_shipment", author="FBS-service")
    assert common.total_operations == 1
    common_group = next(group for period in common.periods for group in period.groups)
    assert common_group.author == "FBS-service" and common_group.status == "failed"
