from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.api.v1.endpoints import inventory as inventory_endpoint
from app.consumer import _looks_like_reservation_message
from app.core.exceptions import LocationNotFoundError
from app.core.schemas.stock_reservation import ProductAvailabilityResponse
from app.core.services.stock_reservation_service import StockReservationService
from app.infrastructure.database.queries import stock_reservations as queries


class FakeReservationRepository:
    def __init__(self, product_exists=True):
        self._product_exists = product_exists
        self.upserts = []
        self.events = []

    async def product_exists(self, conn, product_id):
        return self._product_exists

    async def upsert_reservation_order(self, conn, **kwargs):
        self.upserts.append(kwargs)
        return kwargs

    async def insert_reservation_event(self, conn, **kwargs):
        self.events.append(kwargs)
        return kwargs


class FakeLocationRepository:
    def __init__(self, location):
        self.location = location
        self.requested_location_id = None

    async def get_by_id(self, location_id):
        self.requested_location_id = location_id
        return self.location


class FakeAvailabilityRepository(FakeReservationRepository):
    async def get_location_subtree_availability(self, location_id):
        return []


@pytest.mark.asyncio
async def test_reserved_status_upserts_active_reservation_and_audit_event():
    repo = FakeReservationRepository(product_exists=True)
    service = StockReservationService(repo)
    created_at = datetime(2026, 5, 22, 10, 30, tzinfo=timezone.utc)

    result = await service.process_reservation_order(
        conn=object(),
        product_id="wild1605",
        external_order_id=12345,
        external_status="new",
        external_created_at=created_at,
        raw_payload={"wild": "wild1605"},
    )

    assert result == "processed"
    assert repo.upserts[0]["is_reserved"] is True
    assert repo.upserts[0]["reserved_qty"] == Decimal("1")
    assert repo.events[0]["processing_result"] == "processed"


@pytest.mark.asyncio
async def test_release_status_only_marks_reservation_inactive():
    repo = FakeReservationRepository(product_exists=True)
    service = StockReservationService(repo)

    result = await service.process_reservation_order(
        conn=object(),
        product_id="wild1605",
        external_order_id=12345,
        external_status="shipped",
        external_created_at=None,
        raw_payload={"wild": "wild1605"},
    )

    assert result == "released"
    assert repo.upserts[0]["is_reserved"] is False
    assert repo.events[0]["processing_result"] == "released"


@pytest.mark.asyncio
async def test_unknown_status_writes_audit_without_upsert():
    repo = FakeReservationRepository(product_exists=True)
    service = StockReservationService(repo)

    result = await service.process_reservation_order(
        conn=object(),
        product_id="wild1605",
        external_order_id=12345,
        external_status="cancelled",
        external_created_at=None,
        raw_payload={"wild": "wild1605"},
    )

    assert result == "unknown_status"
    assert repo.upserts == []
    assert repo.events[0]["processing_result"] == "unknown_status"


@pytest.mark.asyncio
async def test_unknown_product_writes_audit_without_upsert():
    repo = FakeReservationRepository(product_exists=False)
    service = StockReservationService(repo)

    result = await service.process_reservation_order(
        conn=object(),
        product_id="missing",
        external_order_id=12345,
        external_status="new",
        external_created_at=None,
        raw_payload={"wild": "missing"},
    )

    assert result == "product_not_found"
    assert repo.upserts == []
    assert repo.events[0]["processing_result"] == "product_not_found"


def test_stock_reservation_routes_are_registered():
    paths = {route.path for route in inventory_endpoint.router.routes}

    assert "/inventory/availability" in paths
    assert "/inventory/availability/totals" in paths
    assert "/inventory/product/{product_id}/availability" in paths
    assert "/inventory/location/{location_id}/availability" in paths
    assert "/inventory/reservations" in paths
    assert "/inventory/reservation-events" in paths


def test_reservation_consumer_detection_does_not_match_write_off_message():
    write_off = [{"product_id": "wild1605", "quantity": 1, "assembly_tasks": ["123"]}]
    reservation = [{"wild": "wild1605", "orders": [{"order_id": 123, "status": "new"}]}]

    assert _looks_like_reservation_message(reservation) is True
    assert _looks_like_reservation_message(write_off) is False


def test_reservation_sql_is_idempotent_and_does_not_touch_inventory_or_movements():
    assert (
        "ON CONFLICT (source_type, product_id, external_order_id)"
        in queries.UPSERT_RESERVATION_ORDER
    )
    assert "wms.inventory" not in queries.UPSERT_RESERVATION_ORDER
    assert "wms.movements" not in queries.UPSERT_RESERVATION_ORDER
    assert "wms.v_product_availability" in queries.GET_PRODUCT_AVAILABILITY
    assert "free_qty" in queries.GET_PRODUCT_AVAILABILITY
    assert "shortage_qty" in queries.GET_PRODUCT_AVAILABILITY


def test_availability_response_serializes_quantities_as_json_numbers():
    response = ProductAvailabilityResponse(
        product_id="wild1605",
        physical_qty=Decimal("2.000"),
        reserved_qty=Decimal("1.000"),
        free_qty=Decimal("1.000"),
        shortage_qty=Decimal("0.000"),
    )

    payload = response.model_dump(mode="json")

    assert payload["physical_qty"] == 2.0
    assert isinstance(payload["physical_qty"], float)


def test_availability_sql_supports_filters_totals_and_location_subtree():
    assert "only_shortage" not in queries.LIST_PRODUCT_AVAILABILITY
    assert "shortage_qty > 0" in queries.LIST_PRODUCT_AVAILABILITY
    assert "reserved_qty > 0" in queries.LIST_PRODUCT_AVAILABILITY
    assert "SUM(shortage_qty)" in queries.GET_AVAILABILITY_TOTALS
    assert "GREATEST(SUM(reserved_qty)" not in queries.GET_AVAILABILITY_TOTALS
    assert "l.path <@" in queries.GET_LOCATION_SUBTREE_AVAILABILITY
    assert "wms.movements" not in queries.GET_LOCATION_SUBTREE_AVAILABILITY


@pytest.mark.asyncio
async def test_location_availability_checks_location_exists():
    reservation_repo = FakeAvailabilityRepository()
    location_repo = FakeLocationRepository(location=None)
    service = StockReservationService(reservation_repo, location_repo)

    with pytest.raises(LocationNotFoundError):
        await service.get_location_subtree_availability(999)

    assert location_repo.requested_location_id == 999
