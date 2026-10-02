from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import fbs_shipments as endpoint
from app.handlers.write_off_fbs_handler import (
    _process_shipment_group,
    record_task_level_attempt_failure,
)
from app.infrastructure.database.repositories import fbs_shipment_repository as repository
from app.shared.config import settings


@pytest.fixture(autouse=True)
def task_level_mode(monkeypatch):
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    monkeypatch.setattr(settings, "FBS_TASK_PROCESSING_MODE", "task_level")


class TaskLevelMovementService:
    def __init__(self):
        self.quantities = []

    async def create_movement_in_transaction(self, conn, movements):
        self.quantities.append(movements[0].quantity)
        return [
            SimpleNamespace(
                movement_id=901,
                created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
            )
        ]


class TaskLevelRepository:
    def __init__(self):
        self.items = [
            {
                "item_id": 100,
                "shipment_id": 70,
                "product_id": "wild1",
                "quantity": 2,
                "author": "FBS 2.0",
                "assembly_tasks": ["10", "11"],
                "status": "new",
                "movement_id": None,
                "retry_count": 0,
                "max_retries": 5,
            },
            {
                "item_id": 101,
                "shipment_id": 70,
                "product_id": "wild1",
                "quantity": 4,
                "author": "FBS 2.0",
                "assembly_tasks": ["11", "12", "13", "14"],
                "status": "new",
                "movement_id": None,
                "retry_count": 0,
                "max_retries": 5,
            },
        ]
        self.states = {"10": False, "11": True, "12": True, "14": False}
        self.saved_results = []
        self.item_updates = {}
        self.shipment_updates = []

    async def lock_items_for_processing(self, conn, *, item_ids):
        return [row for row in self.items if row["item_id"] in item_ids]

    async def lock_assembly_tasks(self, conn, *, assembly_tasks):
        return {task: self.states[task] for task in set(assembly_tasks) if task in self.states}

    async def get_confirmed_task_links(self, conn, *, assembly_tasks, product_id):
        if "11" not in assembly_tasks:
            return {}
        return {
            "11": {
                "existing_success_item_id": 80,
                "existing_movement_id": 800,
                "existing_movement_created_at": datetime(2026, 9, 28, tzinfo=timezone.utc),
            }
        }

    async def get_written_task_occurrences(self, conn, *, item_ids):
        return {}

    async def mark_assembly_tasks_shipped(self, conn, *, assembly_tasks):
        for task in assembly_tasks:
            self.states[task] = True
        return set(assembly_tasks)

    async def upsert_task_results(self, conn, *, results):
        self.saved_results = [dict(result) for result in results]

    async def update_item_task_result(self, conn, **kwargs):
        self.item_updates[kwargs["item_id"]] = kwargs
        return True

    async def update_shipment_status(self, conn, shipment_id):
        self.shipment_updates.append(shipment_id)


@pytest.mark.asyncio
async def test_mixed_group_writes_only_unique_new_tasks_and_keeps_anomalies():
    repo = TaskLevelRepository()
    movement_service = TaskLevelMovementService()

    movement_id = await _process_shipment_group(
        conn=object(),
        product_id="wild1",
        total_quantity=6,
        all_assembly_tasks=["10", "11", "11", "12", "13", "14"],
        author="FBS 2.0",
        movement_service=movement_service,
        shipment_repo=repo,
        item_ids=[100, 101],
    )

    assert movement_id == 901
    assert movement_service.quantities == [2]
    assert {r["task_id"] for r in repo.saved_results if r["outcome"] == "written_off"} == {
        "10",
        "14",
    }
    assert (
        next(r for r in repo.saved_results if r["item_id"] == 101 and r["task_id"] == "11")[
            "outcome"
        ]
        == "duplicate_in_payload"
    )
    assert next(r for r in repo.saved_results if r["task_id"] == "12")["outcome"] == "inconsistent"
    assert next(r for r in repo.saved_results if r["task_id"] == "13")["outcome"] == "not_found"
    assert repo.item_updates[100]["status"] == "success"
    assert repo.item_updates[100]["task_resolution_status"] == "completed_with_duplicates"
    assert repo.item_updates[101]["status"] == "failed"
    assert repo.item_updates[101]["task_resolution_status"] == "partially_completed"
    assert repo.item_updates[101]["movement_id"] is None


@pytest.mark.asyncio
async def test_stock_failure_records_only_new_tasks_as_pending_retry():
    repo = TaskLevelRepository()

    await record_task_level_attempt_failure(
        object(),
        product_id="wild1",
        item_ids=[100, 101],
        shipment_repo=repo,
        outcome="pending_retry",
        error_message="insufficient stock",
        retry_count=1,
        next_retry_at=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
    )

    assert {r["task_id"] for r in repo.saved_results if r["outcome"] == "pending_retry"} == {
        "10",
        "14",
    }
    assert (
        next(r for r in repo.saved_results if r["task_id"] == "11")["outcome"]
        == "duplicate_skipped"
    )
    assert repo.item_updates[100]["status"] == "pending_retry"
    assert repo.item_updates[101]["status"] == "pending_retry"


