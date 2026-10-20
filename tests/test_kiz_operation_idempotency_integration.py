"""PostgreSQL tests for KIZ Stage 2A Phase 3."""

import asyncio
from decimal import Decimal

import asyncpg
import pytest

from app.core.exceptions import KizOperationIdempotencyConflictError
from app.core.kiz_operation_idempotency import KizOperationDisposition
from app.core.services.kiz_operation_idempotency_service import (
    KizOperationIdempotencyService,
)
from app.infrastructure.database.repositories.kiz_operation_repository import (
    KizOperationRepository,
)


@pytest.fixture
def idempotency_service():
    return KizOperationIdempotencyService(KizOperationRepository())


def intent(*, product="sku", quantity="1", operation_type="transfer"):
    item = {
        "external_line_id": "line-1",
        "product_id": product,
        "from_location": "A",
        "quantity": Decimal(quantity),
        "kiz_codes": ["KIZ-A"],
    }
    if operation_type == "transfer":
        item["to_location"] = "B"
    return {"operation_type": operation_type, "items": [item]}


async def acquire(service, conn, **overrides):
    values = {
        "operation_type": "transfer",
        "source_system": "manual",
        "external_operation_id": "external-1",
        "author": "tester",
        "intent": intent(),
    }
    values.update(overrides)
    return await service.acquire(conn, **values)


async def create_synthetic_movement(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('phase3-sku','Phase 3 SKU')"
        )
        location_id = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES ('PHASE3',0) RETURNING location_id"
        )
        movement = await conn.fetchrow(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','phase3-sku',$1,1)
               RETURNING movement_id,created_at""",
            location_id,
        )
        movement_ref = await conn.fetchval(
            """SELECT movement_ref FROM wms.movement_registry
               WHERE movement_id=$1 AND movement_created_at=$2""",
            movement["movement_id"], movement["created_at"],
        )
    return movement_ref


async def wait_blocked(pool, pid):
    async with pool.acquire() as observer:
        for _ in range(200):
            if await observer.fetchval(
                "SELECT cardinality(pg_blocking_pids($1)) > 0", pid
            ):
                return
            await asyncio.sleep(0.01)
    pytest.fail("Expected idempotency unique-key lock wait")


async def test_database_identity_and_type_constraints(kiz_pool):
    valid = (
        "'transfer','manual','external','" + "a" * 64 + "','tester','{}'::jsonb"
    )
    async with kiz_pool.acquire() as conn:
        operation_id = await conn.fetchval(
            f"""INSERT INTO wms.kiz_operations(
                operation_type,source_system,external_operation_id,
                request_fingerprint,author,result_payload
            ) VALUES ({valid}) RETURNING operation_id"""
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                f"""INSERT INTO wms.kiz_operations(
                    operation_type,source_system,external_operation_id,
                    request_fingerprint,author,result_payload
                ) VALUES ({valid})"""
            )
        await conn.execute(
            """INSERT INTO wms.kiz_operations(
                operation_type,source_system,external_operation_id,
                request_fingerprint,author,result_payload
            ) VALUES ('ship','manual','external',$1,'tester','{}')""",
            "b" * 64,
        )
        await conn.execute(
            """INSERT INTO wms.kiz_operations(
                operation_type,source_system,external_operation_id,
                request_fingerprint,author,result_payload
            ) VALUES ('transfer','scanner','external',$1,'tester','{}')""",
            "c" * 64,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """INSERT INTO wms.kiz_operations(
                    operation_type,source_system,external_operation_id,
                    request_fingerprint,author
                ) VALUES ('receive','manual','bad',$1,'tester')""",
                "d" * 64,
            )
        await conn.execute(
            """INSERT INTO wms.kiz_operation_items(operation_id,external_line_id)
               VALUES ($1,'line-1')""",
            operation_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO wms.kiz_operation_items(operation_id,external_line_id)
                   VALUES ($1,'line-1')""",
                operation_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """INSERT INTO wms.kiz_operation_items(
                       operation_id,external_line_id,movement_ref
                   ) VALUES ($1,'unknown-movement',9999999999)""",
                operation_id,
            )


async def test_exact_replay_returns_stored_result(kiz_pool, idempotency_service):
    async with kiz_pool.acquire() as conn:
        async with conn.transaction():
            first = await acquire(idempotency_service, conn)
            assert first.disposition is KizOperationDisposition.NEW
            await idempotency_service.store_successful_result(
                conn,
                operation_id=first.operation["operation_id"],
                result_payload={"operation_id": first.operation["operation_id"], "ok": True},
            )
        async with conn.transaction():
            replay = await acquire(idempotency_service, conn)
            assert replay.disposition is KizOperationDisposition.REPLAY
            assert replay.operation["result_payload"]["ok"] is True
            assert await idempotency_service.load_stored_result(
                conn, operation_id=replay.operation["operation_id"]
            ) == replay.operation["result_payload"]
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links") == 0


