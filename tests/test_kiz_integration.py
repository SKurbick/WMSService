"""Acceptance tests on restored production DDL + the actual KIZ migration."""
from decimal import Decimal

import pytest

from app.core.schemas.kiz import KizAssignment
from app.core.services.kiz_service import KizService
from app.infrastructure.database.repositories.kiz_repository import KizRepository


@pytest.fixture
async def stock(kiz_pool):
    async with kiz_pool.acquire() as conn:
        await conn.execute("INSERT INTO public.products(id,name) VALUES ('sku','SKU'),('other','Other')")
        row = await conn.fetchrow(
            "INSERT INTO wms.locations(name,level) VALUES ('KIZTEST',0) RETURNING location_id,location_code"
        )
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity)
               VALUES ('receive','sku',$1,10)""", row['location_id'],
        )
    return dict(row)


def assignment(stock, code="code", product="sku"):
    return KizAssignment(kiz_code=code, product_id=product,
                         location_code=stock['location_code'], author="tester")


@pytest.fixture
def kiz_service(kiz_pool):
    return KizService(KizRepository(kiz_pool))


async def test_assignment_smoke(kiz_pool, stock, kiz_service):
    result = await kiz_service.assign(assignment(stock))
    assert result["physical_quantity"] == Decimal(10)
    assert result["identified_quantity"] == 1
    assert result["unidentified_quantity"] == 9
    assert result["kiz"]["lifecycle_status"] == "active"
    async with kiz_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.movement_registry") == 1
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_movement_links") == 0
        assert await conn.fetchval("SELECT count(*) FROM wms.kiz_events") == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM wms.kiz_events WHERE movement_ref IS NOT NULL"
        ) == 0

import asyncio
import asyncpg
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from app.core.exceptions import LocationNotFoundError, ProductNotFoundError
from app.core.kiz_errors import KizConflictError, KizGuardError
from app.core.schemas.kiz import KizTerminalRequest
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.system_repository import SystemRepository
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
async def client(kiz_pool):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db_pool] = lambda: kiz_pool
    add_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def identify(service, stock, count=4):
    for i in range(count):
        await service.assign(assignment(stock, f"initial-{i}"))


@pytest.mark.parametrize("field,value,error", [
    ("product_id", "missing", ProductNotFoundError),
    ("location_code", "missing", LocationNotFoundError),
    ("product_id", "other", KizConflictError),
])
async def test_assignment_missing_scope(stock, kiz_service, field, value, error):
    data = assignment(stock).model_dump()
    data[field] = value
    with pytest.raises(error):
        await kiz_service.assign(KizAssignment(**data))


async def test_duplicate_and_fractional(kiz_pool, stock, kiz_service):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=1.5")
    await kiz_service.assign(assignment(stock, "Case"))
    with pytest.raises(KizConflictError):
        await kiz_service.assign(assignment(stock, "Case"))
    with pytest.raises(KizConflictError):
        await kiz_service.assign(assignment(stock, "case"))
    await kiz_service.terminate("Case", "error", KizTerminalRequest(author="tester", reason="wrong"))
    result = await kiz_service.assign(assignment(stock, "case"))
    assert result["identified_quantity"] == 1
    assert result["unidentified_quantity"] == Decimal("0.5")


@pytest.mark.parametrize("terminal", ["error", "deactivated"])
async def test_terminal_and_audit(kiz_pool, stock, kiz_service, terminal):
    await kiz_service.assign(assignment(stock))
    result = await kiz_service.terminate(
        "code", terminal, KizTerminalRequest(author="closer", reason="test", metadata={"ticket": 1})
    )
    assert result["closed_at"] is not None
    assert result["lifecycle_status"] == terminal
    with pytest.raises(KizConflictError):
        await kiz_service.terminate(
            "code", terminal, KizTerminalRequest(author="closer", reason="again")
        )
    events = await kiz_service.events("code")
    assert events["total"] == 2
    assert events["items"][1]["reason"] == "test"
    assert (await kiz_service.summary("sku", stock["location_code"]))["identified_quantity"] == 0
    async with kiz_pool.acquire() as c:
        await c.execute("DELETE FROM wms.inventory")
        assert await c.fetchval("SELECT count(*) FROM wms.inventory") == 0


@pytest.mark.parametrize("target,allowed", [(12, True), (6, True), (4, True), (3, False)])
async def test_inventory_quantity_guard(kiz_pool, stock, kiz_service, target, allowed):
    await identify(kiz_service, stock)
    async with kiz_pool.acquire() as c:
        if allowed:
            await c.execute("UPDATE wms.inventory SET quantity=$1", Decimal(target))
        else:
            with pytest.raises(KizGuardError):
                await c.execute("UPDATE wms.inventory SET quantity=$1", Decimal(target))
        assert await c.fetchval("SELECT quantity FROM wms.inventory") == (target if allowed else 10)


@pytest.mark.parametrize("mutation", [
    "DELETE FROM wms.inventory",
    "UPDATE wms.inventory SET product_id='other'",
    "UPDATE wms.inventory SET location_id=(SELECT max(location_id) FROM wms.locations)",
    "UPDATE wms.inventory SET status='damaged'",
    "UPDATE wms.inventory SET batch_number='batch'",
    "UPDATE wms.inventory SET container_code='box'",
])
async def test_inventory_identity_guard(kiz_pool, stock, kiz_service, mutation):
    await identify(kiz_service, stock)
    async with kiz_pool.acquire() as c:
        await c.execute("INSERT INTO wms.locations(name,level) VALUES ('OTHER',0)")
        with pytest.raises(KizGuardError):
            await c.execute(mutation)
        row = await c.fetchrow("SELECT * FROM wms.inventory")
        assert row["quantity"] == 10
        assert row["location_id"] == stock["location_id"]


async def test_write_audit_failure_rolls_back_assignment_and_touch(kiz_pool, stock, kiz_service, monkeypatch):
    async with kiz_pool.acquire() as c:
        before = dict(await c.fetchrow("SELECT * FROM wms.inventory"))
    async def fail_event(*args):
        raise RuntimeError("audit injection")
    monkeypatch.setattr(kiz_service.repo, "event", fail_event)
    with pytest.raises(RuntimeError, match="audit injection"):
        await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as c:
        assert dict(await c.fetchrow("SELECT * FROM wms.inventory")) == before
        assert await c.fetchval("SELECT count(*) FROM wms.kiz") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.kiz_events") == 0


async def wait_blocked(pool, pid):
    async with pool.acquire() as observer:
        for _ in range(200):
            if await observer.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
                return
            await asyncio.sleep(0.01)
    pytest.fail("Expected a real PostgreSQL lock wait")


@pytest.mark.parametrize("duplicate", [False, True])
async def test_concurrent_assignment(kiz_pool, stock, kiz_service, duplicate):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=5")
    await identify(kiz_service, stock)
    # For UNIQUE race, use another physical scope so inventory lock cannot serialize it.
    other = dict(stock)
    if duplicate:
        async with kiz_pool.acquire() as c:
            other = dict(await c.fetchrow(
                "INSERT INTO wms.locations(name,level) VALUES ('OTHER',0) RETURNING location_id,location_code"
            ))
            await c.execute(
                "INSERT INTO wms.inventory(product_id,location_id,status,quantity) VALUES ('sku',$1,'available',1)",
                other["location_id"],
            )
    async with kiz_pool.acquire() as first, kiz_pool.acquire() as second:
        async with first.transaction():
            await kiz_service.assign_in_transaction(first, assignment(stock, "winner"))
            async def compete():
                async with second.transaction():
                    return await kiz_service.assign_in_transaction(
                        second, assignment(other, "winner" if duplicate else "loser")
                    )
            task = asyncio.create_task(compete())
            try:
                await wait_blocked(kiz_pool, second.get_server_pid())
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        with pytest.raises(KizConflictError):
            await asyncio.wait_for(task, 5)
    assert (await kiz_service.summary("sku", stock["location_code"]))["identified_quantity"] == 5
    assert (await kiz_service.events("winner"))["total"] == 1


@pytest.mark.parametrize("writer_kind", ["inventory", "movement", "assignment"])
async def test_real_assignment_invalidates_repeatable_read(kiz_pool, stock, kiz_service, writer_kind):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=5")
    await identify(kiz_service, stock)
    async with kiz_pool.acquire() as writer:
        with pytest.raises(asyncpg.SerializationError):
            async with writer.transaction(isolation="repeatable_read"):
                assert await writer.fetchval("SELECT count(*) FROM wms.kiz") == 4
                await kiz_service.assign(assignment(stock, "fifth"))
                if writer_kind == "inventory":
                    await writer.execute("UPDATE wms.inventory SET quantity=4")
                elif writer_kind == "movement":
                    await writer.execute(
                        """INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity)
                        VALUES ('ship','sku',$1,1)""", stock["location_id"],
                    )
                else:
                    await kiz_service.assign_in_transaction(writer, assignment(stock, "sixth"))
    summary = await kiz_service.summary("sku", stock["location_code"])
    assert summary["physical_quantity"] == summary["identified_quantity"] == 5
    async with kiz_pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == 1


@pytest.mark.parametrize("kind", ["ship", "adjust"])
@pytest.mark.parametrize("quantity", [6, 7])
async def test_movement_api_rollback(client, kiz_pool, stock, kiz_service, kind, quantity):
    await identify(kiz_service, stock)
    response = await client.post("/api/movements", json=[{
        "movement_type": kind, "product_id": "sku",
        "from_location_code": stock["location_code"], "quantity": quantity,
        "reason": "test", "user_name": "tester",
    }])
    assert response.status_code == (201 if quantity == 6 else 409), response.text
    async with kiz_pool.acquire() as c:
        assert await c.fetchval("SELECT quantity FROM wms.inventory") == (4 if quantity == 6 else 10)
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == (2 if quantity == 6 else 1)
        assert await c.fetchval("SELECT count(*) FROM wms.movement_registry") == (
            2 if quantity == 6 else 1
        )
        assert await c.fetchval("SELECT count(*) FROM wms.kiz") == 4


@pytest.mark.parametrize("kind", ["assembly", "disassembly", "re_sorting"])
@pytest.mark.parametrize("quantity", [6, 7])
async def test_existing_operation_flows(client, kiz_pool, stock, kiz_service, kind, quantity):
    async with kiz_pool.acquire() as c:
        operation_code = "re_sorting_operations" if kind == "re_sorting" else "kit_operations"
        await c.execute(
            """INSERT INTO wms.operation_locations(operation_code,location_id,location_code,scope,author)
            VALUES ($1,$2,$3,'direct','tester')""", operation_code,
            stock["location_id"], stock["location_code"],
        )
        kit_id = "sku" if kind == "disassembly" else "other"
        component_id = "other" if kind == "disassembly" else "sku"
        import json
        await c.execute("UPDATE public.products SET is_kit=true,kit_components=$2::jsonb WHERE id=$1",
                        kit_id, json.dumps({component_id: 1}))
    await identify(kiz_service, stock)
    if kind == "re_sorting":
        path = "/api/re-sorting-operations"
        data = dict(from_product_id="sku", to_product_id="other", reason="test")
    else:
        path = "/api/kit-operations"
        data = dict(operation_type=kind, kit_product_id=kit_id)
    response = await client.post(path, json=dict(
        **data, quantity=quantity, author="tester", location_code=stock["location_code"]
    ))
    assert response.status_code == (201 if quantity == 6 else 409), response.text
    async with kiz_pool.acquire() as c:
        assert await c.fetchval("SELECT quantity FROM wms.inventory WHERE product_id='sku'") == (
            4 if quantity == 6 else 10
        )
        table = "re_sorting_operations" if kind == "re_sorting" else "kit_operations"
        assert await c.fetchval(f"SELECT count(*) FROM wms.{table}") == (1 if quantity == 6 else 0)
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == (3 if quantity == 6 else 1)
        assert await c.fetchval("SELECT count(*) FROM wms.movement_registry") == (
            3 if quantity == 6 else 1
        )


@pytest.mark.parametrize("quantity", [6, 8])
async def test_fbs_http_group_atomicity(client, kiz_pool, stock, kiz_service, monkeypatch, quantity):
    from app.shared.config import settings
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", stock["location_code"])
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    async with kiz_pool.acquire() as c:
        await c.execute("""INSERT INTO public.assembly_task(task_id,vendor_code,date)
            SELECT n, 'sku', CURRENT_DATE FROM generate_series(1,8) n""")
    await identify(kiz_service, stock)
    response = await client.post("/api/fbs-shipments", json=[dict(
        product_id="sku", quantity=quantity, assembly_tasks=[str(n) for n in range(1, quantity+1)],
        author="tester", supply_id="supply", warehouse_id=1, delivery_type="fbs", account="test",
    )])
    assert response.status_code == (201 if quantity == 6 else 409), response.text
    async with kiz_pool.acquire() as c:
        item = await c.fetchrow("SELECT * FROM wms.fbs_shipment_items")
        assert item["status"] == ("success" if quantity == 6 else "failed")
        if quantity == 8:
            assert item["error_message"].startswith("KIZ_CONFLICT:")
            assert item["next_retry_at"] is None
        assert await c.fetchval("SELECT count(*) FROM public.assembly_task WHERE is_shipped") == (
            quantity if quantity == 6 else 0
        )
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == (2 if quantity == 6 else 1)
        assert await c.fetchval("SELECT count(*) FROM wms.movement_registry") == (
            2 if quantity == 6 else 1
        )


@pytest.mark.parametrize("calculated", [10, 3, 0])
async def test_recalculate_guard(client, kiz_pool, stock, kiz_service, calculated):
    await identify(kiz_service, stock)
    async with kiz_pool.acquire() as c:
        # Deliberately damage ledger only in this disposable DB to exercise maintenance.
        if calculated == 0:
            await c.execute(
                "ALTER TABLE wms.movement_registry DISABLE TRIGGER "
                "trg_movement_registry_immutable"
            )
            await c.execute("DELETE FROM wms.movement_registry")
            await c.execute(
                "ALTER TABLE wms.movement_registry ENABLE TRIGGER "
                "trg_movement_registry_immutable"
            )
            await c.execute("DELETE FROM wms.movements")
        else:
            await c.execute("UPDATE wms.movements SET quantity=$1", Decimal(calculated))
        await c.execute("UPDATE wms.inventory SET quantity=12")
        before = dict(await c.fetchrow("SELECT * FROM wms.inventory"))
        registry_before = await c.fetchval("SELECT count(*) FROM wms.movement_registry")
    response = await client.post("/api/system/recalculate-inventory", json={})
    assert response.status_code == (200 if calculated == 10 else 409), response.text
    async with kiz_pool.acquire() as c:
        row = dict(await c.fetchrow("SELECT * FROM wms.inventory"))
        assert row["quantity"] == (10 if calculated == 10 else 12)
        if calculated != 10:
            assert row == before
            diagnostic = response.json()["diagnostics"][0]
            assert diagnostic["identified_quantity"] == 4
            assert diagnostic["calculated_quantity"] == calculated
        assert await c.fetchval("SELECT count(*) FROM wms.kiz") == 4
        assert await c.fetchval("SELECT count(*) FROM wms.movement_registry") == registry_before


async def test_api_read_lifecycle_and_integrity(client, kiz_pool, stock):
    data = assignment(stock).model_dump()
    result = await client.post("/api/kiz/assign", json=data)
    assert result.status_code == 201, result.text
    assert (await client.get("/api/kiz/code")).json()["created_by"] == "tester"
    assert (await client.get("/api/kiz", params={"location_code":stock["location_code"]})).json()["total"] == 1
    assert (await client.get("/api/kiz/code/events")).json()["items"][0]["event_type"] == "assigned"
    assert (await client.get("/api/kiz/stock-summary", params={
        "product_id":"sku", "location_code":stock["location_code"],
    })).json()["identified_quantity"] == 1
    assert (await client.get("/api/system/kiz-integrity")).json() == []
    async with kiz_pool.acquire() as c:
        # Owner-only corruption injection: never performed against application DB.
        async with c.transaction():
            await c.execute("ALTER TABLE wms.inventory DISABLE TRIGGER trg_kiz_inventory_guard")
            await c.execute("DELETE FROM wms.inventory")
            await c.execute("ALTER TABLE wms.inventory ENABLE TRIGGER trg_kiz_inventory_guard")
    broken = (await client.get("/api/system/kiz-integrity")).json()
    assert broken[0]["inventory_missing"] is True
    assert broken[0]["difference"] == "1"
    assert (await client.post("/api/kiz/code/deactivate", json={"author":"tester","reason":"close"})).status_code == 200
    assert (await client.get("/api/system/kiz-integrity")).json() == []

@pytest.mark.parametrize("path,data,status", [
    ("/api/kiz/assign", {"kiz_code":" bad","product_id":"sku","location_code":"KIZTEST","author":"t"}, 422),
    ("/api/kiz/assign", {"kiz_code":"a","product_id":"missing","location_code":"KIZTEST","author":"t"}, 404),
    ("/api/kiz/assign", {"kiz_code":"a","product_id":"sku","location_code":"missing","author":"t"}, 404),
    ("/api/kiz/assign", {"kiz_code":"a","product_id":"other","location_code":"KIZTEST","author":"t"}, 409),
    ("/api/kiz/code/mark-error", {"author":"t","reason":""}, 422),
])
async def test_api_input_errors(client, stock, path, data, status):
    response = await client.post(path, json=data)
    assert response.status_code == status, response.text


async def test_code_with_slash_and_pagination(client, stock):
    for code in ["a/b+CODE", "second"]:
        response = await client.post("/api/kiz/assign", json=assignment(stock, code).model_dump())
        assert response.status_code == 201, response.text
    assert (await client.get("/api/kiz/a%2Fb%2BCODE")).json()["kiz_code"] == "a/b+CODE"
    page = (await client.get("/api/kiz", params={"limit":1,"offset":1,"lifecycle_status":"active"})).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (await client.get("/api/kiz", params={"offset":100})).json()["items"] == []
    result = await client.post("/api/kiz/a%2Fb%2BCODE/mark-error",
                              json={"author":"tester","reason":"wrong"})
    assert result.status_code == 200, result.text
    assert (await client.get("/api/kiz/a%2Fb%2BCODE/events", params={"limit":1,"offset":1})).json()["total"] == 2


@pytest.mark.parametrize("mutation", [
    "UPDATE wms.kiz SET kiz_code='changed'",
    "DELETE FROM wms.kiz",
    "UPDATE wms.kiz_events SET reason='changed'",
    "DELETE FROM wms.kiz_events",
])
async def test_identity_and_audit_immutable(kiz_pool, stock, kiz_service, mutation):
    await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as c:
        with pytest.raises(KizGuardError):
            await c.execute(mutation)


async def test_exact_stock_scope(kiz_pool, stock, kiz_service):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=0")
        await c.execute("""INSERT INTO wms.inventory(product_id,location_id,status,quantity,batch_number,container_code)
            VALUES ('sku',$1,'available',100,'batch',NULL),
                   ('sku',$1,'available',100,NULL,'box'),('sku',$1,'damaged',100,NULL,NULL)""",
            stock["location_id"])
    with pytest.raises(KizConflictError):
        await kiz_service.assign(assignment(stock))
    assert (await kiz_service.summary("sku", stock["location_code"]))["physical_quantity"] == 0


async def test_terminal_audit_failure_rolls_back(kiz_pool, stock, kiz_service, monkeypatch):
    await kiz_service.assign(assignment(stock))
    async def fail(*args):
        raise RuntimeError("audit failure")
    monkeypatch.setattr(kiz_service.repo, "event", fail)
    with pytest.raises(RuntimeError):
        await kiz_service.terminate("code","error",KizTerminalRequest(author="t",reason="wrong"))
    assert (await kiz_service.get("code"))["lifecycle_status"] == "active"
    assert (await kiz_service.events("code"))["total"] == 1


async def test_terminal_serializes_with_assignment(kiz_pool, stock, kiz_service):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=1")
    await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as first, kiz_pool.acquire() as second:
        async with first.transaction():
            await kiz_service.terminate_in_transaction(
                first, "code", "deactivated", KizTerminalRequest(author="t",reason="close")
            )
            async def compete():
                async with second.transaction():
                    return await kiz_service.assign_in_transaction(second, assignment(stock,"new"))
            task = asyncio.create_task(compete())
            try:
                await wait_blocked(kiz_pool, second.get_server_pid())
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        result = await asyncio.wait_for(task,5)
    assert result["identified_quantity"] == 1


async def test_recalculate_preserves_unrelated_and_removes_obsolete(kiz_pool, stock, kiz_service):
    await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as c:
        await c.execute("""INSERT INTO wms.inventory(product_id,location_id,status,quantity,batch_number)
          VALUES ('other',$1,'available',3,NULL),('sku',$1,'damaged',7,NULL),
                 ('sku',$1,'available',2,'obsolete')""",stock["location_id"])
    await SystemRepository(kiz_pool).recalculate_inventory(product_id="sku")
    async with kiz_pool.acquire() as c:
        assert await c.fetchval("SELECT quantity FROM wms.inventory WHERE product_id='other'") == 3
        assert await c.fetchval("SELECT quantity FROM wms.inventory WHERE status='damaged'") == 7
        assert await c.fetchval("SELECT count(*) FROM wms.inventory WHERE batch_number='obsolete'") == 0
    assert (await kiz_service.get("code"))["lifecycle_status"] == "active"


async def test_fbs_serialization_retry_is_bounded(client, kiz_pool, stock, monkeypatch):
    from app.shared.config import settings
    from app.retry_worker import process_pending_retries
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", stock["location_code"])
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    async with kiz_pool.acquire() as c:
        await c.execute("""INSERT INTO public.assembly_task(task_id,vendor_code,date)
            VALUES (1,'sku',CURRENT_DATE);
            CREATE FUNCTION wms.test_serialization_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION USING ERRCODE='40001', MESSAGE='injected concurrency conflict'; END;
            $$;
            CREATE TRIGGER test_serialization_failure BEFORE INSERT ON wms.movements
                FOR EACH ROW EXECUTE FUNCTION wms.test_serialization_failure();
        """)
    response = await client.post("/api/fbs-shipments",json=[dict(
        product_id="sku", quantity=1, assembly_tasks=["1"], author="tester",
        supply_id="supply", warehouse_id=1, delivery_type="fbs", account="test",
    )])
    assert response.status_code == 409, response.text
    async with kiz_pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM wms.fbs_shipment_items")
        assert row["status"] == "pending_retry"
        assert row["error_message"].startswith("CONCURRENT_WRITE_CONFLICT:")
        assert row["next_retry_at"] is not None
        await c.execute("""UPDATE wms.fbs_shipment_items
            SET max_retries=1,next_retry_at=now()-interval '1 minute'""")
    await process_pending_retries(kiz_pool)
    async with kiz_pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM wms.fbs_shipment_items")
        assert row["status"] == "retry_exhausted"
        assert row["retry_count"] == 1 and row["next_retry_at"] is None
        assert not await c.fetchval("SELECT is_shipped FROM public.assembly_task")
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == 1


@pytest.mark.parametrize("first_action", ["assign", "spend", "terminal"])
async def test_inventory_lock_serializes_identity_and_spend(
    kiz_pool, stock, kiz_service, first_action
):
    async with kiz_pool.acquire() as c:
        await c.execute("UPDATE wms.inventory SET quantity=5")
    await identify(kiz_service, stock)

    async def spend(conn):
        await conn.execute(
            """INSERT INTO wms.movements(movement_type,product_id,from_location_id,quantity)
               VALUES ('ship','sku',$1,1)""", stock["location_id"]
        )

    async with kiz_pool.acquire() as first, kiz_pool.acquire() as second:
        async with first.transaction():
            if first_action == "assign":
                await kiz_service.assign_in_transaction(first, assignment(stock, "fifth"))
            elif first_action == "spend":
                await spend(first)
            else:
                await kiz_service.terminate_in_transaction(
                    first, "initial-0", "error", KizTerminalRequest(author="t", reason="wrong")
                )

            async def compete():
                async with second.transaction():
                    if first_action == "spend":
                        return await kiz_service.assign_in_transaction(second, assignment(stock))
                    await spend(second)

            task = asyncio.create_task(compete())
            try:
                await wait_blocked(kiz_pool, second.get_server_pid())
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        if first_action == "terminal":
            await asyncio.wait_for(task, 5)
        else:
            error = KizGuardError if first_action == "assign" else KizConflictError
            with pytest.raises(error):
                await asyncio.wait_for(task, 5)

    summary = await kiz_service.summary("sku", stock["location_code"])
    assert summary["physical_quantity"] == (5 if first_action == "assign" else 4)
    assert summary["identified_quantity"] == {"assign": 5, "spend": 4, "terminal": 3}[first_action]
    assert summary["integrity_ok"]
    async with kiz_pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == (
            1 if first_action == "assign" else 2
        )


async def test_manual_fbs_retry_kiz_conflict_and_recovery(client, kiz_pool, stock, kiz_service, monkeypatch):
    from app.shared.config import settings
    monkeypatch.setattr(settings, "FBS_LOCATION_CODE", stock["location_code"])
    monkeypatch.setattr(settings, "FBS_VALIDATE_ASSEMBLY_TASKS", True)
    async with kiz_pool.acquire() as c:
        await c.execute("""INSERT INTO public.assembly_task(task_id,vendor_code,date)
            SELECT n,'sku',CURRENT_DATE FROM generate_series(1,7) n""")
    await identify(kiz_service, stock)
    response = await client.post("/api/fbs-shipments", json=[dict(
        product_id="sku", quantity=7, assembly_tasks=[str(n) for n in range(1,8)],
        author="t", supply_id="s", warehouse_id=1, delivery_type="fbs", account="test",
    )])
    assert response.status_code == 409, response.text
    item_id = response.json()["items"][0]["item_id"]
    retry = await client.post(f"/api/fbs-shipments/items/{item_id}/retry")
    assert retry.status_code == 409, retry.text
    assert retry.json()["detail"]["error_code"] == "KIZ_CONFLICT"
    async with kiz_pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM wms.fbs_shipment_items WHERE item_id=$1", item_id)
        assert row["status"] == "failed" and row["next_retry_at"] is None
        assert row["retry_count"] == 1
        assert await c.fetchval("SELECT count(*) FROM public.assembly_task WHERE is_shipped") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == 1
    await kiz_service.terminate(
        "initial-0", "error", KizTerminalRequest(author="t", reason="wrong identity")
    )
    recovered = await client.post(f"/api/fbs-shipments/items/{item_id}/retry")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["items"][0]["status"] == "success"
    assert recovered.json()["items"][0]["error_message"] is None
    assert (await kiz_service.summary("sku", stock["location_code"]))["physical_quantity"] == 3


async def test_terminal_with_stale_snapshot_rolls_back(kiz_pool, stock, kiz_service):
    await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as stale:
        with pytest.raises(asyncpg.SerializationError):
            async with stale.transaction(isolation="repeatable_read"):
                assert await stale.fetchval("SELECT count(*) FROM wms.kiz") == 1
                await kiz_service.assign(assignment(stock, "second"))
                await kiz_service.terminate_in_transaction(
                    stale, "code", "error", KizTerminalRequest(author="t", reason="wrong")
                )
    assert (await kiz_service.get("code"))["lifecycle_status"] == "active"
    assert (await kiz_service.events("code"))["total"] == 1
    assert (await kiz_service.summary("sku", stock["location_code"]))["identified_quantity"] == 2


async def test_recalculate_waits_for_assignment_and_preserves_identity(kiz_pool, stock, kiz_service):
    async with kiz_pool.acquire() as first, kiz_pool.acquire() as maintenance:
        class Acquired:
            async def __aenter__(self):
                return maintenance

            async def __aexit__(self, *args):
                return False

        class SingleConnectionPool:
            def acquire(self):
                return Acquired()

        async with first.transaction():
            await kiz_service.assign_in_transaction(first, assignment(stock))
            task = asyncio.create_task(
                SystemRepository(SingleConnectionPool()).recalculate_inventory(product_id="sku")
            )
            try:
                await wait_blocked(kiz_pool, maintenance.get_server_pid())
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        await asyncio.wait_for(task, 5)
    summary = await kiz_service.summary("sku", stock["location_code"])
    assert summary["physical_quantity"] == 10
    assert summary["identified_quantity"] == 1
    assert summary["integrity_ok"]
    assert (await kiz_service.events("code"))["total"] == 1


@pytest.mark.parametrize("code", ["stock-summary", "alpha/events", ".", "a/../b", "a\nb"])
async def test_assignment_route_collision_422_without_writes(client, kiz_pool, stock, code):
    async with kiz_pool.acquire() as c:
        before = dict(await c.fetchrow("SELECT * FROM wms.inventory"))
    response = await client.post("/api/kiz/assign", json=assignment(stock).model_dump() | {"kiz_code": code})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "kiz_code"]
    async with kiz_pool.acquire() as c:
        assert dict(await c.fetchrow("SELECT * FROM wms.inventory")) == before
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == 1
        assert await c.fetchval("SELECT count(*) FROM wms.kiz") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.kiz_events") == 0


@pytest.mark.parametrize("operation", ["assign", "mark-error", "deactivate"])
async def test_metadata_nul_422_without_writes(client, kiz_pool, stock, kiz_service, operation):
    if operation != "assign":
        await kiz_service.assign(assignment(stock))
    async with kiz_pool.acquire() as c:
        before = dict(await c.fetchrow("SELECT * FROM wms.inventory"))
    payload = assignment(stock).model_dump() if operation == "assign" else {"author": "tester", "reason": "wrong"}
    payload["metadata"] = {"nested": [{"note": "bad\x00value"}]}
    path = "/api/kiz/assign" if operation == "assign" else f"/api/kiz/code/{operation}"
    response = await client.post(path, json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "metadata"]
    async with kiz_pool.acquire() as c:
        assert dict(await c.fetchrow("SELECT * FROM wms.inventory")) == before
        assert await c.fetchval("SELECT count(*) FROM wms.movements") == 1
        assert await c.fetchval("SELECT count(*) FROM wms.kiz_events") == (0 if operation == "assign" else 1)
    if operation != "assign":
        assert (await kiz_service.get("code"))["lifecycle_status"] == "active"


@pytest.mark.parametrize("code", ["assign", "events", "Stock-summary", "alpha/Events", "КИЗ/ABC+123"])
async def test_accepted_code_card_and_static_routes(client, stock, code):
    from urllib.parse import quote
    payload = assignment(stock, code).model_dump()
    payload["metadata"] = {"комментарий": ["Товар 😀", "\\u0000"]}
    response = await client.post("/api/kiz/assign", json=payload)
    assert response.status_code == 201, response.text
    response = await client.get("/api/kiz/" + quote(code, safe=""))
    assert response.status_code == 200, response.text
    assert response.json()["kiz_code"] == code
    assert response.json()["metadata"] == payload["metadata"]
    summary = await client.get("/api/kiz/stock-summary", params={"product_id": "sku", "location_code": stock["location_code"]})
    assert summary.status_code == 200 and summary.json()["identified_quantity"] == 1
