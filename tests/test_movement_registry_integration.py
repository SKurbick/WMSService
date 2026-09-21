"""Real PostgreSQL coverage for KIZ Stage 2A Phase 1 movement identity."""

import asyncio

import asyncpg
import pytest

from app.core.services.movement_service import MovementService
from app.core.services.task_service import TaskService
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository


async def seed_scope(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('registry-sku','Registry SKU')"
        )
        source = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('REGISTRY-SOURCE',0) "
            "RETURNING location_id,location_code"
        )
        destination = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('REGISTRY-DEST',0) "
            "RETURNING location_id,location_code"
        )
    return dict(source), dict(destination)


async def registry_integrity(conn):
    return await conn.fetchrow("SELECT * FROM wms.check_movement_registry_integrity()")


async def test_trigger_registers_exact_identity_and_constraints_protect_it(kiz_pool):
    source, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        movement = await conn.fetchrow(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','registry-sku',$1,2)
               RETURNING movement_id,created_at""",
            source["location_id"],
        )
        registry = await conn.fetchrow(
            """SELECT * FROM wms.movement_registry
               WHERE movement_id=$1 AND movement_created_at=$2""",
            movement["movement_id"],
            movement["created_at"],
        )
        assert registry["movement_ref"] > 0

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO wms.movements(
                       movement_id,movement_type,product_id,to_location_id,quantity,created_at
                   ) VALUES ($1,'receive','registry-sku',$2,1,$3)""",
                movement["movement_id"], source["location_id"], movement["created_at"],
            )
        assert await conn.fetchval(
            "SELECT quantity FROM wms.inventory WHERE product_id='registry-sku'"
        ) == 2

        with pytest.raises(asyncpg.PostgresError) as immutable:
            await conn.execute(
                "UPDATE wms.movement_registry SET movement_id=movement_id WHERE movement_ref=$1",
                registry["movement_ref"],
            )
        assert immutable.value.sqlstate == "55000"

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "DELETE FROM wms.movements WHERE movement_id=$1 AND created_at=$2",
                movement["movement_id"], movement["created_at"],
            )


async def test_registry_error_rolls_back_movement_and_inventory(kiz_pool):
    source, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        await conn.execute("""
            CREATE FUNCTION wms.test_reject_registry_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'registry fault injection'; END $$;
            CREATE TRIGGER trg_test_reject_registry_insert
            BEFORE INSERT ON wms.movement_registry
            FOR EACH ROW EXECUTE FUNCTION wms.test_reject_registry_insert();
        """)
        with pytest.raises(asyncpg.RaiseError, match="registry fault injection"):
            await conn.execute(
                """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
                   VALUES ('receive','registry-sku',$1,2)""",
                source["location_id"],
            )
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.movements WHERE product_id='registry-sku'"
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.inventory WHERE product_id='registry-sku'"
        ) == 0


async def test_concurrent_backfill_and_new_inserts_reach_complete_coverage(kiz_pool):
    source, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','registry-sku',$1,1)""",
            [(source["location_id"],) for _ in range(18)],
        )
        await conn.execute(
            "ALTER TABLE wms.movement_registry DISABLE TRIGGER "
            "trg_movement_registry_immutable"
        )
        await conn.execute("""
            DELETE FROM wms.movement_registry
            WHERE movement_ref IN (
                SELECT movement_ref FROM wms.movement_registry
                ORDER BY movement_ref LIMIT 12
            )
        """)
        await conn.execute(
            "ALTER TABLE wms.movement_registry ENABLE TRIGGER "
            "trg_movement_registry_immutable"
        )

    async def backfill_worker():
        async with kiz_pool.acquire() as conn:
            while await conn.fetchval("SELECT wms.backfill_movement_registry(2)"):
                await asyncio.sleep(0)

    async def insert_worker():
        async with kiz_pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
                   VALUES ('receive','registry-sku',$1,1)""",
                [(source["location_id"],) for _ in range(12)],
            )

    await asyncio.gather(backfill_worker(), backfill_worker(), insert_worker())

    async with kiz_pool.acquire() as conn:
        integrity = await registry_integrity(conn)
        assert dict(integrity) == {
            "movement_rows": 30,
            "registry_rows": 30,
            "missing_registry_rows": 0,
            "orphan_registry_rows": 0,
            "duplicate_movement_coordinates": 0,
            "duplicate_registry_coordinates": 0,
            "is_complete": True,
        }
        assert await conn.fetchval(
            "SELECT count(DISTINCT movement_ref) FROM wms.movement_registry"
        ) == 30


async def test_legacy_container_database_writers_are_removed(kiz_pool):
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT to_regprocedure('wms.unpack_from_container(character varying,character varying,numeric)')"
        ) is None
        assert await conn.fetchval(
            "SELECT to_regprocedure('wms.move_container_inventory()')"
        ) is None
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid='wms.containers'::regclass "
            "AND tgname='trg_move_container_inventory' AND NOT tgisinternal"
        ) == 0

async def test_task_completion_movement_writer_is_registered(kiz_pool):
    source, destination = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','registry-sku',$1,2)""",
            source["location_id"],
        )

    class TaskItems:
        async def get_items_for_movements(self, task_id):
            assert task_id == 42
            return [{
                "product_id": "registry-sku",
                "from_location_code": source["location_code"],
                "quantity_actual": 2,
                "batch_number": None,
            }]

    movement_service = MovementService(
        MovementRepository(kiz_pool), LocationRepository(kiz_pool)
    )
    task_service = TaskService(TaskItems(), None, movement_service, None)
    await task_service._create_movements_for_task(42, destination["location_code"], 7)

    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.movements WHERE reason='Task #42'"
        ) == 1
        assert (await registry_integrity(conn))["is_complete"] is True
