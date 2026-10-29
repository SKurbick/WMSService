from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_inventory_history_service
from app.api.v1.endpoints.inventory_history import router
from app.core.exceptions import InventoryHistoryValidationError
from app.core.services.inventory_history_service import InventoryHistoryService
from app.infrastructure.database.repositories.inventory_history_repository import (
    InventoryHistoryRepository,
)
from app.middleware.error_handler import add_exception_handlers


class TurnoverRepository:
    def __init__(self, rows=()):
        self.rows = rows
        self.call = None

    async def get_daily_turnover(self, *args):
        self.call = args
        return self.rows


@pytest.fixture
def turnover_client():
    repository = TurnoverRepository(
        [
            dict(
                product_id="sku",
                product_name="Товар",
                period_date=date(2026, 7, 1),
                received_quantity=Decimal("100.5"),
                shipped_quantity=Decimal("20"),
            ),
            dict(
                product_id="sku",
                product_name="Товар",
                period_date=date(2026, 7, 2),
                received_quantity=Decimal("0"),
                shipped_quantity=Decimal("0"),
            ),
            dict(
                product_id="sku",
                product_name="Товар",
                period_date=date(2026, 7, 3),
                received_quantity=Decimal("1.25"),
                shipped_quantity=Decimal("2.5"),
            ),
        ],
    )
    application = FastAPI()
    application.include_router(router, prefix="/api")
    add_exception_handlers(application)
    application.dependency_overrides[
        get_inventory_history_service
    ] = lambda: InventoryHistoryService(repository)
    return TestClient(application), repository


def test_turnover_http_totals_empty_days_and_product_filter(turnover_client):
    client, repository = turnover_client
    response = client.get(
        "/api/inventory-history/daily-turnover",
        params=dict(date_from="2026-07-01", date_to="2026-07-03", product_id="sku"),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["timezone"] == "Europe/Moscow"
    assert payload["total_products"] == 1
    assert "limit" not in payload and "offset" not in payload
    item = payload["items"][0]
    assert item["totals"] == {"received_quantity": 101.75, "shipped_quantity": 22.5}
    assert len(item["days"]) == 3
    assert item["days"][1] == {"date": "2026-07-02", "received_quantity": 0, "shipped_quantity": 0}
    assert repository.call == (date(2026, 7, 1), date(2026, 7, 3), "sku", "day")


@pytest.mark.parametrize("extra", [{"date_from": "bad"}, {"date_to": None}, {"group_by": "year"}])
def test_turnover_query_validation(turnover_client, extra):
    client, repository = turnover_client
    params = dict(date_from="2026-07-01", date_to="2026-07-03")
    params.update(extra)
    assert client.get("/api/inventory-history/daily-turnover", params=params).status_code == 422
    assert repository.call is None


@pytest.mark.parametrize("start,end", [("2026-07-03", "2026-07-01"), ("2025-01-01", "2026-01-02")])
def test_turnover_period_errors_are_400(turnover_client, start, end):
    client, repository = turnover_client
    response = client.get(
        "/api/inventory-history/daily-turnover", params=dict(date_from=start, date_to=end)
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "INVENTORY_HISTORY_VALIDATION_ERROR"
    assert repository.call is None


async def test_turnover_empty_result_and_maximum_period():
    repository = TurnoverRepository()
    result = await InventoryHistoryService(repository).get_daily_turnover(
        date(2024, 1, 1), date(2024, 12, 31)
    )
    assert result.items == []
    assert result.total_products == 0
    with pytest.raises(InventoryHistoryValidationError):
        await InventoryHistoryService(repository).get_daily_turnover(
            date(2024, 1, 1), date(2025, 1, 1)
        )


async def test_turnover_postgres_aggregation_and_product_filter(kiz_pool):
    """Opt-in: real SQL on an isolated local database via tests/conftest.py."""
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('a','A'),('b','B'),('c','C')"
        )
        source = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('TURNOVER-A',0) RETURNING location_id"
        )
        target = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('TURNOVER-B',0) RETURNING location_id"
        )
        await conn.execute(
            """INSERT INTO wms.movements
                   (movement_type,product_id,from_location_id,to_location_id,quantity,created_at)
               VALUES
                   ('receive','a',NULL,$1,10,'2026-06-30 20:59:59+00'),
                   ('receive','a',NULL,$1,60,'2026-06-30 21:00:00+00'),
                   ('receive','a',NULL,$2,40.5,'2026-07-01 10:00:00+00'),
                   ('transfer','a',$1,$2,20,'2026-07-01 11:00:00+00'),
                   ('putaway','a',$1,$2,5,'2026-07-01 12:00:00+00'),
                   ('adjust','a',NULL,$1,7,'2026-07-01 13:00:00+00'),
                   ('ship','a',$2,NULL,20,'2026-07-01 14:00:00+00'),
                   ('ship','a',$1,NULL,2.5,'2026-07-03 20:59:59+00'),
                   ('receive','a',NULL,$1,1000,'2026-07-03 21:00:00+00'),
                   ('receive','b',NULL,$2,3,'2026-07-01 09:00:00+00'),
                   ('adjust','c',NULL,$2,9,'2026-07-01 09:00:00+00')""",
            source,
            target,
        )
    service = InventoryHistoryService(InventoryHistoryRepository(kiz_pool))
    result = await service.get_daily_turnover(date(2026, 7, 1), date(2026, 7, 3))
    assert result.total_products == 2
    assert [item.product_id for item in result.items] == ["a", "b"]
    product = result.items[0]
    assert product.totals.received_quantity == Decimal("100.5")
    assert product.totals.shipped_quantity == Decimal("22.5")
    assert product.days[0].received_quantity == Decimal("100.5")
    assert product.days[0].shipped_quantity == Decimal("20")
    assert product.days[1].received_quantity == product.days[1].shipped_quantity == 0
    assert product.days[2].shipped_quantity == Decimal("2.5")
    filtered = await service.get_daily_turnover(date(2026, 7, 1), date(2026, 7, 3), product_id="a")
    assert filtered.total_products == 1
    assert filtered.items == [product]
    missing = await service.get_daily_turnover(
        date(2026, 7, 1), date(2026, 7, 3), product_id="absent"
    )
    assert missing.total_products == 0
    assert missing.items == []


