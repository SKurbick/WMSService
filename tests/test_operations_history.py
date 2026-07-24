from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import LocationNotFoundError, OperationsHistoryValidationError
from app.core.services.operations_history_service import OperationsHistoryService
from app.infrastructure.database.queries.operations_history import GET_OPERATIONS_HISTORY


class FakeOperationsHistoryRepository:
    def __init__(self, rows=None, total=0, location_exists=True):
        self.rows = rows or []
        self.total = total
        self.location_exists = location_exists
        self.arguments = None

    async def get_page(self, *arguments):
        self.arguments = arguments
        return self.location_exists, self.total, self.rows


def event_row(**overrides):
    payload = {
        "event_id": "kit_operation:42",
        "source_type": "kit_operation",
        "operation_type": "kit_assembly",
        "operation_name": "Комплектация",
        "status": "completed",
        "created_at": datetime(2026, 7, 22, 7, 15, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 7, 22, 7, 15, 1, tzinfo=timezone.utc),
        "author": "operator.pushkino",
        "location_id": 123,
        "location_code": "PUSHKINO-УПАКОВКА",
        "product_count": 3,
        "total_quantity": Decimal("1"),
        "external_reference": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_kit_operation_is_returned_as_one_normalized_event():
    repository = FakeOperationsHistoryRepository([event_row()], total=1)
    response = await OperationsHistoryService(repository).get_operations(
        date(2026, 7, 22), date(2026, 7, 22)
    )

    assert response.total == 1
    assert [item.event_id for item in response.items] == ["kit_operation:42"]
    assert "m.source_type IS DISTINCT FROM 'kit_operation'" in GET_OPERATIONS_HISTORY


def test_re_sorting_header_and_movements_are_not_duplicated():
    assert "'re_sorting:' || ro.operation_id" in GET_OPERATIONS_HISTORY
    assert "m.source_type IS DISTINCT FROM 're_sorting_operation'" in GET_OPERATIONS_HISTORY
    assert "UNION ALL" in GET_OPERATIONS_HISTORY


@pytest.mark.asyncio
async def test_failed_fbs_header_without_items_is_preserved():
    row = event_row(
        event_id="fbs_shipment:9",
        source_type="fbs_shipment",
        operation_type="fbs_shipment",
        operation_name="ФБС-отгрузка",
        status="validation_failed",
        completed_at=None,
        author=None,
        location_id=None,
        location_code=None,
        product_count=0,
        total_quantity=Decimal("0"),
    )
    response = await OperationsHistoryService(
        FakeOperationsHistoryRepository([row], total=1)
    ).get_operations(date(2026, 7, 1), date(2026, 7, 1))

    assert response.items[0].status == "validation_failed"
    assert response.items[0].product_count == 0
    assert response.items[0].total_quantity == Decimal("0")


def test_fbs_items_are_aggregated_into_one_shipment_event():
    sql = GET_OPERATIONS_HISTORY
    assert "GROUP BY fs.shipment_id" in sql
    assert "count(DISTINCT i.product_id)" in sql
    assert "COALESCE(sum(i.quantity), 0)::numeric" in sql
    assert "count(DISTINCT i.author) = 1" in sql
    assert "count(DISTINCT i.supply_id) = 1" in sql


def test_only_unambiguous_fbs_movement_is_hidden():
    sql = GET_OPERATIONS_HISTORY
    assert "fbs_movement_matches AS" in sql
    assert "GROUP BY m.movement_id" in sql
    assert "WHERE matches = 1" in sql
    assert "NOT EXISTS (" in sql
    assert "f.movement_id = m.movement_id" in sql


def test_ambiguous_fbs_movement_id_is_not_selected_for_exclusion():
    assert "WHERE matches = 1" in GET_OPERATIONS_HISTORY
    assert "matches > 1" not in GET_OPERATIONS_HISTORY
    assert "reason" not in GET_OPERATIONS_HISTORY.lower()


def test_standalone_receive_and_unknown_type_have_safe_name_mapping():
    sql = GET_OPERATIONS_HISTORY
    assert "WHEN 'receive' THEN 'Поступление'" in sql
    assert "ELSE m.movement_type" in sql
    assert "m.movement_type::text AS operation_type" in sql


def test_transfer_location_is_null_but_filter_checks_both_sides():
    sql = GET_OPERATIONS_HISTORY
    assert "WHEN m.from_location_id IS NULL THEN m.to_location_id" in sql
    assert "WHEN m.to_location_id IS NULL THEN m.from_location_id" in sql
    assert "ELSE NULL" in sql
    assert "m.from_location_id = $6 OR m.to_location_id = $6" in sql


def test_stable_sort_and_pagination_are_after_union():
    sql = GET_OPERATIONS_HISTORY
    assert sql.index("all_events AS") < sql.index("ORDER BY created_at DESC, event_id DESC")
    assert sql.index("ORDER BY created_at DESC, event_id DESC") < sql.index("LIMIT $9 OFFSET $10")


def test_filters_are_pushed_into_all_relevant_branches():
    sql = GET_OPERATIONS_HISTORY
    assert sql.count("$3::text IS NULL") == 4
    assert "i.operation_id = ko.operation_id AND i.product_id = $5" in sql
    assert "ro.from_product_id = $5 OR ro.to_product_id = $5" in sql
    assert "bool_or(i.product_id = $5)" in sql
    assert "m.product_id = $5" in sql
    assert "$6::bigint IS NULL" in sql
    assert "$7::text IS NULL" in sql
    assert "$8::text IS NULL" in sql


def test_movement_event_id_uses_id_and_epoch_microseconds():
    sql = GET_OPERATIONS_HISTORY
    assert "'movement:' || m.movement_id || ':'" in sql
    assert "extract(epoch FROM m.created_at) * 1000000" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"date_from": date(2026, 7, 2), "date_to": date(2026, 7, 1)}, "date_from"),
        ({"date_from": date(2025, 7, 1), "date_to": date(2026, 7, 2)}, "366"),
        (
            {
                "date_from": date(2026, 7, 1),
                "date_to": date(2026, 7, 1),
                "source_type": "receipt",
            },
            "source_type",
        ),
        (
            {
                "date_from": date(2026, 7, 1),
                "date_to": date(2026, 7, 1),
                "operation_type": "write_off",
            },
            "operation_type",
        ),
    ],
)
async def test_request_validation(kwargs, message):
    with pytest.raises(OperationsHistoryValidationError, match=message):
        await OperationsHistoryService(FakeOperationsHistoryRepository()).get_operations(**kwargs)


