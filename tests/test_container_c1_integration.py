"""PostgreSQL/API acceptance coverage for Stage 3C C1 KIZ in containers."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.infrastructure.database.connection import get_db_pool
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def c1_client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def c1_scope(kiz_pool):
    suffix = uuid4().hex[:8]
    async with kiz_pool.acquire() as conn:
        await conn.execute("INSERT INTO public.products(id,name) VALUES('c1-sku','C1 SKU') ON CONFLICT DO NOTHING")
        warehouse = await conn.fetchrow("INSERT INTO wms.locations(name,level) VALUES($1,0) RETURNING location_id,location_code",f'C1-W-{suffix}')
        source = await conn.fetchrow("INSERT INTO wms.locations(name,parent_location_id,level) VALUES($1,$2,1) RETURNING location_id,location_code",f'C1-S-{suffix}',warehouse['location_id'])
        destination = await conn.fetchrow("INSERT INTO wms.locations(name,parent_location_id,level) VALUES($1,$2,1) RETURNING location_id,location_code",f'C1-D-{suffix}',warehouse['location_id'])
        await conn.execute("INSERT INTO wms.inventory(product_id,location_id,quantity,status,batch_number,container_code) VALUES('c1-sku',$1,10,'available',NULL,NULL)",source['location_id'])
    return {"source":dict(source),"destination":dict(destination)}


async def setup_scope(client, scope, qr):
    for code in ['C1-K1','C1-K2','C1-K3']:
        response=await client.post('/api/kiz/assign',json={'kiz_code':code,'product_id':'c1-sku','location_code':scope['source']['location_code'],'author':'c1','metadata':{}})
        assert response.status_code==201,response.text
    response=await client.post('/api/containers/register',json={'qr_code':qr,'container_type':'box','location_code':scope['source']['location_code'],'contents':[]})
    assert response.status_code==201,response.text
    return response.json()['container_id']


def fill(container_id,key,quantity='5',codes=None):
    return {'source_system':'c1','external_operation_id':key,'author':'c1','container_id':container_id,'items':[{'external_line_id':'1','product_id':'c1-sku','quantity':quantity,'batch_number':None,'kiz_codes':codes or []}]}


def extract(container_id,key,quantity='1',codes=None):
    return {'source_system':'c1','external_operation_id':key,'author':'c1','container_id':container_id,'items':[{'external_line_id':'1','product_id':'c1-sku','quantity':quantity,'batch_number':None,'kiz_codes':codes or []}]}


async def test_mixed_fill_move_extract_unpack_replay_history_and_reads(c1_client,kiz_pool,c1_scope):
    container_id=await setup_scope(c1_client,c1_scope,'C1-FLOW')
    request=fill(container_id,'fill-1',codes=['C1-K2','C1-K1'])
    filled=await c1_client.post('/api/container-operations/fill',json=request)
    assert filled.status_code==201,filled.text
    assert filled.json()['items'][0]['kiz_codes']==['C1-K1','C1-K2']
    replay=await c1_client.post('/api/container-operations/fill',json={**request,'items':[{**request['items'][0],'kiz_codes':['C1-K1','C1-K2']}]})
    assert replay.status_code==201 and replay.json()==filled.json()
    conflict=await c1_client.post('/api/container-operations/fill',json={**request,'items':[{**request['items'][0],'kiz_codes':['C1-K1']}]})
    assert conflict.status_code==409 and conflict.json()['error_code']=='CONTAINER_IDEMPOTENCY_CONFLICT'

    move=await c1_client.post('/api/container-operations/move',json={'source_system':'c1','external_operation_id':'move-1','author':'c1','container_id':container_id,'to_location_code':c1_scope['destination']['location_code']})
    assert move.status_code==201,move.text
    assert move.json()['items'][0]['kiz_codes']==['C1-K1','C1-K2']

    extracted=await c1_client.post('/api/container-operations/extract',json=extract(container_id,'extract-1','3',['C1-K1']))
    assert extracted.status_code==201,extracted.text
    assert extracted.json()['items'][0]['kiz_codes']==['C1-K1']
    unpacked=await c1_client.post('/api/container-operations/unpack-all',json={'source_system':'c1','external_operation_id':'unpack-1','author':'c1','container_id':container_id})
    assert unpacked.status_code==201,unpacked.text
    assert unpacked.json()['items'][0]['kiz_codes']==['C1-K2']

    card=(await c1_client.get('/api/kiz/C1-K2')).json()
    assert card['container_id'] is None and card['location_id']==c1_scope['destination']['location_id']
    listed=(await c1_client.get('/api/kiz',params={'container_id':container_id})).json()
    assert listed['total']==0
    summary=(await c1_client.get('/api/kiz/stock-summary',params={'product_id':'c1-sku','container_id':container_id})).json()
    assert summary['physical_quantity']=='0' and summary['identified_quantity']==0
    history=(await c1_client.get('/api/kiz-history',params={'kiz_code':'C1-K1'})).json()
    assert [entry['event_type'] for entry in history['timeline']].count('fill')==1
    assert [entry['event_type'] for entry in history['timeline']].count('move')==1
    assert [entry['event_type'] for entry in history['timeline']].count('extract')==1
    integrity=await c1_client.get('/api/system/kiz-integrity')
    assert integrity.status_code==200 and integrity.json()==[]
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT sum(quantity) FROM wms.inventory WHERE product_id='c1-sku'")==Decimal('10')
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links l JOIN wms.kiz k USING(kiz_id) WHERE k.kiz_code='C1-K1'")==5


async def test_unidentified_limits_and_contained_terminal_transition(c1_client,kiz_pool,c1_scope):
    container_id=await setup_scope(c1_client,c1_scope,'C1-LIMIT')
    rejected=await c1_client.post('/api/container-operations/fill',json=fill(container_id,'too-many','8',[]))
    assert rejected.status_code==409
    assert rejected.json()['error_code']=='INSUFFICIENT_UNIDENTIFIED_QUANTITY'
    filled=await c1_client.post('/api/container-operations/fill',json=fill(container_id,'identified','2',['C1-K1']))
    assert filled.status_code==201,filled.text
    terminal=await c1_client.post('/api/kiz/C1-K1/mark-error',json={'author':'c1','reason':'test terminal','metadata':{}})
    assert terminal.status_code==200,terminal.text
    assert terminal.json()['lifecycle_status']=='error' and terminal.json()['container_id']==container_id
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT quantity FROM wms.inventory WHERE product_id='c1-sku' AND container_code='C1-LIMIT'")==Decimal('2')
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz WHERE container_id=$1 AND lifecycle_status='active'",container_id)==0
        assert await conn.fetchval("SELECT count(*) FROM wms.movements WHERE source_type='container_operation'")==2


async def test_same_kiz_concurrent_fill_has_one_winner(c1_client,kiz_pool,c1_scope):
    first=await setup_scope(c1_client,c1_scope,'C1-RACE-A')
    registered=await c1_client.post('/api/containers/register',json={'qr_code':'C1-RACE-B','container_type':'box','location_code':c1_scope['source']['location_code'],'contents':[]})
    second=registered.json()['container_id']
    responses=await asyncio.gather(
        c1_client.post('/api/container-operations/fill',json=fill(first,'race-a','1',['C1-K1'])),
        c1_client.post('/api/container-operations/fill',json=fill(second,'race-b','1',['C1-K1'])),
    )
    assert sorted(response.status_code for response in responses)==[201,409]
    async with kiz_pool.acquire() as conn:
        holder=await conn.fetchval("SELECT container_id FROM wms.kiz WHERE kiz_code='C1-K1' AND lifecycle_status='active'")
        assert holder in {first,second}
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links l JOIN wms.kiz k USING(kiz_id) WHERE k.kiz_code='C1-K1'")==2
        assert await conn.fetchval("SELECT count(*) FROM wms.check_kiz_holder_integrity()") == 0


def transfer_payload(scope, key, code="C1-K1"):
    return {
        "source_system": "c1",
        "external_operation_id": key,
        "author": "c1",
        "items": [
            {
                "external_line_id": "1",
                "product_id": "c1-sku",
                "from_location_code": scope["source"]["location_code"],
                "to_location_code": scope["destination"]["location_code"],
                "quantity": 1,
                "kiz_codes": [code],
            }
        ],
    }


def ship_payload(scope, key, code="C1-K1"):
    return {
        "source_system": "c1",
        "external_operation_id": key,
        "author": "c1",
        "items": [
            {
                "external_line_id": "1",
                "product_id": "c1-sku",
                "from_location_code": scope["source"]["location_code"],
                "quantity": 1,
                "kiz_codes": [code],
            }
        ],
    }


async def assert_c1_integrity(kiz_pool):
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.check_kiz_holder_integrity()") == 0


async def test_concurrent_fill_and_kiz_transfer_have_one_winner(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-TRANSFER")
    filled, transferred = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/fill",
            json=fill(container_id, "race-fill-transfer", "1", ["C1-K1"]),
        ),
        c1_client.post(
            "/api/kiz-operations/transfer",
            json=transfer_payload(c1_scope, "race-transfer-fill"),
        ),
    )
    assert sorted((filled.status_code, transferred.status_code)) == [201, 409]
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT location_id,container_id FROM wms.kiz WHERE kiz_code='C1-K1'"
        )
        assert (state["location_id"], state["container_id"]) in {
            (None, container_id),
            (c1_scope["destination"]["location_id"], None),
        }
        assert await conn.fetchval(
            "SELECT sum(quantity) FROM wms.inventory WHERE product_id='c1-sku'"
        ) == Decimal("10")
    await assert_c1_integrity(kiz_pool)


async def test_concurrent_fill_and_kiz_ship_have_one_winner(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-SHIP")
    filled, shipped = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/fill",
            json=fill(container_id, "race-fill-ship", "1", ["C1-K1"]),
        ),
        c1_client.post(
            "/api/kiz-operations/ship",
            json=ship_payload(c1_scope, "race-ship-fill"),
        ),
    )
    assert sorted((filled.status_code, shipped.status_code)) == [201, 409]
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT lifecycle_status,container_id FROM wms.kiz WHERE kiz_code='C1-K1'"
        )
        assert (state["lifecycle_status"], state["container_id"]) in {
            ("active", container_id),
            ("shipped", None),
        }
    await assert_c1_integrity(kiz_pool)


async def test_concurrent_extract_and_container_move_serialize(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-EXTRACT-MOVE")
    seeded = await c1_client.post(
        "/api/container-operations/fill",
        json=fill(container_id, "seed-extract-move", "2", ["C1-K1"]),
    )
    assert seeded.status_code == 201, seeded.text
    extracted, moved = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/extract",
            json=extract(container_id, "race-extract-move", "1", ["C1-K1"]),
        ),
        c1_client.post(
            "/api/container-operations/move",
            json={
                "source_system": "c1",
                "external_operation_id": "race-move-extract",
                "author": "c1",
                "container_id": container_id,
                "to_location_code": c1_scope["destination"]["location_code"],
            },
        ),
    )
    assert extracted.status_code == moved.status_code == 201
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT location_id FROM wms.containers WHERE container_id=$1", container_id
        ) == c1_scope["destination"]["location_id"]
        holder = await conn.fetchval(
            "SELECT location_id FROM wms.kiz WHERE kiz_code='C1-K1'"
        )
        assert holder in {
            c1_scope["source"]["location_id"],
            c1_scope["destination"]["location_id"],
        }
    await assert_c1_integrity(kiz_pool)


async def test_concurrent_extract_and_terminal_transition_are_atomic(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-TERMINAL")
    seeded = await c1_client.post(
        "/api/container-operations/fill",
        json=fill(container_id, "seed-terminal", "1", ["C1-K1"]),
    )
    assert seeded.status_code == 201, seeded.text
    extracted, terminal = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/extract",
            json=extract(container_id, "race-extract-terminal", "1", ["C1-K1"]),
        ),
        c1_client.post(
            "/api/kiz/C1-K1/mark-error",
            json={"author": "c1", "reason": "race", "metadata": {}},
        ),
    )
    assert sorted((extracted.status_code, terminal.status_code)) in ([200, 409], [201, 409])
    async with kiz_pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT lifecycle_status,location_id,container_id FROM wms.kiz "
            "WHERE kiz_code='C1-K1'"
        )
        assert (
            state["lifecycle_status"] == "active"
            and state["location_id"] == c1_scope["source"]["location_id"]
            and state["container_id"] is None
        ) or (
            state["lifecycle_status"] == "error"
            and state["location_id"] is None
            and state["container_id"] == container_id
        )
    await assert_c1_integrity(kiz_pool)


async def test_concurrent_move_and_unpack_all_serialize_identified_stock(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-MOVE-UNPACK")
    seeded = await c1_client.post(
        "/api/container-operations/fill",
        json=fill(container_id, "seed-move-unpack", "2", ["C1-K1"]),
    )
    assert seeded.status_code == 201, seeded.text
    moved, unpacked = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/move",
            json={
                "source_system": "c1",
                "external_operation_id": "race-move-unpack",
                "author": "c1",
                "container_id": container_id,
                "to_location_code": c1_scope["destination"]["location_code"],
            },
        ),
        c1_client.post(
            "/api/container-operations/unpack-all",
            json={
                "source_system": "c1",
                "external_operation_id": "race-unpack-move",
                "author": "c1",
                "container_id": container_id,
            },
        ),
    )
    assert moved.status_code == unpacked.status_code == 201
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT status FROM wms.containers WHERE container_id=$1", container_id
        ) == "empty"
        assert await conn.fetchval(
            "SELECT container_id FROM wms.kiz WHERE kiz_code='C1-K1'"
        ) is None
    await assert_c1_integrity(kiz_pool)


async def test_two_extracts_competing_for_same_kiz_have_one_winner(
    c1_client, kiz_pool, c1_scope
):
    container_id = await setup_scope(c1_client, c1_scope, "C1-RACE-EXTRACTS")
    seeded = await c1_client.post(
        "/api/container-operations/fill",
        json=fill(container_id, "seed-extracts", "1", ["C1-K1"]),
    )
    assert seeded.status_code == 201, seeded.text
    first, second = await asyncio.gather(
        c1_client.post(
            "/api/container-operations/extract",
            json=extract(container_id, "race-extract-a", "1", ["C1-K1"]),
        ),
        c1_client.post(
            "/api/container-operations/extract",
            json=extract(container_id, "race-extract-b", "1", ["C1-K1"]),
        ),
    )
    assert sorted((first.status_code, second.status_code)) == [201, 409]
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT location_id FROM wms.kiz WHERE kiz_code='C1-K1'"
        ) == c1_scope["source"]["location_id"]
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_movement_links l "
            "JOIN wms.kiz k USING(kiz_id) WHERE k.kiz_code='C1-K1'"
        ) == 4
    await assert_c1_integrity(kiz_pool)