@pytest.mark.asyncio
async def test_duplicate_only_group_is_safe_noop_without_movement():
    repo = TaskLevelRepository()
    repo.items = [repo.items[0] | {"assembly_tasks": ["11"], "quantity": 1}]
    movement_service = TaskLevelMovementService()

    movement_id = await _process_shipment_group(
        conn=object(),
        product_id="wild1",
        total_quantity=1,
        all_assembly_tasks=["11"],
        author="FBS 2.0",
        movement_service=movement_service,
        shipment_repo=repo,
        item_ids=[100],
    )

    assert movement_id is None
    assert movement_service.quantities == []
    assert repo.item_updates[100]["status"] == "failed"
    assert repo.item_updates[100]["task_resolution_status"] == "duplicate_only"


@pytest.mark.asyncio
async def test_retry_becomes_noop_when_concurrent_attempt_already_finished_item():
    repo = TaskLevelRepository()
    repo.items = [
        repo.items[0] | {"status": "success", "movement_id": 777, "assembly_tasks": ["10"]}
    ]
    movement_service = TaskLevelMovementService()

    movement_id = await _process_shipment_group(
        conn=object(),
        product_id="wild1",
        total_quantity=1,
        all_assembly_tasks=["10"],
        author="FBS 2.0",
        movement_service=movement_service,
        shipment_repo=repo,
        item_ids=[100],
        expected_statuses={"pending_retry"},
    )

    assert movement_id == 777
    assert movement_service.quantities == []
    assert repo.saved_results == []
    assert repo.item_updates == {}


@pytest.mark.asyncio
async def test_retry_preserves_written_off_occurrence_instead_of_overwriting_it():
    repo = TaskLevelRepository()
    repo.items = [repo.items[0] | {"status": "failed", "assembly_tasks": ["10"]}]
    repo.states["10"] = True
    movement_created_at = datetime(2026, 9, 28, tzinfo=timezone.utc)

    async def existing_written(conn, *, item_ids):
        return {
            (100, 0): {
                "task_id": 10,
                "movement_id": 700,
                "movement_created_at": movement_created_at,
                "is_shipped_before": False,
                "reason": "СЗ списано текущим movement",
            }
        }

    repo.get_written_task_occurrences = existing_written
    await record_task_level_attempt_failure(
        object(),
        product_id="wild1",
        item_ids=[100],
        shipment_repo=repo,
        outcome="failed",
        error_message="retry error",
    )

    result = repo.saved_results[0]
    assert result["outcome"] == "written_off"
    assert result["effect_quantity"] == 1
    assert result["movement_id"] == 700
    assert result["movement_created_at"] == movement_created_at
    assert repo.item_updates[100]["status"] == "success"
    assert repo.item_updates[100]["movement_id"] == 700


class ApiAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class ApiPool:
    def acquire(self):
        return ApiAcquire()


@pytest.mark.asyncio
async def test_task_results_endpoint_returns_summary_and_stable_movement_identity(monkeypatch):
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)

    class ApiRepository:
        async def get_shipment_by_id(self, conn, shipment_id):
            return {"shipment_id": shipment_id}

        async def get_task_results(self, conn, **kwargs):
            row = {
                "result_id": 1,
                "shipment_id": 70,
                "item_id": 100,
                "occurrence_index": 0,
                "task_id": 10,
                "product_id": "wild1",
                "outcome": "written_off",
                "effect_quantity": 1,
                "movement_id": 901,
                "movement_created_at": now,
                "existing_success_item_id": None,
                "existing_movement_id": None,
                "existing_movement_created_at": None,
                "is_shipped_before": False,
                "reason": "Списано",
                "attempt_count": 1,
                "first_processed_at": now,
                "last_processed_at": now,
                "last_error": None,
                "created_at": now,
                "updated_at": now,
            }
            summary = {
                "total_tasks": 1,
                "written_off": 1,
                "duplicate_skipped": 0,
                "inconsistent": 0,
                "not_found": 0,
                "pending_retry": 0,
                "failed": 0,
                "effect_quantity": 1,
            }
            return [row], 1, summary

    monkeypatch.setattr(endpoint, "FbsShipmentRepository", ApiRepository)
    response = await endpoint.get_shipment_task_results(
        shipment_id=70,
        product_id=None,
        outcome=None,
        task_id=None,
        limit=200,
        offset=0,
        pool=ApiPool(),
    )

    assert response.summary.effect_quantity == 1
    assert response.summary.requires_reconciliation is False
    assert response.items[0].movement_id == 901
    assert response.items[0].movement_created_at == now


def test_task_results_route_is_read_only_and_registered():
    route = next(
        route for route in endpoint.router.routes if route.path == "/{shipment_id}/task-results"
    )
    assert route.methods == {"GET"}


def test_confirmed_duplicate_requires_real_unambiguous_movement():
    assert "JOIN wms.movements AS movement" in repository.GET_CONFIRMED_TASK_LINKS
    assert "HAVING count(*) = 1" in repository.GET_CONFIRMED_TASK_LINKS
    assert "movement.created_at = result.movement_created_at" in (
        repository.GET_CONFIRMED_TASK_LINKS
    )