@pytest.mark.asyncio
async def test_missing_location_raises_not_found():
    repository = FakeOperationsHistoryRepository(location_exists=False)
    with pytest.raises(LocationNotFoundError):
        await OperationsHistoryService(repository).get_operations(
            date(2026, 7, 1), date(2026, 7, 1), location_id=999
        )


@pytest.mark.asyncio
async def test_filters_and_pagination_are_forwarded_to_repository():
    repository = FakeOperationsHistoryRepository()
    await OperationsHistoryService(repository).get_operations(
        date(2026, 7, 1),
        date(2026, 7, 2),
        source_type="movement",
        operation_type="transfer",
        product_id="sku",
        location_id=7,
        author="operator",
        status=None,
        limit=20,
        offset=40,
    )

    assert repository.arguments == (
        date(2026, 7, 1),
        date(2026, 7, 2),
        "movement",
        "transfer",
        "sku",
        7,
        "operator",
        None,
        20,
        40,
    )


@pytest.mark.asyncio
async def test_decimal_is_kept_in_python_and_serialized_as_json_number():
    row = event_row(total_quantity=Decimal("1.25"))
    response = await OperationsHistoryService(
        FakeOperationsHistoryRepository([row], total=1)
    ).get_operations(date(2026, 7, 22), date(2026, 7, 22))

    assert isinstance(response.items[0].total_quantity, Decimal)
    payload = response.model_dump(mode="json")
    assert payload["items"][0]["total_quantity"] == 1.25
    assert isinstance(payload["items"][0]["total_quantity"], float)


def test_moscow_half_open_boundaries_are_explicit():
    sql = GET_OPERATIONS_HISTORY
    assert "AT TIME ZONE 'Europe/Moscow'" in sql
    assert "m.created_at >= p.period_start AND m.created_at < p.period_end" in sql
    assert "fs.received_at >= p.period_start AND fs.received_at < p.period_end" in sql
