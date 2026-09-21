"""PostgreSQL and API acceptance tests for container Stage 3B Phase B1."""

import asyncio
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def container_b1_scope(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('container-b1-sku','B1 SKU')"
        )
        source = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B1-SOURCE',0) "
                "RETURNING location_id,location_code"
            )
        )
        destination = dict(
            await conn.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('B1-DESTINATION',0) "
                "RETURNING location_id,location_code"
            )
        )
    return source, destination


@pytest.fixture
async def container_b1_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def insert_container(
    conn, location_id, qr_code, *, container_type="box", status="empty"
):
    return await conn.fetchval(
        """INSERT INTO wms.containers(qr_code,container_type,location_id,status)
           VALUES ($1,$2,$3,$4) RETURNING container_id""",
        qr_code,
        container_type,
        location_id,
        status,
    )


async def test_container_identity_is_unique_immutable_and_not_deletable(
    kiz_pool, container_b1_scope
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        container_id = await insert_container(conn, source["location_id"], "B1-IDENTITY")

        with pytest.raises(asyncpg.UniqueViolationError):
            await insert_container(conn, source["location_id"], "B1-IDENTITY")

        with pytest.raises(asyncpg.PostgresError) as renamed:
            await conn.execute(
                "UPDATE wms.containers SET qr_code='B1-RENAMED' WHERE container_id=$1",
                container_id,
            )
        assert renamed.value.sqlstate == "55000"

        with pytest.raises(asyncpg.PostgresError) as reidentified:
            await conn.execute(
                "UPDATE wms.containers SET container_id=999 WHERE container_id=$1",
                container_id,
            )
        assert reidentified.value.sqlstate == "55000"

        with pytest.raises(asyncpg.PostgresError) as deleted:
            await conn.execute(
                "DELETE FROM wms.containers WHERE container_id=$1", container_id
            )
        assert deleted.value.sqlstate == "55000"
        assert await conn.fetchval(
            "SELECT qr_code FROM wms.containers WHERE container_id=$1", container_id
        ) == "B1-IDENTITY"


async def test_flat_model_and_direct_location_are_enforced(
    kiz_pool, container_b1_scope
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        parent_id = await insert_container(conn, source["location_id"], "B1-PARENT")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """INSERT INTO wms.containers(
                       qr_code,container_type,location_id,status,parent_container_id
                   ) VALUES ('B1-CHILD','box',$1,'empty',$2)""",
                source["location_id"],
                parent_id,
            )
        with pytest.raises(asyncpg.NotNullViolationError):
            await conn.execute(
                """INSERT INTO wms.containers(qr_code,container_type,location_id,status)
                   VALUES ('B1-NO-LOCATION','box',NULL,'empty')"""
            )


async def test_b4_rejects_direct_active_content_writer(kiz_pool, container_b1_scope):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        container_id = await insert_container(
            conn, source["location_id"], "B1-CONTENTS", status="open"
        )
        with pytest.raises(asyncpg.CheckViolationError, match="container_operation"):
            await conn.execute(
                "INSERT INTO wms.container_contents("
                "container_id,product_id,quantity,batch_number,status) "
                "VALUES ($1,'container-b1-sku',1,NULL,'active')",
                container_id,
            )
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.container_contents WHERE container_id=$1",
            container_id,
        ) == 0

