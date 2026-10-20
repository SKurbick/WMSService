from dataclasses import dataclass
from decimal import Decimal

import pytest
import asyncpg.exceptions
from pydantic import ValidationError

from app.core.exceptions import FbsKizProcessingError
from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.core.services.fbs_kiz_shipment_service import FbsKizShipmentService
from app.handlers.write_off_fbs_handler import format_fbs_processing_error


def payload(**updates):
    value = {
        "author": "operator",
        "supply_id": "SUP-1",
        "product_id": "p1",
        "warehouse_id": 1,
        "delivery_type": "fbs",
        "account": "account",
        "quantity": 2,
        "assembly_tasks": ["1", "2"],
        "kiz_bindings": [{"assembly_task": "1", "kiz_code": "K1"}],
    }
    value.update(updates)
    return value


def test_legacy_payload_remains_valid():
    request = WriteOffAccordingToFBS(**payload(kiz_bindings=None))
    assert request.kiz_bindings is None
    assert request.assembly_tasks == ["1", "2"]


@pytest.mark.parametrize(
    "bindings",
    [
        [{"assembly_task": "3", "kiz_code": "K1"}],
        [
            {"assembly_task": "1", "kiz_code": "K1"},
            {"assembly_task": "1", "kiz_code": "K2"},
        ],
        [
            {"assembly_task": "1", "kiz_code": "K1"},
            {"assembly_task": "2", "kiz_code": "K1"},
        ],
        [{"assembly_task": "1", "kiz_code": "   "}],
    ],
)
def test_invalid_kiz_bindings_are_rejected(bindings):
    with pytest.raises(ValidationError):
        WriteOffAccordingToFBS(**payload(kiz_bindings=bindings))


@dataclass
class FakeConnection:
    pass


class FakeRepository:
    def __init__(self, bindings, scopes=None):
        self.bindings = bindings
        self.scopes = scopes or {}
        self.transitions = []
        self.movements = []
        self.shipped = []
        self.completed = None
        self.next_operation_item = 100

    async def lock_bindings(self, connection, task_ids):
        return {task: self.bindings[task] for task in task_ids if task in self.bindings}

    async def get_location_by_code(self, connection, code):
        return {"location_id": 10, "location_code": code}

    async def get_locations(self, connection, location_ids):
        names = {10: "PUSHKINO-ФБС", 20: "STORAGE-A"}
        return {
            location_id: {"location_id": location_id, "location_code": names[location_id]}
            for location_id in location_ids
        }

    async def lock_source_scopes(self, connection, scopes):
        return {
            scope: self.scopes.get(scope, {"physical": Decimal("10"), "identified": 0})
            for scope in scopes
        }

    async def create_operation(self, connection, **kwargs):
        return 50

    async def create_operation_item(self, connection, operation_id, external_line_id):
        self.next_operation_item += 1
        return self.next_operation_item

    async def transition_kiz_to_shipped(self, connection, **kwargs):
        self.transitions.append(kwargs)

    async def create_movement(self, connection, **kwargs):
        movement = {
            "movement_id": len(self.movements) + 1,
            "created_at": f"created-{len(self.movements) + 1}",
            "movement_ref": 1000 + len(self.movements) + 1,
            **kwargs,
        }
        self.movements.append(movement)
        return movement

    async def link_operation_movement(self, connection, operation_item_id, movement_ref):
        return None

    async def record_shipped_kiz(self, connection, **kwargs):
        self.shipped.append(kwargs)

    async def complete_operation(self, connection, operation_id, items):
        self.completed = {"operation_id": operation_id, "items": list(items)}


def result(task_id):
    return {
        "shipment_id": 1,
        "item_id": 1,
        "occurrence_index": int(task_id) - 1,
        "task_id": task_id,
        "product_id": "p1",
        "outcome": "new",
        "effect_quantity": 0,
        "details": {},
    }


def binding(task_id, code, lifecycle, location_id=None, container_id=None):
    return {
        "binding_id": int(task_id),
        "task_id": int(task_id),
        "kiz_code": code,
        "kiz_id": int(task_id),
        "product_id": "p1",
        "kiz_product_id": "p1",
        "state": "resolved",
        "lifecycle_status": lifecycle,
        "location_id": location_id,
        "container_id": container_id,
    }


@pytest.mark.asyncio
async def test_mixed_group_creates_one_movement_per_physical_source():
    repository = FakeRepository(
        {
            "1": binding("1", "K1", "active", location_id=20),
            "2": binding("2", "K2", "registered"),
        },
        {
            (10, "p1"): {"physical": Decimal("5"), "identified": 0},
            (20, "p1"): {"physical": Decimal("3"), "identified": 1},
        },
    )
    results = [result("1"), result("2"), result("3")]

    movements = await FbsKizShipmentService(repository).process(
        FakeConnection(),
        shipment_id=1,
        product_id="p1",
        author="operator",
        fbs_location_code="PUSHKINO-ФБС",
        results=results,
        new_tasks=["1", "2", "3"],
    )

    assert [(movement["source_location_id"], movement["quantity"]) for movement in movements] == [
        (10, 2),
        (20, 1),
    ]
    by_task = {row["task_id"]: row for row in results}
    assert by_task["1"]["source_kind"] == "active_loose"
    assert by_task["1"]["movement_ref"] != by_task["2"]["movement_ref"]
    assert by_task["2"]["movement_ref"] == by_task["3"]["movement_ref"]
    assert len(repository.transitions) == 2
    assert len(repository.shipped) == 2


