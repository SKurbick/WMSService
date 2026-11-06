"""Regression: one shipment/product may ship multiple disjoint task batches."""

import asyncpg
import pytest

from app.handlers import write_off_fbs_handler as handler
from app.infrastructure.database.repositories.fbs_kiz_repository import FbsKizRepository
from app.shared.config import settings
from test_fbs_kiz_stage1 import FakeRepository, binding
from test_fbs_task_level_processing import TaskLevelMovementService, TaskLevelRepository


class OperationConnection:
    def __init__(self):
        self.operations = {}

    async def fetchrow(self, query, external_id, fingerprint, author):
        if external_id in self.operations:
            raise asyncpg.UniqueViolationError("duplicate operation identity")
        row = {
            "operation_id": len(self.operations) + 1,
            "request_fingerprint": fingerprint,
        }
        self.operations[external_id] = row
        return row


def unit(task, code=None, location=10):
    return {"task_id": task, "kiz_code": code, "source_location_id": location}


@pytest.mark.asyncio
async def test_batch_key_is_stable_but_distinguishes_new_tasks():
    repo = FbsKizRepository()
    first = OperationConnection()
    reordered = OperationConnection()
    original = [unit("10", "K10"), unit("11", "K11")]
    await repo.create_operation(
        first, shipment_id=70, product_id="wild1", author="FBS 2.0", tasks=original
    )
    await repo.create_operation(
        reordered,
        shipment_id=70,
        product_id="wild1",
        author="retry",
        tasks=list(reversed(original)),
    )
    assert first.operations == reordered.operations
    await repo.create_operation(
        first,
        shipment_id=70,
        product_id="wild1",
        author="retry",
        tasks=[unit("12", "K12"), unit("13", "K13")],
    )
    assert len(first.operations) == 2
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_operation(
            first, shipment_id=70, product_id="wild1", author="retry", tasks=original
        )


@pytest.mark.asyncio
async def test_partial_pipeline_retry_preserves_prior_effects(monkeypatch):
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    monkeypatch.setattr(settings, "FBS_TASK_PROCESSING_MODE", "task_level")
    conn = OperationConnection()

    class KizRepository(FakeRepository):
        async def create_operation(self, connection, **kwargs):
            return await FbsKizRepository().create_operation(connection, **kwargs)

    kiz = KizRepository(
        {
            str(t): binding(str(t), f"K{t}", "registered")
            | {"product_id": "wild1", "kiz_product_id": "wild1"}
            for t in (10, 11, 12, 13)
        }
    )
    monkeypatch.setattr(handler, "FbsKizRepository", lambda: kiz)

    class ShipmentRepository(TaskLevelRepository):
        async def has_kiz_bindings(self, connection, *, assembly_tasks):
            return bool(assembly_tasks)

        async def get_confirmed_task_links(self, connection, **kwargs):
            return {}

        async def get_written_task_occurrences(self, connection, *, item_ids):
            return {
                (r["item_id"], r["occurrence_index"]): r
                for r in self.saved_results
                if r["outcome"] == "written_off"
            }

    shipments = ShipmentRepository()
    shipments.items = [
        shipments.items[0] | {"assembly_tasks": ["10", "11", "12", "13"], "quantity": 4}
    ]
    shipments.states = {"10": False, "11": False}

    async def process():
        return await handler._process_shipment_group(
            conn=conn,
            product_id="wild1",
            total_quantity=4,
            all_assembly_tasks=["10", "11", "12", "13"],
            author="FBS 2.0",
            movement_service=TaskLevelMovementService(),
            shipment_repo=shipments,
            item_ids=[100],
        )

    await process()
    assert [r["outcome"] for r in shipments.saved_results] == [
        "written_off",
        "written_off",
        "not_found",
        "not_found",
    ]
    original_movement = shipments.saved_results[0]["movement_id"]
    shipments.states.update({"12": False, "13": False})
    await process()
    assert len(conn.operations) == 2
    assert [m["quantity"] for m in kiz.movements] == [2, 2]
    assert shipments.saved_results[0]["movement_id"] == original_movement
    assert all(r["outcome"] == "written_off" for r in shipments.saved_results)
    assert shipments.item_updates[100]["status"] == "success"
    await process()
    assert len(conn.operations) == 2
    assert [m["quantity"] for m in kiz.movements] == [2, 2]
    assert len(kiz.shipped) == 4


async def test_postgres_batches_coexist_with_legacy_operation(kiz_pool):
    repo = FbsKizRepository()
    async with kiz_pool.acquire() as conn:
        legacy = await conn.fetchval(
            """INSERT INTO wms.kiz_operations(
                operation_type,source_system,external_operation_id,
                request_fingerprint,author,result_payload
            ) VALUES ('ship','fbs','fbs:70:wild1',$1,'FBS 2.0','{}')
            RETURNING operation_id""",
            "a" * 64,
        )
        async with conn.transaction():
            first = await repo.create_operation(
                conn,
                shipment_id=70,
                product_id="wild1",
                author="FBS 2.0",
                tasks=[unit("12", "K12"), unit("13", "K13")],
            )
            await repo.complete_operation(conn, first, [])
        async with conn.transaction():
            second = await repo.create_operation(
                conn,
                shipment_id=70,
                product_id="wild1",
                author="FBS 2.0",
                tasks=[unit("14", "K14")],
            )
            await repo.complete_operation(conn, second, [])
        assert len({legacy, first, second}) == 3
        with pytest.raises(asyncpg.UniqueViolationError):
            await repo.create_operation(
                conn,
                shipment_id=70,
                product_id="wild1",
                author="retry",
                tasks=[unit("13", "K13"), unit("12", "K12")],
            )
        assert (
            await conn.fetchval(
                "SELECT external_operation_id FROM wms.kiz_operations WHERE operation_id=$1", legacy
            )
            == "fbs:70:wild1"
        )
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 3
        # An interrupted batch leaves no identity behind; the same batch can retry.
        with pytest.raises(RuntimeError, match="rollback batch"):
            async with conn.transaction():
                await repo.create_operation(
                    conn,
                    shipment_id=70,
                    product_id="wild1",
                    author="FBS 2.0",
                    tasks=[unit("15", "K15")],
                )
                raise RuntimeError("rollback batch")
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 3
        async with conn.transaction():
            retried = await repo.create_operation(
                conn,
                shipment_id=70,
                product_id="wild1",
                author="FBS 2.0",
                tasks=[unit("15", "K15")],
            )
            await repo.complete_operation(conn, retried, [])