async def test_same_key_with_another_fingerprint_is_conflict(
    kiz_pool, idempotency_service,
):
    async with kiz_pool.acquire() as conn:
        async with conn.transaction():
            first = await acquire(idempotency_service, conn)
            await idempotency_service.store_successful_result(
                conn, operation_id=first.operation["operation_id"], result_payload={"ok": True}
            )
        async with conn.transaction():
            with pytest.raises(KizOperationIdempotencyConflictError) as error:
                await acquire(
                    idempotency_service,
                    conn,
                    intent=intent(product="other"),
                )
            assert error.value.operation_id == first.operation["operation_id"]


@pytest.mark.parametrize("conflicting", [False, True])
async def test_concurrent_replay_uses_unique_row_lock(
    kiz_pool, idempotency_service, conflicting,
):
    async with kiz_pool.acquire() as first_conn, kiz_pool.acquire() as second_conn:
        async with first_conn.transaction():
            winner = await acquire(idempotency_service, first_conn)
            await idempotency_service.store_successful_result(
                first_conn,
                operation_id=winner.operation["operation_id"],
                result_payload={"winner": True},
            )

            async def contender():
                async with second_conn.transaction():
                    return await acquire(
                        idempotency_service,
                        second_conn,
                        intent=intent(product="other") if conflicting else intent(),
                    )

            task = asyncio.create_task(contender())
            try:
                await wait_blocked(kiz_pool, second_conn.get_server_pid())
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise

        if conflicting:
            with pytest.raises(KizOperationIdempotencyConflictError):
                await task
        else:
            replay = await task
            assert replay.disposition is KizOperationDisposition.REPLAY
            assert replay.operation["result_payload"] == {"winner": True}

    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1


@pytest.mark.parametrize("rollback_after", ["intent", "item", "movement", "result"])
async def test_rollback_leaves_key_available(
    kiz_pool, idempotency_service, rollback_after,
):
    movement_ref = await create_synthetic_movement(kiz_pool)
    with pytest.raises(RuntimeError, match="fault injection"):
        async with kiz_pool.acquire() as conn:
            async with conn.transaction():
                operation = await acquire(idempotency_service, conn)
                if rollback_after != "intent":
                    item = await idempotency_service.create_item(
                        conn,
                        operation_id=operation.operation["operation_id"],
                        external_line_id="line-1",
                    )
                if rollback_after in {"movement", "result"}:
                    await idempotency_service.attach_movement(
                        conn,
                        operation_item_id=item["operation_item_id"],
                        movement_ref=movement_ref,
                    )
                if rollback_after == "result":
                    await idempotency_service.store_successful_result(
                        conn,
                        operation_id=operation.operation["operation_id"],
                        result_payload={"ok": True},
                    )
                raise RuntimeError("fault injection")

    async with kiz_pool.acquire() as conn:
        async with conn.transaction():
            retry = await acquire(idempotency_service, conn)
            assert retry.disposition is KizOperationDisposition.NEW
            await idempotency_service.store_successful_result(
                conn, operation_id=retry.operation["operation_id"], result_payload={"retry": True}
            )
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operation_items") == 0


async def test_item_movement_is_one_time_and_unique(kiz_pool, idempotency_service):
    movement_ref = await create_synthetic_movement(kiz_pool)
    async with kiz_pool.acquire() as conn:
        async with conn.transaction():
            operation = await acquire(idempotency_service, conn)
            first = await idempotency_service.create_item(
                conn,
                operation_id=operation.operation["operation_id"],
                external_line_id="line-1",
            )
            second = await idempotency_service.create_item(
                conn,
                operation_id=operation.operation["operation_id"],
                external_line_id="line-2",
            )
            await idempotency_service.attach_movement(
                conn,
                operation_item_id=first["operation_item_id"],
                movement_ref=movement_ref,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await idempotency_service.attach_movement(
                    conn,
                    operation_item_id=second["operation_item_id"],
                    movement_ref=movement_ref,
                )


async def test_service_requires_caller_transaction_and_valid_json(
    kiz_pool, idempotency_service,
):
    async with kiz_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="open transaction"):
            await acquire(idempotency_service, conn)
        async with conn.transaction():
            operation = await acquire(idempotency_service, conn)
            with pytest.raises(ValueError, match="NUL"):
                await idempotency_service.store_successful_result(
                    conn,
                    operation_id=operation.operation["operation_id"],
                    result_payload={"bad": "value\x00"},
                )
            await idempotency_service.store_successful_result(
                conn,
                operation_id=operation.operation["operation_id"],
                result_payload={"ok": True},
            )


async def test_intent_without_result_cannot_commit(kiz_pool, idempotency_service):
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError, match="store its result"):
            async with conn.transaction():
                await acquire(idempotency_service, conn)
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_operations") == 0
