from decimal import Decimal

import pytest

from app.api.v1.endpoints import kit_operations as endpoint
from app.core.enums import KitOperationItemRole, KitOperationType, MovementType
from app.core.exceptions import KitOperationConflictError, KitOperationValidationError
from app.core.services.kit_operation_service import KitOperationService
from app.infrastructure.database.queries import kit_operations as queries


class FakeKitOperationRepository:
    def __init__(self, loose=None, container_qty=Decimal("0"), operation_location=None):
        self.pool = None
        self.loose = loose
        self.container_qty = container_qty
        self.operation_location = operation_location

    async def get_loose_inventory_for_update(self, conn, product_id, location_id):
        if self.loose is None:
            return None
        return {"inventory_id": 1, "quantity": self.loose}

    async def get_container_inventory_quantity(self, conn, product_id, location_id):
        return self.container_qty

    async def get_location_by_code(self, conn, location_code):
        return {
            "location_id": 55,
            "location_code": location_code,
            "name": "Kit zone",
            "is_active": True,
            "level": 1,
        }

    async def get_active_kit_operation_location(self, conn, location_code, location_id):
        return self.operation_location


def test_kit_operation_routes_are_registered_with_locations_before_dynamic_route():
    paths = [route.path for route in endpoint.router.routes]

    assert "/kit-operations" in paths
    assert "/kit-operations/locations" in paths
    assert "/kit-operations/locations/{operation_location_id}/deactivate" in paths
    assert "/kit-operations/{operation_id}" in paths
    assert paths.index("/kit-operations/locations") < paths.index("/kit-operations/{operation_id}")


def test_kit_operation_sql_uses_allowed_locations_direct_scope_and_source_linkage():
    assert "wms.operation_locations" in queries.GET_ACTIVE_KIT_OPERATION_LOCATION
    assert "operation_code = 'kit_operations'" in queries.GET_ACTIVE_KIT_OPERATION_LOCATION
    assert "scope = 'direct'" in queries.GET_ACTIVE_KIT_OPERATION_LOCATION
    assert "is_active = TRUE" in queries.GET_ACTIVE_KIT_OPERATION_LOCATION
    assert "operation_location_id" in queries.CREATE_KIT_OPERATION
    assert "location_code" in queries.CREATE_KIT_OPERATION
    assert "pg_advisory_xact_lock" in queries.LOCK_KIT_OPERATION_SCOPE
    assert "FOR UPDATE" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "status = 'available'" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "batch_number IS NULL" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "container_code IS NULL" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "source_type" in queries.CREATE_KIT_MOVEMENT
    assert "source_id" in queries.CREATE_KIT_MOVEMENT
    assert "source_item_id" in queries.CREATE_KIT_MOVEMENT
    assert "kit_operation" in queries.CREATE_KIT_MOVEMENT


def test_kit_operation_service_no_longer_requires_level_5():
    names = KitOperationService._validate_operation_location.__code__.co_names
    constants = KitOperationService._validate_operation_location.__code__.co_consts

    assert "level" not in names
    assert 5 not in constants


@pytest.mark.asyncio
async def test_operation_location_must_be_allowed_for_kit_operations():
    service = KitOperationService(FakeKitOperationRepository(operation_location=None))

    with pytest.raises(KitOperationConflictError, match="not allowed"):
        await service._validate_operation_location(object(), "PUSHKINO-КОМПЛЕКТАЦИЯ")


def test_assembly_movement_specs_consume_components_and_create_kit_result():
    service = KitOperationService(FakeKitOperationRepository())

    specs = service._movement_specs(
        KitOperationType.ASSEMBLY,
        "metawild_test",
        {"testwild": Decimal("2"), "testwild2": Decimal("1")},
        Decimal("3"),
        55,
    )

    assert [spec["role"] for spec in specs] == [
        KitOperationItemRole.COMPONENT_CONSUMPTION.value,
        KitOperationItemRole.COMPONENT_CONSUMPTION.value,
        KitOperationItemRole.KIT_RESULT.value,
    ]
    assert [spec["movement_type"] for spec in specs] == [MovementType.KIT_ASSEMBLY.value] * 3
    assert specs[0]["product_id"] == "testwild"
    assert specs[0]["total_quantity"] == Decimal("6")
    assert specs[0]["from_location_id"] == 55
    assert specs[0]["to_location_id"] is None
    assert specs[2]["product_id"] == "metawild_test"
    assert specs[2]["total_quantity"] == Decimal("3")
    assert specs[2]["from_location_id"] is None
    assert specs[2]["to_location_id"] == 55


def test_disassembly_movement_specs_consume_kit_and_create_components():
    service = KitOperationService(FakeKitOperationRepository())

    specs = service._movement_specs(
        KitOperationType.DISASSEMBLY,
        "metawild_test",
        {"testwild": Decimal("2"), "testwild2": Decimal("1")},
        Decimal("3"),
        55,
    )

    assert [spec["role"] for spec in specs] == [
        KitOperationItemRole.KIT_CONSUMPTION.value,
        KitOperationItemRole.COMPONENT_RESULT.value,
        KitOperationItemRole.COMPONENT_RESULT.value,
    ]
    assert [spec["movement_type"] for spec in specs] == [MovementType.KIT_DISASSEMBLY.value] * 3
    assert specs[0]["product_id"] == "metawild_test"
    assert specs[0]["total_quantity"] == Decimal("3")
    assert specs[0]["from_location_id"] == 55
    assert specs[1]["product_id"] == "testwild"
    assert specs[1]["total_quantity"] == Decimal("6")
    assert specs[1]["to_location_id"] == 55


def test_parse_components_rejects_empty_or_non_positive_quantities():
    service = KitOperationService(FakeKitOperationRepository())

    with pytest.raises(KitOperationValidationError):
        service._parse_components({})
    with pytest.raises(KitOperationValidationError):
        service._parse_components({"testwild": 0})


@pytest.mark.asyncio
async def test_container_only_consumption_is_conflict_for_mvp():
    service = KitOperationService(
        FakeKitOperationRepository(loose=None, container_qty=Decimal("10"))
    )

    with pytest.raises(KitOperationConflictError, match="only loose stock"):
        await service._check_consumption_stock(
            conn=object(),
            operation_type=KitOperationType.DISASSEMBLY,
            kit_product_id="metawild_test",
            kit_components={"testwild": Decimal("2")},
            quantity=Decimal("3"),
            location_id=55,
        )
