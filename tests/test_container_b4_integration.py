"""Acceptance coverage for Stage 3B B4 container writer closure."""

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.queries import tasks as task_queries
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def b4_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def b4_scope(kiz_pool):
    suffix = uuid4().hex[:8]
    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.products(id,name) VALUES ('container-b4-sku','B4 SKU') "
            "ON CONFLICT (id) DO NOTHING"
        )
        source = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ($1,0) "
            "RETURNING location_id,location_code", f"B4-SOURCE-{suffix}"
        ))
        destination = dict(await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ($1,0) "
            "RETURNING location_id,location_code", f"B4-DESTINATION-{suffix}"
        ))
    return source, destination


def movement_payload(movement_type, product_id, *, source=None, destination=None,
                     quantity=1, batch_number=None, container_code=None):
    return {
        "movement_type": movement_type,
        "product_id": product_id,
        "from_location_code": source,
        "to_location_code": destination,
        "quantity": quantity,
        "batch_number": batch_number,
        "container_code": container_code,
        "user_name": "b4-test",
        "reason": "B4 regression",
    }


async def test_generic_loose_receive_transfer_adjust_remain_supported(
    b4_client, kiz_pool, b4_scope
):
    source, destination = b4_scope
    payloads = [
        movement_payload(
            "receive", "container-b4-sku",
            destination=source["location_code"], quantity=5,
        ),
        movement_payload(
            "transfer", "container-b4-sku", source=source["location_code"],
            destination=destination["location_code"], quantity=2,
        ),
        movement_payload(
            "adjust", "container-b4-sku",
            destination=destination["location_code"], quantity=1,
        ),
    ]
    for payload in payloads:
        response = await b4_client.post("/api/movements", json=[payload])
        assert response.status_code == 201, response.text

    async with kiz_pool.acquire() as conn:
        quantities = await conn.fetch(
            "SELECT location_id,quantity FROM wms.inventory "
            "WHERE product_id='container-b4-sku' AND status='available' "
            "AND batch_number IS NULL AND container_code IS NULL"
        )
    assert {row["location_id"]: row["quantity"] for row in quantities} == {
        source["location_id"]: Decimal("3"),
        destination["location_id"]: Decimal("3"),
    }


@pytest.mark.parametrize("movement_type", ["adjust", "receive", "transfer", "ship", "unpack"])
async def test_generic_container_movement_rejects_entire_batch_without_effect(
    b4_client, kiz_pool, b4_scope, movement_type
):
    source, destination = b4_scope
    before_count = await kiz_pool.fetchval(
        "SELECT count(*) FROM wms.movements WHERE reason='B4 rejected batch'"
    )
    first = movement_payload(
        "receive", "container-b4-sku", destination=source["location_code"]
    )
    first["reason"] = "B4 rejected batch"
    second = movement_payload(
        movement_type,
        "container-b4-sku",
        source=source["location_code"] if movement_type in {"transfer", "ship", "unpack"} else None,
        destination=destination["location_code"] if movement_type in {"adjust", "receive", "transfer"} else None,
        container_code="B4-FORBIDDEN",
    )
    second["reason"] = "B4 rejected batch"

    response = await b4_client.post("/api/movements", json=[first, second])
    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "GENERIC_CONTAINER_MOVEMENT_NOT_ALLOWED"
    assert await kiz_pool.fetchval(
        "SELECT count(*) FROM wms.movements WHERE reason='B4 rejected batch'"
    ) == before_count


async def test_empty_register_supported_nonempty_rejected_and_legacy_routes_absent(
    b4_client, kiz_pool, b4_scope
):
    source, destination = b4_scope
    empty = await b4_client.post("/api/containers/register", json={
        "qr_code": "B4-EMPTY",
        "container_type": "box",
        "location_code": source["location_code"],
        "contents": [],
    })
    assert empty.status_code == 201, empty.text
    container_id = empty.json()["container_id"]
    assert empty.json()["items_registered"] == 0

    rejected = await b4_client.post("/api/containers/register", json={
        "qr_code": "B4-NONEMPTY",
        "container_type": "box",
        "location_code": source["location_code"],
        "contents": [{
            "product_id": "container-b4-sku", "quantity": 1,
            "batch_number": None, "is_scanned": True,
        }],
    })
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error_code"] == "CONTAINER_CONTENTS_NOT_ALLOWED"
    assert await kiz_pool.fetchval(
        "SELECT count(*) FROM wms.containers WHERE qr_code='B4-NONEMPTY'"
    ) == 0

    legacy_move = await b4_client.put(
        f"/api/containers/{container_id}/location",
        json={"location_code": destination["location_code"]},
    )
    legacy_unpack = await b4_client.post(
        f"/api/containers/{container_id}/unpack",
        json={"qr_code": "B4-EMPTY", "product_id": "container-b4-sku", "quantity": 1},
    )
    assert legacy_move.status_code in {404, 405}
    assert legacy_unpack.status_code in {404, 405}

    schema = (await b4_client.get("/openapi.json")).json()
    assert "/api/containers/{container_id}/location" not in schema["paths"]
    assert "/api/containers/{container_id}/unpack" not in schema["paths"]


