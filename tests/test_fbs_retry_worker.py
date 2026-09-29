import json

import pytest

from app import retry_worker
from app.shared.config import settings


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def transaction(self):
        return FakeTransaction()


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConnection()

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeShipmentRepository:
    def __init__(self, stored_tasks):
        self.rows = [
            {
                "item_id": 10,
                "shipment_id": 20,
                "product_id": "wild163",
                "quantity": 2,
                "author": "FBS 2.0",
                "supply_id": "SUP-1",
                "account": "ACCOUNT-1",
                "assembly_tasks": stored_tasks,
                "warehouse_id": 1,
                "delivery_type": "FBS",
                "wb_warehouse": None,
                "shipment_date": None,
                "retry_count": 0,
                "max_retries": 5,
            }
        ]
        self.status_updates = []
        self.shipment_updates = []

    async def get_pending_retry_items(self, conn):
        return self.rows

    async def update_item_status(self, conn, **kwargs):
        self.status_updates.append(kwargs)

    async def update_shipment_status(self, conn, shipment_id):
        self.shipment_updates.append(shipment_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("as_json_string", [False, True])
async def test_retry_worker_normalizes_jsonb_assembly_tasks(monkeypatch, as_json_string):
    tasks = ["5850557887", "5850628387"]
    stored_tasks = json.dumps(tasks) if as_json_string else list(tasks)
    repo = FakeShipmentRepository(stored_tasks)
    calls = []

    async def fake_process_shipment_group(**kwargs):
        calls.append(kwargs)
        return 9001

    monkeypatch.setattr(retry_worker, "FbsShipmentRepository", lambda: repo)
    monkeypatch.setattr(retry_worker, "MovementRepository", lambda pool: object())
    monkeypatch.setattr(retry_worker, "LocationRepository", lambda pool: object())
    monkeypatch.setattr(retry_worker, "MovementService", lambda *args: object())
    monkeypatch.setattr(retry_worker, "_process_shipment_group", fake_process_shipment_group)

    await retry_worker.process_pending_retries(FakePool())

    assert len(calls) == 1
    assert calls[0]["all_assembly_tasks"] == tasks
    assert calls[0]["total_quantity"] == 2
    assert calls[0]["item_ids"] == [10]
    assert repo.status_updates == []
    assert repo.shipment_updates == [20]


@pytest.mark.asyncio
async def test_retry_worker_marks_only_invalid_jsonb_group_failed(monkeypatch):
    repo = FakeShipmentRepository('["5850557887", "invalid"]')
    calls = []

    async def fake_process_shipment_group(**kwargs):
        calls.append(kwargs)
        return 9001

    monkeypatch.setattr(settings, "FBS_TASK_PROCESSING_MODE", "legacy")
    monkeypatch.setattr(retry_worker, "FbsShipmentRepository", lambda: repo)
    monkeypatch.setattr(retry_worker, "MovementRepository", lambda pool: object())
    monkeypatch.setattr(retry_worker, "LocationRepository", lambda pool: object())
    monkeypatch.setattr(retry_worker, "MovementService", lambda *args: object())
    monkeypatch.setattr(retry_worker, "_process_shipment_group", fake_process_shipment_group)

    await retry_worker.process_pending_retries(FakePool())

    assert calls == []
    assert len(repo.status_updates) == 1
    assert repo.status_updates[0]["item_id"] == 10
    assert repo.status_updates[0]["status"] == "failed"
    assert "invalid literal for int()" in repo.status_updates[0]["error_message"]
    assert repo.shipment_updates == [20]