@pytest.mark.asyncio
async def test_unknown_kiz_is_retryable_without_movements():
    unknown = binding("1", "K1", None)
    unknown["kiz_id"] = None
    repository = FakeRepository({"1": unknown})

    with pytest.raises(FbsKizProcessingError) as caught:
        await FbsKizShipmentService(repository).process(
            FakeConnection(),
            shipment_id=1,
            product_id="p1",
            author="operator",
            fbs_location_code="PUSHKINO-ФБС",
            results=[result("1")],
            new_tasks=["1"],
        )

    assert caught.value.error_code == "KIZ_NOT_REGISTERED"
    assert caught.value.retryable is True
    assert repository.movements == []


@pytest.mark.asyncio
async def test_contained_kiz_is_controlled_permanent_failure():
    repository = FakeRepository(
        {"1": binding("1", "K1", "active", container_id=99)}
    )

    with pytest.raises(FbsKizProcessingError) as caught:
        await FbsKizShipmentService(repository).process(
            FakeConnection(),
            shipment_id=1,
            product_id="p1",
            author="operator",
            fbs_location_code="PUSHKINO-ФБС",
            results=[result("1")],
            new_tasks=["1"],
        )

    assert caught.value.error_code == "KIZ_CONTAINER_SHIPMENT_UNSUPPORTED"
    assert caught.value.retryable is False
    assert repository.movements == []


@pytest.mark.asyncio
async def test_registered_kiz_ships_from_fbs_without_activation():
    repository = FakeRepository({"1": binding("1", "K1", "registered")})
    results = [result("1")]

    movements = await FbsKizShipmentService(repository).process(
        FakeConnection(),
        shipment_id=1,
        product_id="p1",
        author="operator",
        fbs_location_code="PUSHKINO-ФБС",
        results=results,
        new_tasks=["1"],
    )

    assert [(movement["source_location_id"], movement["quantity"]) for movement in movements] == [
        (10, 1)
    ]
    assert repository.transitions[0]["source_location_id"] == 10
    assert results[0]["source_kind"] == "fbs_zone"


@pytest.mark.asyncio
async def test_active_kiz_at_fbs_is_selected_from_identified_stock():
    repository = FakeRepository(
        {"1": binding("1", "K1", "active", location_id=10)},
        {(10, "p1"): {"physical": Decimal("1"), "identified": 1}},
    )

    movements = await FbsKizShipmentService(repository).process(
        FakeConnection(),
        shipment_id=1,
        product_id="p1",
        author="operator",
        fbs_location_code="PUSHKINO-ФБС",
        results=[result("1")],
        new_tasks=["1"],
    )

    assert movements[0]["source_location_id"] == 10
    assert movements[0]["quantity"] == 1


@pytest.mark.asyncio
async def test_one_source_shortage_rejects_whole_group_before_writes():
    repository = FakeRepository(
        {
            "1": binding("1", "K1", "active", location_id=20),
            "2": binding("2", "K2", "registered"),
        },
        {
            (10, "p1"): {"physical": Decimal("0"), "identified": 0},
            (20, "p1"): {"physical": Decimal("1"), "identified": 1},
        },
    )

    with pytest.raises(FbsKizProcessingError) as caught:
        await FbsKizShipmentService(repository).process(
            FakeConnection(),
            shipment_id=1,
            product_id="p1",
            author="operator",
            fbs_location_code="PUSHKINO-ФБС",
            results=[result("1"), result("2")],
            new_tasks=["1", "2"],
        )

    assert caught.value.error_code == "KIZ_CONFLICT"
    assert repository.transitions == []
    assert repository.movements == []


@pytest.mark.asyncio
async def test_terminal_kiz_is_controlled_failure_without_writes():
    repository = FakeRepository({"1": binding("1", "K1", "shipped")})

    with pytest.raises(FbsKizProcessingError) as caught:
        await FbsKizShipmentService(repository).process(
            FakeConnection(),
            shipment_id=1,
            product_id="p1",
            author="operator",
            fbs_location_code="PUSHKINO-ФБС",
            results=[result("1")],
            new_tasks=["1"],
        )

    assert caught.value.error_code == "KIZ_TERMINAL"
    assert repository.movements == []


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.SerializationError("serialization"),
        asyncpg.exceptions.DeadlockDetectedError("deadlock"),
    ],
)
def test_concurrent_database_errors_have_controlled_retry_code(error):
    assert format_fbs_processing_error(error).startswith("CONCURRENT_WRITE_CONFLICT:")
