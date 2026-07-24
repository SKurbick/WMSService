from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import OperationsHistoryEventIdError, OperationsHistoryNotFoundError
from app.core.operations_history import (
    build_fbs_event_id,
    build_kit_event_id,
    build_movement_event_id,
    build_re_sorting_event_id,
    parse_operation_event_id,
)
from app.core.services.operations_history_service import OperationsHistoryService


UTC_TIME = datetime(2026, 7, 16, 7, 40, 0, 451000, tzinfo=timezone.utc)


class FakeRepository:
    movement_rows = []
    kit = (None, [])
    re_sorting = (None, [])
    fbs = (None, [], [])

    async def get_movement_detail(self, movement_id, created_at):
        assert movement_id == 29530
        assert created_at == UTC_TIME
        return self.movement_rows

    async def get_kit_detail(self, operation_id):
        return self.kit

    async def get_re_sorting_detail(self, operation_id):
        return self.re_sorting

    async def get_fbs_detail(self, shipment_id):
        return self.fbs


def movement(**overrides):
    row = {
        "movement_id": 29530,
        "movement_type": "future_type",
        "product_id": "sku",
        "product_name": "Товар",
        "quantity": Decimal("4.25"),
        "from_location_id": 1,
        "from_location_code": "A",
        "to_location_id": 2,
        "to_location_code": "B",
        "batch_number": None,
        "container_code": None,
        "user_name": "operator",
        "reason": "reason",
        "source_type": None,
        "source_id": None,
        "source_item_id": None,
        "metadata": {},
        "created_at": UTC_TIME,
    }
    row.update(overrides)
    return row


def test_event_id_codec_supports_all_sources_and_exact_microseconds():
    assert build_kit_event_id(42) == "kit_operation:42"
    assert build_re_sorting_event_id(7) == "re_sorting:7"
    assert build_fbs_event_id(156) == "fbs_shipment:156"
    event_id = build_movement_event_id(29530, UTC_TIME)
    parsed = parse_operation_event_id(event_id)
    assert parsed.entity_id == 29530
    assert parsed.movement_created_at == UTC_TIME


@pytest.mark.parametrize(
    "event_id",
    ["unknown:1", "kit_operation", "kit_operation:x", "kit_operation:0", "movement:1", "movement:1:-1"],
)
def test_event_id_codec_rejects_malformed_values(event_id):
    with pytest.raises(ValueError):
        parse_operation_event_id(event_id)


@pytest.mark.asyncio
async def test_standalone_movement_detail_is_typed_and_preserves_unknown_type():
    repository = FakeRepository()
    repository.movement_rows = [movement()]
    response = await OperationsHistoryService(repository).get_operation_detail(
        build_movement_event_id(29530, UTC_TIME)
    )
    assert response.source_type == "movement"
    assert response.operation_type == "future_type"
    assert response.operation_name == "future_type"
    assert response.status is None
    assert response.items == []
    assert len(response.movements) == 1
    assert '"quantity":4.25' in response.model_dump_json()
    assert response.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_malformed_event_id_is_domain_400_error():
    with pytest.raises(OperationsHistoryEventIdError):
        await OperationsHistoryService(FakeRepository()).get_operation_detail("movement:1")


@pytest.mark.asyncio
async def test_absent_valid_event_is_not_found():
    with pytest.raises(OperationsHistoryNotFoundError):
        await OperationsHistoryService(FakeRepository()).get_operation_detail("kit_operation:42")


def test_detail_and_list_routes_are_both_registered():
    from app.main import app

    paths = [route.path for route in app.routes]
    assert "/api/operations-history" in paths
    assert "/api/operations-history/{event_id}" in paths
