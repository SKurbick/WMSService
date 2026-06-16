import json
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import fbs_shipments as endpoint
from app import consumer
from app.consumer import consume_fbs_queue, start_consumer, start_external_fbs_consumer
from app.core.enums import FbsShipmentSource
from app.core.exceptions import AssemblyTasksAlreadyProcessedError, FbsShipmentItemsUpdateError
from app.shared.config import settings
from app.handlers.write_off_fbs_handler import (
    MARK_ASSEMBLY_TASKS_SHIPPED,
    _process_shipment_group,
    validate_assembly_tasks,
)
from app.infrastructure.database.repositories import fbs_shipment_repository as repository


class AssemblyTaskConnection:
    def __init__(self, updated_ids):
        self.updated_ids = updated_ids
        self.fetch_calls = 0

    async def fetch(self, query, task_ids):
        self.fetch_calls += 1
        if query == MARK_ASSEMBLY_TASKS_SHIPPED:
            return [{"task_id": task_id} for task_id in self.updated_ids]
        return [{"task_id": task_id, "is_shipped": False} for task_id in task_ids]


class FakeMovementService:
    def __init__(self):
        self.calls = 0

    async def create_movement_in_transaction(self, conn, movements):
        self.calls += 1
        return [SimpleNamespace(movement_id=501)]


class FakeShipmentRepository:
    def __init__(self, updated_item_ids):
        self.updated_item_ids = updated_item_ids

    async def mark_items_success_in_transaction(self, conn, **kwargs):
        return self.updated_item_ids


@pytest.mark.asyncio
async def test_claims_all_assembly_tasks_before_movement():
    await validate_assembly_tasks(["10", "11"], AssemblyTaskConnection([10, 11]))


@pytest.mark.asyncio
async def test_partial_assembly_task_claim_blocks_movement():
    with pytest.raises(AssemblyTasksAlreadyProcessedError):
        await validate_assembly_tasks(["10", "11"], AssemblyTaskConnection([10]))


@pytest.mark.asyncio
async def test_partial_claim_does_not_create_movement():
    movement_service = FakeMovementService()
    with pytest.raises(AssemblyTasksAlreadyProcessedError):
        await _process_shipment_group(
            conn=AssemblyTaskConnection([10]),
            product_id="SKU-1",
            total_quantity=2,
            all_assembly_tasks=["10", "11"],
            author="test",
            movement_service=movement_service,
            shipment_repo=FakeShipmentRepository([100]),
            item_ids=[100],
        )
    assert movement_service.calls == 0


@pytest.mark.asyncio
async def test_successful_group_marks_all_items_with_created_movement():
    movement_service = FakeMovementService()
    movement_id = await _process_shipment_group(
        conn=AssemblyTaskConnection([10, 11]),
        product_id="SKU-1",
        total_quantity=2,
        all_assembly_tasks=["10", "11"],
        author="test",
        movement_service=movement_service,
        shipment_repo=FakeShipmentRepository([100, 101]),
        item_ids=[100, 101],
    )
    assert movement_id == 501
    assert movement_service.calls == 1


@pytest.mark.asyncio
async def test_disabled_assembly_task_validation_skips_public_assembly_task(monkeypatch):
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", False)
    conn = AssemblyTaskConnection([])
    movement_service = FakeMovementService()

    movement_id = await _process_shipment_group(
        conn=conn,
        product_id="SKU-1",
        total_quantity=2,
        all_assembly_tasks=["missing-10", "missing-11"],
        author="test",
        movement_service=movement_service,
        shipment_repo=FakeShipmentRepository([100, 101]),
        item_ids=[100, 101],
    )

    assert movement_id == 501
    assert movement_service.calls == 1
    assert conn.fetch_calls == 0


@pytest.mark.asyncio
async def test_duplicate_assembly_tasks_are_rejected():
    with pytest.raises(Exception, match="дубли"):
        await validate_assembly_tasks(["10", "10"], AssemblyTaskConnection([10]))


@pytest.mark.asyncio
async def test_missing_item_update_raises_and_forces_group_transaction_rollback():
    with pytest.raises(FbsShipmentItemsUpdateError):
        await _process_shipment_group(
            conn=AssemblyTaskConnection([10]),
            product_id="SKU-1",
            total_quantity=1,
            all_assembly_tasks=["10"],
            author="test",
            movement_service=FakeMovementService(),
            shipment_repo=FakeShipmentRepository([]),
            item_ids=[100],
        )


def test_source_sql_and_atomic_item_update_contract():
    assert "source" in repository.CREATE_SHIPMENT
    assert "source = $4" in repository.GET_SHIPMENTS
    assert "source = $1" in repository.GET_SHIPMENTS_STATS
    assert "WHERE item_id = ANY($2::bigint[])" in repository.MARK_ITEMS_SUCCESS
    assert "RETURNING item_id" in repository.MARK_ITEMS_SUCCESS
    assert "error_message = NULL" in repository.MARK_ITEMS_SUCCESS
    assert "next_retry_at = NULL" in repository.MARK_ITEMS_SUCCESS


def test_fbs_consumers_are_thin_adapters_for_distinct_sources():
    assert "RABBITMQ_QUEUE" in start_consumer.__code__.co_names
    assert "STANDARD" in start_consumer.__code__.co_names
    assert "EXTERNAL_FBS_QUEUE" in start_external_fbs_consumer.__code__.co_names
    assert "EXTERNAL_DETECTED" in start_external_fbs_consumer.__code__.co_names


def test_manual_item_retry_route_is_registered_before_dynamic_shipment_route():
    paths = [route.path for route in endpoint.router.routes]
    assert "/items/{item_id}/retry" in paths
    assert paths.index("/items/{item_id}/retry") < paths.index("/{shipment_id}")


class FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class FakeMessage:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.acked = False

    async def ack(self):
        self.acked = True

    async def nack(self, requeue):
        raise AssertionError("unexpected nack")


class FakeQueue:
    def __init__(self, message):
        self.message = message

    def __aiter__(self):
        async def iterator():
            yield self.message

        return iterator()


class FakeRabbitConnection:
    def __init__(self, queue):
        self.queue = queue

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def channel(self):
        return self

    async def declare_queue(self, queue_name, passive):
        return self.queue


class RecordingShipmentRepository:
    def __init__(self):
        self.sources = []

    async def create_shipment(self, conn, **kwargs):
        self.sources.append(kwargs["source"])
        return 700


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", [FbsShipmentSource.STANDARD, FbsShipmentSource.EXTERNAL_DETECTED]
)
async def test_consumer_creates_shipment_with_adapter_source(monkeypatch, source):
    payload = [
        {
            "author": "producer",
            "supply_id": "SUP-1",
            "product_id": "SKU-1",
            "warehouse_id": 1,
            "delivery_type": "fbs",
            "account": "a",
            "quantity": 1,
            "assembly_tasks": ["10"],
        }
    ]
    message = FakeMessage(payload)
    recording_repo = RecordingShipmentRepository()

    async def fake_connect(url):
        return FakeRabbitConnection(FakeQueue(message))

    async def fake_get_pool():
        return FakePool()

    async def fake_handle(*args, **kwargs):
        return 700

    monkeypatch.setattr(consumer.aio_pika, "connect_robust", fake_connect)
    monkeypatch.setattr(consumer, "get_db_pool", fake_get_pool)
    monkeypatch.setattr(consumer, "handle_write_off_fbs", fake_handle)
    monkeypatch.setattr(consumer, "fbs_shipment_repo", recording_repo)

    await consume_fbs_queue(queue_name="queue", source=source)

    assert recording_repo.sources == [source.value]
    assert message.acked is True
