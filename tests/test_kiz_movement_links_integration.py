"""Real PostgreSQL coverage for KIZ Stage 2A Phase 2 infrastructure."""

from datetime import datetime, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.core.kiz_errors import KizGuardError
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers


async def seed_scope(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('phase2-sku','Phase 2 SKU')"
        )
        first = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('PHASE2-A',0) "
            "RETURNING location_id,location_code"
        )
        second = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('PHASE2-B',0) "
            "RETURNING location_id,location_code"
        )
        await conn.executemany(
            "INSERT INTO wms.inventory(product_id,location_id,quantity,status,batch_number,container_code) "
            "VALUES ('phase2-sku',$1,10,'available',NULL,NULL)",
            [(first["location_id"],), (second["location_id"],)],
        )
    return dict(first), dict(second)


async def insert_kiz(conn, code, location_id, status="active"):
    return await conn.fetchval(
        """INSERT INTO wms.kiz(
               kiz_code,product_id,location_id,lifecycle_status,closed_at,created_by
           ) VALUES ($1,'phase2-sku',$2,$3::varchar,
                     CASE WHEN $3::varchar='active' THEN NULL ELSE now() END,'test')
           RETURNING kiz_id""",
        code, location_id, status,
    )


async def insert_movement(conn, location_id, quantity=1):
    movement = await conn.fetchrow(
        """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
           VALUES ('receive','phase2-sku',$1,$2)
           RETURNING movement_id,created_at""",
        location_id, quantity,
    )
    return await conn.fetchval(
        """SELECT movement_ref FROM wms.movement_registry
           WHERE movement_id=$1 AND movement_created_at=$2""",
        movement["movement_id"], movement["created_at"],
    )


@pytest.fixture
async def phase2_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_association_cardinality_uniqueness_and_foreign_keys(kiz_pool):
    first, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        kiz_one = await insert_kiz(conn, "phase2-one", first["location_id"])
        kiz_two = await insert_kiz(conn, "phase2-two", first["location_id"])
        movement_one = await insert_movement(conn, first["location_id"])
        movement_two = await insert_movement(conn, first["location_id"])

        await conn.executemany(
            "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
            [(kiz_one, movement_one), (kiz_two, movement_one), (kiz_one, movement_two)],
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_movement_links WHERE movement_ref=$1",
            movement_one,
        ) == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_movement_links WHERE kiz_id=$1",
            kiz_one,
        ) == 2

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
                kiz_one, movement_one,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
                9_999_999_999, movement_one,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
                kiz_one, 9_999_999_999,
            )


@pytest.mark.parametrize("statement", [
    "UPDATE wms.kiz_movement_links SET movement_ref=movement_ref",
    "DELETE FROM wms.kiz_movement_links",
])
async def test_association_is_immutable(kiz_pool, statement):
    first, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        kiz_id = await insert_kiz(conn, "phase2-link", first["location_id"])
        movement_ref = await insert_movement(conn, first["location_id"])
        await conn.execute(
            "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
            kiz_id, movement_ref,
        )
        with pytest.raises(asyncpg.PostgresError) as error:
            await conn.execute(statement)
        assert error.value.sqlstate == "55000"


@pytest.mark.parametrize(
    "status,location_kind,closed,valid",
    [
        ("active", "present", False, True),
        ("active", "null", False, False),
        ("active", "present", True, False),
        ("shipped", "null", True, True),
        ("shipped", "present", True, False),
        ("shipped", "null", False, False),
        ("error", "present", True, True),
        ("error", "null", True, True),
        ("deactivated", "present", True, True),
        ("deactivated", "null", True, True),
    ],
)
async def test_lifecycle_location_constraints(kiz_pool, status, location_kind, closed, valid):
    first, _ = await seed_scope(kiz_pool)
    location_id = first["location_id"] if location_kind == "present" else None
    closed_at = datetime.now(timezone.utc) if closed else None
    async with kiz_pool.acquire() as conn:
        query = """INSERT INTO wms.kiz(
                       kiz_code,product_id,location_id,lifecycle_status,closed_at,created_by
                   ) VALUES ('phase2-state','phase2-sku',$1,$2,$3,'test')"""
        if valid:
            await conn.execute(query, location_id, status, closed_at)
        else:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(query, location_id, status, closed_at)


@pytest.mark.parametrize("statement", [
    "UPDATE wms.kiz SET location_id=$1 WHERE kiz_code='phase2-guard'",
    """UPDATE wms.kiz SET lifecycle_status='shipped',location_id=NULL,closed_at=now()
       WHERE kiz_code='phase2-guard' AND $1::bigint IS NOT NULL""",
])
async def test_direct_location_or_shipment_update_remains_forbidden(kiz_pool, statement):
    first, second = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        await insert_kiz(conn, "phase2-guard", first["location_id"])
        with pytest.raises(KizGuardError):
            await conn.execute(statement, second["location_id"])


async def test_shipped_event_requires_link_for_same_kiz(kiz_pool):
    first, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        kiz_id = await insert_kiz(conn, "phase2-event", None, "shipped")
        other_kiz = await insert_kiz(conn, "phase2-other", first["location_id"])
        movement_ref = await insert_movement(conn, first["location_id"])
        await conn.execute(
            "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
            kiz_id, movement_ref,
        )
        event_sql = """INSERT INTO wms.kiz_events(
            kiz_id,event_type,from_status,to_status,product_id,location_id,
            author,metadata,movement_ref
        ) VALUES ($1,'shipped','active','shipped','phase2-sku',$2,'test','{}',$3)"""
        await conn.execute(event_sql, kiz_id, first["location_id"], movement_ref)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(event_sql, other_kiz, first["location_id"], movement_ref)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """INSERT INTO wms.kiz_events(
                    kiz_id,event_type,from_status,to_status,product_id,location_id,author,metadata
                ) VALUES ($1,'shipped','active','shipped','phase2-sku',$2,'test','{}')""",
                kiz_id, first["location_id"],
            )


async def test_shipped_read_models_keep_record_with_null_location(
    kiz_pool, phase2_client,
):
    first, _ = await seed_scope(kiz_pool)
    async with kiz_pool.acquire() as conn:
        kiz_id = await insert_kiz(conn, "phase2-shipped", None, "shipped")
        movement_ref = await insert_movement(conn, first["location_id"])
        await conn.execute(
            "INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref) VALUES ($1,$2)",
            kiz_id, movement_ref,
        )
        await conn.execute(
            """INSERT INTO wms.kiz_events(
                kiz_id,event_type,from_status,to_status,product_id,location_id,
                author,metadata,movement_ref
            ) VALUES ($1,'shipped','active','shipped','phase2-sku',$2,'test','{}',$3)""",
            kiz_id, first["location_id"], movement_ref,
        )

    card = await phase2_client.get("/api/kiz/phase2-shipped")
    assert card.status_code == 200, card.text
    assert card.json()["lifecycle_status"] == "shipped"
    assert card.json()["location_id"] is None
    assert card.json()["location_code"] is None

    page = await phase2_client.get("/api/kiz", params={"lifecycle_status": "shipped"})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["location_id"] is None

    events = await phase2_client.get("/api/kiz/phase2-shipped/events")
    assert events.status_code == 200, events.text
    assert events.json()["items"][0]["movement_ref"] == movement_ref
    assert (await phase2_client.get("/api/system/kiz-integrity")).json() == []