async def test_empty_and_open_containers_reject_direct_active_contents(
    kiz_pool, container_b1_scope
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        empty_id = await insert_container(conn, source["location_id"], "B1-EMPTY")
        open_id = await insert_container(
            conn, source["location_id"], "B1-OPEN", status="open"
        )
        for container_id in (empty_id, open_id):
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO wms.container_contents("
                    "container_id,product_id,quantity,status) "
                    "VALUES ($1,'container-b1-sku',1,'active')",
                    container_id,
                )

@pytest.mark.parametrize("invalid_status", ["opened", "in_transit", "unknown"])
async def test_invalid_container_status_is_rejected(
    kiz_pool, container_b1_scope, invalid_status
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await insert_container(
                conn,
                source["location_id"],
                f"B1-STATUS-{invalid_status}",
                status=invalid_status,
            )


@pytest.mark.parametrize("invalid_type", ["unit", "unknown"])
async def test_invalid_container_type_is_rejected(
    kiz_pool, container_b1_scope, invalid_type
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await insert_container(
                conn,
                source["location_id"],
                f"B1-TYPE-{invalid_type}",
                container_type=invalid_type,
            )


@pytest.mark.parametrize("container_type", ["pallet", "box", "cage", "trolley"])
async def test_all_container_types_round_trip_through_register_and_card(
    container_b1_client, container_b1_scope, container_type
):
    source, _ = container_b1_scope
    qr_code = f"B1-{container_type.upper()}"
    response = await container_b1_client.post(
        "/api/containers/register",
        json={
            "qr_code": qr_code,
            "container_type": container_type,
            "location_code": source["location_code"],
            "contents": [],
        },
    )
    assert response.status_code == 201, response.text

    card = await container_b1_client.get(f"/api/containers/{qr_code}")
    assert card.status_code == 200, card.text
    assert card.json()["container_type"] == container_type
    assert card.json()["status"] == "empty"


@pytest.mark.parametrize("container_status", ["empty", "open", "sealed", "blocked"])
async def test_all_container_statuses_round_trip_through_api(
    container_b1_client, container_b1_scope, container_status
):
    source, _ = container_b1_scope
    qr_code = f"B1-ROUNDTRIP-{container_status.upper()}"
    created = await container_b1_client.post(
        "/api/containers/register",
        json={
            "qr_code": qr_code,
            "container_type": "box",
            "location_code": source["location_code"],
            "contents": [],
        },
    )
    container_id = created.json()["container_id"]
    updated = await container_b1_client.patch(
        f"/api/containers/{container_id}/status",
        json={"status": container_status},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == container_status

    card = await container_b1_client.get(f"/api/containers/{qr_code}")
    assert card.status_code == 200, card.text
    assert card.json()["status"] == container_status


async def test_b4_register_is_empty_only_and_legacy_move_unpack_are_unavailable(
    container_b1_client, container_b1_scope
):
    source, destination = container_b1_scope
    rejected = await container_b1_client.post(
        "/api/containers/register",
        json={
            "qr_code": "B1-LEGACY",
            "container_type": "box",
            "location_code": source["location_code"],
            "contents": [{
                "product_id": "container-b1-sku", "quantity": 2,
                "batch_number": "B1-BATCH", "is_scanned": True,
            }],
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "CONTAINER_CONTENTS_NOT_ALLOWED"

    created = await container_b1_client.post(
        "/api/containers/register",
        json={
            "qr_code": "B1-EMPTY-B4", "container_type": "box",
            "location_code": source["location_code"], "contents": [],
        },
    )
    container_id = created.json()["container_id"]
    moved = await container_b1_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": destination["location_code"]},
    )
    unpacked = await container_b1_client.post(
        f"/api/containers/{container_id}/unpack",
        json={"qr_code": "B1-EMPTY-B4", "product_id": "container-b1-sku", "quantity": 1},
    )
    assert moved.status_code in {404, 405}
    assert unpacked.status_code in {404, 405}

def test_b1_migration_has_no_physical_quantity_writes():
    sql = (
        ROOT / "scripts/migrations/20260915_add_container_b1_contract.sql"
    ).read_text(encoding="utf-8").lower()
    assert "update wms.inventory" not in sql
    assert "insert into wms.movements" in sql  # Legacy function body only.
    before_functions = sql.split("create or replace function wms.register_container", 1)[0]
    assert "insert into wms.movements" not in before_functions
    assert "update wms.container_contents\n    set quantity" not in before_functions


async def test_b4_direct_content_rejection_keeps_container_state(
    kiz_pool, container_b1_scope
):
    source, _ = container_b1_scope
    async with kiz_pool.acquire() as conn:
        container_id = await insert_container(
            conn, source["location_id"], "B1-CONCURRENT", status="open"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO wms.container_contents("
                "container_id,product_id,quantity,status) "
                "VALUES ($1,'container-b1-sku',1,'active')",
                container_id,
            )
        assert await conn.fetchval(
            "SELECT status FROM wms.containers WHERE container_id=$1", container_id
        ) == "open"
