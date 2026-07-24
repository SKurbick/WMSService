from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import InventoryHistoryValidationError, LocationNotFoundError
from app.core.services.inventory_history_service import InventoryHistoryService
from app.infrastructure.database.queries.inventory_history import GET_DAILY_BALANCES


class FakeInventoryHistoryRepository:
    def __init__(self, rows=None, total=1, location_exists=True):
        self.rows = rows or []
        self.total = total
        self.location_exists = location_exists
        self.call = None

    async def get_daily_balance_page(self, *args):
        self.call = args
        return self.location_exists, self.total, self.rows


def row(day, opening, incoming, outgoing, closing, product_id="sku-1", name="Товар"):
    return {
        "product_id": product_id,
        "product_name": name,
        "day": day,
        "opening_quantity": Decimal(str(opening)),
        "incoming_quantity": Decimal(str(incoming)),
        "outgoing_quantity": Decimal(str(outgoing)),
        "closing_quantity": Decimal(str(closing)),
    }


@pytest.mark.asyncio
async def test_chain_of_days_and_day_without_movements():
    repository = FakeInventoryHistoryRepository(
        [
            row(date(2026, 7, 1), 0, 10, 0, 10),
            row(date(2026, 7, 2), 10, 0, 0, 10),
            row(date(2026, 7, 3), 10, 0, 4, 6),
        ]
    )
    response = await InventoryHistoryService(repository).get_daily_balances(
        date(2026, 7, 1), date(2026, 7, 3)
    )

    assert [day.model_dump() for day in response.items[0].days] == [
        {
            "date": date(2026, 7, 1),
            "opening_quantity": Decimal("0"),
            "incoming_quantity": Decimal("10"),
            "outgoing_quantity": Decimal("0"),
            "closing_quantity": Decimal("10"),
        },
        {
            "date": date(2026, 7, 2),
            "opening_quantity": Decimal("10"),
            "incoming_quantity": Decimal("0"),
            "outgoing_quantity": Decimal("0"),
            "closing_quantity": Decimal("10"),
        },
        {
            "date": date(2026, 7, 3),
            "opening_quantity": Decimal("10"),
            "incoming_quantity": Decimal("0"),
            "outgoing_quantity": Decimal("4"),
            "closing_quantity": Decimal("6"),
        },
    ]


@pytest.mark.asyncio
async def test_opening_before_period_and_json_quantities_are_numbers():
    repository = FakeInventoryHistoryRepository(
        [row(date(2026, 7, 1), Decimal("3.50"), 0, 0, Decimal("3.50"))]
    )
    response = await InventoryHistoryService(repository).get_daily_balances(
        date(2026, 7, 1), date(2026, 7, 1)
    )
    day_payload = response.model_dump(mode="json")["items"][0]["days"][0]

    assert day_payload["opening_quantity"] == 3.5
    assert isinstance(day_payload["opening_quantity"], float)
    assert isinstance(response.items[0].days[0].opening_quantity, Decimal)


def test_transfer_uses_both_sides_without_movement_type_rules():
    sql = GET_DAILY_BALANCES.lower()

    assert "m.to_location_id" in sql
    assert "m.from_location_id" in sql
    assert "union all" in sql
    assert "true as is_incoming" in sql
    assert "false as is_incoming" in sql
    assert "movement_type" not in sql


def test_timezone_and_half_open_interval_are_explicit_in_sql():
    assert "AT TIME ZONE 'Europe/Moscow'" in GET_DAILY_BALANCES
    assert "(l.created_at AT TIME ZONE 'Europe/Moscow')::date" in GET_DAILY_BALANCES
    assert "l.created_at >= p.period_start" in GET_DAILY_BALANCES
    assert "l.created_at < p.period_end" in GET_DAILY_BALANCES


def test_kit_and_re_sorting_need_no_special_sql_branch():
    assert "kit_assembly" not in GET_DAILY_BALANCES
    assert "kit_disassembly" not in GET_DAILY_BALANCES
    assert "re_sorting" not in GET_DAILY_BALANCES


@pytest.mark.asyncio
async def test_pagination_is_by_product_and_keeps_full_day_ranges():
    rows = [
        row(date(2026, 7, day), day - 1, 1, 0, day, product_id=product)
        for product in ("sku-2", "sku-3")
        for day in (1, 2, 3)
    ]
    repository = FakeInventoryHistoryRepository(rows, total=5)
    response = await InventoryHistoryService(repository).get_daily_balances(
        date(2026, 7, 1), date(2026, 7, 3), limit=2, offset=1
    )

    assert response.total_products == 5
    assert [item.product_id for item in response.items] == ["sku-2", "sku-3"]
    assert [len(item.days) for item in response.items] == [3, 3]
    assert "LIMIT $6 OFFSET $7" in GET_DAILY_BALANCES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "date_from,date_to,include_subtree,message",
    [
        (date(2026, 7, 2), date(2026, 7, 1), False, "date_from"),
        (date(2025, 7, 1), date(2026, 7, 2), False, "366"),
        (date(2026, 7, 1), date(2026, 7, 1), True, "location_id"),
    ],
)
async def test_cross_parameter_validation(date_from, date_to, include_subtree, message):
    with pytest.raises(InventoryHistoryValidationError, match=message):
        await InventoryHistoryService(FakeInventoryHistoryRepository()).get_daily_balances(
            date_from, date_to, include_subtree=include_subtree
        )


@pytest.mark.asyncio
async def test_missing_location_is_404_domain_error():
    with pytest.raises(LocationNotFoundError):
        await InventoryHistoryService(
            FakeInventoryHistoryRepository(location_exists=False)
        ).get_daily_balances(date(2026, 7, 1), date(2026, 7, 1), location_id=999)