def test_turnover_returns_all_products_without_default_or_max_limit(turnover_client):
    client, repository = turnover_client
    template = repository.rows[0]
    repository.rows = [dict(template, product_id=f"sku-{index:04d}") for index in range(501)]
    response = client.get(
        "/api/inventory-history/daily-turnover",
        params=dict(date_from="2026-07-01", date_to="2026-07-01"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_products"] == len(payload["items"]) == 501
    assert payload["items"][-1]["product_id"] == "sku-0500"
    assert repository.call == (date(2026, 7, 1), date(2026, 7, 1), None, "day")


def test_turnover_openapi_has_no_pagination(turnover_client):
    client, _ = turnover_client
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/inventory-history/daily-turnover"]["get"]
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "date_from",
        "date_to",
        "product_id",
        "group_by",
    }
    properties = schema["components"]["schemas"]["DailyTurnoverResponse"]["properties"]
    assert "limit" not in properties and "offset" not in properties


def test_monthly_http_shape_and_totals(turnover_client):
    client, repository = turnover_client
    repository.rows = [repository.rows[0]]
    response = client.get(
        "/api/inventory-history/daily-turnover",
        params=dict(date_from="2026-07-01", date_to="2026-07-03", group_by="month"),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["group_by"] == "month"
    item = payload["items"][0]
    assert "days" not in item
    assert item["months"] == [
        {"month": "2026-07-01", "received_quantity": 100.5, "shipped_quantity": 20}
    ]
    assert item["totals"] == {"received_quantity": 100.5, "shipped_quantity": 20}
    assert repository.call[-1] == "month"


async def test_monthly_empty_response_and_invalid_group():
    service = InventoryHistoryService(TurnoverRepository())
    result = await service.get_daily_turnover(
        date(2026, 1, 1), date(2026, 12, 31), group_by="month"
    )
    assert result.group_by == "month"
    assert result.items == []
    with pytest.raises(InventoryHistoryValidationError, match="group_by"):
        await service.get_daily_turnover(date(2026, 1, 1), date(2026, 1, 1), group_by="year")


@pytest.mark.parametrize(
    "status,name,params",
    [
        (400, "reversed_dates", {"date_from": "2026-07-03", "date_to": "2026-07-01"}),
        (400, "period_too_long", {"date_from": "2025-01-01", "date_to": "2026-01-02"}),
        (
            422,
            "invalid_group",
            {"date_from": "2026-07-01", "date_to": "2026-07-03", "group_by": "year"},
        ),
        (422, "required_date", {"date_to": "2026-07-03"}),
    ],
)
def test_turnover_swagger_error_examples_match_api(turnover_client, status, name, params):
    client, _ = turnover_client
    path = "/api/inventory-history/daily-turnover"
    route = next(route for route in client.app.routes if getattr(route, "path", None) == path)
    # FastAPI omits None values when generating OpenAPI, including input=null.
    # Compare the original example metadata to the complete runtime JSON.
    example = route.responses[status]["content"]["application/json"]["examples"][name]["value"]
    response = client.get(path, params=params)
    assert response.status_code == status
    assert response.json() == example


async def test_monthly_postgres_boundaries_empty_month_and_year(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute("INSERT INTO public.products(id,name) VALUES ('monthly','Monthly')")
        location = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('MONTHLY',0) RETURNING location_id"
        )
        await conn.execute(
            """INSERT INTO wms.movements
                   (movement_type,product_id,from_location_id,to_location_id,quantity,created_at)
               VALUES
                   ('receive','monthly',NULL,$1,100,'2026-01-14 20:59:59+00'),
                   ('receive','monthly',NULL,$1,10,'2026-01-14 21:00:00+00'),
                   ('ship','monthly',$1,NULL,3,'2026-01-31 20:59:59+00'),
                   ('ship','monthly',$1,NULL,4,'2026-01-31 21:00:00+00'),
                   ('receive','monthly',NULL,$1,5.5,'2026-04-10 20:59:59+00'),
                   ('receive','monthly',NULL,$1,1000,'2026-04-10 21:00:00+00')""",
            location,
        )
    service = InventoryHistoryService(InventoryHistoryRepository(kiz_pool))
    start, end = date(2026, 1, 15), date(2026, 4, 10)
    monthly = await service.get_daily_turnover(start, end, group_by="month")
    daily = await service.get_daily_turnover(start, end)
    product = monthly.items[0]
    assert [m.month for m in product.months] == [date(2026, n, 1) for n in range(1, 5)]
    assert [(m.received_quantity, m.shipped_quantity) for m in product.months] == [
        (Decimal("10"), Decimal("3")),
        (0, Decimal("4")),
        (0, 0),
        (Decimal("5.5"), 0),
    ]
    assert product.totals == daily.items[0].totals
    assert product.totals.received_quantity == Decimal("15.5")
    assert product.totals.shipped_quantity == Decimal("7")
    for group, expected in [("day", 365), ("month", 12)]:
        yearly = await service.get_daily_turnover(
            date(2026, 1, 1), date(2026, 12, 31), group_by=group
        )
        periods = yearly.items[0].days if group == "day" else yearly.items[0].months
        assert len(periods) == expected
