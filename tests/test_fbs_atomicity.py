from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.core.exceptions import FbsShipmentItemsUpdateError
from app.handlers.write_off_fbs_handler import (
    MARK_ASSEMBLY_TASKS_SHIPPED,
    VALIDATE_ASSEMBLY_TASKS,
    _process_shipment_group,
)
from app.shared.config import settings


@pytest.fixture(autouse=True)
def enable_assembly_task_validation(monkeypatch):
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)


class StatefulTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.snapshot = deepcopy(self.conn.state)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.conn.state.clear()
            self.conn.state.update(self.snapshot)
        return False


class StatefulConnection:
    def __init__(self):
        self.state = {
            "assembly": {10: False, 11: False, 12: False},
            "movements": [],
            "inventory": 10,
            "items": {
                100: {"shipment_id": 70, "status": "new", "movement_id": None},
            },
            "shipment_status": "processing",
        }

    def transaction(self):
        return StatefulTransaction(self)

    async def fetch(self, query, task_ids):
        if query == VALIDATE_ASSEMBLY_TASKS:
            return [
                {"task_id": task_id, "is_shipped": self.state["assembly"][task_id]}
                for task_id in task_ids
                if task_id in self.state["assembly"]
            ]
        if query == MARK_ASSEMBLY_TASKS_SHIPPED:
            updated = []
            for task_id in task_ids:
                if not self.state["assembly"][task_id]:
                    self.state["assembly"][task_id] = True
                    updated.append({"task_id": task_id})
            return updated
        raise AssertionError(f"Unexpected query: {query}")


class StatefulMovementService:
    def __init__(self, state, fail_after_insert=False):
        self.state = state
        self.fail_after_insert = fail_after_insert

    async def create_movement_in_transaction(self, conn, movements):
        movement = movements[0]
        movement_id = 501
        self.state["movements"].append(
            {"movement_id": movement_id, "quantity": movement.quantity}
        )
        self.state["inventory"] -= movement.quantity
        if self.fail_after_insert:
            raise RuntimeError("fault after movement insert")
        return [SimpleNamespace(movement_id=movement_id)]


class StatefulShipmentRepository:
    def __init__(self, state, fail_item_update=False):
        self.state = state
        self.fail_item_update = fail_item_update

    async def lock_items_for_processing(self, conn, *, item_ids):
        return [
            {"item_id": item_id, **self.state["items"][item_id]}
            for item_id in item_ids
        ]

    async def get_success_linked_assembly_tasks(self, conn, *, assembly_tasks):
        return set()

    async def mark_items_success_in_transaction(
        self, conn, *, item_ids, movement_id, retry_count=None
    ):
        if self.fail_item_update:
            raise RuntimeError("fault before item success update")
        for item_id in item_ids:
            self.state["items"][item_id].update(
                status="success", movement_id=movement_id
            )
        return list(item_ids)

    async def update_shipment_status(self, conn, shipment_id):
        self.state["shipment_status"] = "completed"


async def run_group(conn, movement_service, shipment_repo):
    async with conn.transaction():
        return await _process_shipment_group(
            conn=conn,
            product_id="wild2123",
            total_quantity=3,
            all_assembly_tasks=["10", "11", "12"],
            author="FBS 2.0",
            movement_service=movement_service,
            shipment_repo=shipment_repo,
            item_ids=[100],
        )


@pytest.mark.asyncio
async def test_successful_fbs_group_commits_all_related_state():
    conn = StatefulConnection()
    movement_id = await run_group(
        conn,
        StatefulMovementService(conn.state),
        StatefulShipmentRepository(conn.state),
    )

    assert movement_id == 501
    assert conn.state["movements"] == [{"movement_id": 501, "quantity": 3}]
    assert conn.state["inventory"] == 7
    assert all(conn.state["assembly"].values())
    assert conn.state["items"][100]["status"] == "success"
    assert conn.state["items"][100]["movement_id"] == 501
    assert conn.state["shipment_status"] == "completed"


@pytest.mark.asyncio
async def test_error_after_movement_insert_rolls_back_everything():
    conn = StatefulConnection()
    with pytest.raises(RuntimeError, match="after movement insert"):
        await run_group(
            conn,
            StatefulMovementService(conn.state, fail_after_insert=True),
            StatefulShipmentRepository(conn.state),
        )

    assert conn.state["movements"] == []
    assert conn.state["inventory"] == 10
    assert not any(conn.state["assembly"].values())
    assert conn.state["items"][100]["status"] == "new"
    assert conn.state["items"][100]["movement_id"] is None


@pytest.mark.asyncio
async def test_error_after_assembly_update_rolls_back_movement_and_tasks():
    conn = StatefulConnection()
    with pytest.raises(RuntimeError, match="before item success update"):
        await run_group(
            conn,
            StatefulMovementService(conn.state),
            StatefulShipmentRepository(conn.state, fail_item_update=True),
        )

    assert conn.state["movements"] == []
    assert conn.state["inventory"] == 10
    assert not any(conn.state["assembly"].values())
    assert conn.state["items"][100]["status"] == "new"
    assert conn.state["items"][100]["movement_id"] is None
    assert conn.state["shipment_status"] == "processing"


@pytest.mark.asyncio
async def test_missing_locked_item_aborts_before_physical_write_off():
    conn = StatefulConnection()
    repo = StatefulShipmentRepository(conn.state)

    async def lock_nothing(conn, *, item_ids):
        return []

    repo.lock_items_for_processing = lock_nothing
    with pytest.raises(FbsShipmentItemsUpdateError):
        await run_group(conn, StatefulMovementService(conn.state), repo)

    assert conn.state["movements"] == []
    assert conn.state["inventory"] == 10