async def test_database_guard_rejects_uncontrolled_container_movement(kiz_pool, b4_scope):
    source, _ = b4_scope
    with pytest.raises(asyncpg.CheckViolationError):
        await kiz_pool.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,container_code) "
            "VALUES ('receive','container-b4-sku',$1,1,'B4-DIRECT')",
            source["location_id"],
        )


async def test_task_quantity_queries_use_only_exact_available_loose_scope(kiz_pool, b4_scope):
    source, _ = b4_scope
    async with kiz_pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO wms.inventory(product_id,location_id,quantity,status,batch_number,container_code) "
            "VALUES ('container-b4-sku',$1,$2,$3,$4,$5)",
            [
                (source["location_id"], 2, "available", None, None),
                (source["location_id"], 7, "available", "BATCH-X", None),
                (source["location_id"], 11, "available", None, "B4-TASK-BOX"),
                (source["location_id"], 13, "quarantine", None, None),
            ],
        )
        zone = await conn.fetchval(
            task_queries.GET_PRODUCT_QTY_IN_ZONE,
            "container-b4-sku", source["location_code"], None,
        )
        location = await conn.fetchval(
            task_queries.GET_INVENTORY_QTY_IN_LOCATION,
            "container-b4-sku", source["location_id"], None,
        )
        batch = await conn.fetchval(
            task_queries.GET_INVENTORY_QTY_BY_LOCATION_CODE,
            "container-b4-sku", source["location_code"], "BATCH-X",
        )
    assert zone == location == Decimal("2")
    assert batch == Decimal("7")


async def test_recalculate_valid_container_allowed_then_mismatch_aborts_without_rewrite(
    b4_client, kiz_pool, b4_scope
):
    source, _ = b4_scope
    received = await b4_client.post("/api/movements", json=[movement_payload(
        "receive", "container-b4-sku", destination=source["location_code"], quantity=4
    )])
    assert received.status_code == 201, received.text
    registered = await b4_client.post("/api/containers/register", json={
        "qr_code": "B4-RECALC",
        "container_type": "box",
        "location_code": source["location_code"],
        "contents": [],
    })
    container_id = registered.json()["container_id"]
    filled = await b4_client.post("/api/container-operations/fill", json={
        "source_system": "test",
        "external_operation_id": "b4-recalc-fill",
        "author": "b4-test",
        "container_id": container_id,
        "items": [{
            "external_line_id": "1", "product_id": "container-b4-sku",
            "quantity": "2", "batch_number": None,
        }],
    })
    assert filled.status_code == 201, filled.text

    valid = await b4_client.post(
        "/api/system/recalculate-inventory", json={"product_id": "container-b4-sku"}
    )
    assert valid.status_code == 200, valid.text

    async with kiz_pool.acquire() as conn:
        await conn.execute(
            "UPDATE wms.inventory SET quantity=quantity+1 "
            "WHERE product_id='container-b4-sku' AND container_code='B4-RECALC'"
        )
        before_loose = await conn.fetchval(
            "SELECT quantity FROM wms.inventory WHERE product_id='container-b4-sku' "
            "AND location_id=$1 AND status='available' AND batch_number IS NULL "
            "AND container_code IS NULL", source["location_id"],
        )

    rejected = await b4_client.post(
        "/api/system/recalculate-inventory", json={"product_id": "container-b4-sku"}
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error_code"] == "CONTAINER_INVENTORY_INTEGRITY_ERROR"

    async with kiz_pool.acquire() as conn:
        after_loose = await conn.fetchval(
            "SELECT quantity FROM wms.inventory WHERE product_id='container-b4-sku' "
            "AND location_id=$1 AND status='available' AND batch_number IS NULL "
            "AND container_code IS NULL", source["location_id"],
        )
    assert after_loose == before_loose

    audit = await b4_client.get("/api/system/audit-summary")
    assert audit.status_code == 200, audit.text
    assert audit.json()["container_quantity_mismatch_count"] >= 1
