from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints import inventory as inventory_endpoint
from app.core.exceptions import LocationNotFoundError
from app.core.services.inventory_service import InventoryService
from app.infrastructure.database.queries.inventory import GET_LOCATION_RECURSIVE_SUMMARY


class FakeInventoryRepository:
    def __init__(self, rows):
        self.rows = rows
        self.requested_location_id = None

    async def get_location_recursive_summary(self, location_id):
        self.requested_location_id = location_id
        return self.rows


class FakeLocationRepository:
    def __init__(self, location):
        self.location = location

    async def get_by_id(self, location_id):
        return self.location


class FakeContainerRepository:
    pass


def make_service(location, rows):
    inventory_repo = FakeInventoryRepository(rows)
    service = InventoryService(
        inventory_repo,
        FakeLocationRepository(location),
        FakeContainerRepository(),
    )
    return service, inventory_repo


@pytest.mark.asyncio
async def test_get_location_recursive_summary_maps_aggregated_rows():
    updated_at = datetime(2026, 5, 21, tzinfo=timezone.utc)
    service, inventory_repo = make_service(
        location={"location_id": 10},
        rows=[
            {
                "product_id": "SKU-1",
                "product_name": "Product 1",
                "category": "cat",
                "total_quantity": 42,
                "locations_count": 3,
                "in_containers": 30,
                "loose": 12,
                "last_updated": updated_at,
            }
        ],
    )

    result = await service.get_location_recursive_summary(10)

    assert inventory_repo.requested_location_id == 10
    assert len(result) == 1
    assert result[0].product_id == "SKU-1"
    assert result[0].total_quantity == 42
    assert result[0].locations_count == 3
    assert result[0].in_containers == 30
    assert result[0].loose == 12
    assert result[0].last_updated == updated_at


@pytest.mark.asyncio
async def test_get_location_recursive_summary_checks_location_exists():
    service, inventory_repo = make_service(location=None, rows=[])

    with pytest.raises(LocationNotFoundError):
        await service.get_location_recursive_summary(999)

    assert inventory_repo.requested_location_id is None


def test_recursive_summary_route_is_registered():
    paths = {route.path for route in inventory_endpoint.router.routes}

    assert "/inventory/location/{location_id}/recursive-summary" in paths


def test_recursive_summary_sql_uses_ltree_subtree_and_product_aggregation():
    sql = GET_LOCATION_RECURSIVE_SUMMARY

    assert "l.path <@" in sql
    assert "parent.location_id = $1" in sql
    assert "GROUP BY i.product_id, p.name, p.category" in sql
    assert "COUNT(DISTINCT i.location_id)" in sql
    assert "FILTER (WHERE i.container_code IS NOT NULL)" in sql
    assert "FILTER (WHERE i.container_code IS NULL)" in sql
