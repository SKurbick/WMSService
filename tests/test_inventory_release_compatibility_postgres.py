"""Inventory Stage 2A with upstream auto/partial KIZ import and warehouse history."""

import json
from uuid import uuid4

from test_inventory_documents_postgres import inventory_env  # noqa: F401
from test_inventory_freeze_postgres import freeze_env  # noqa: F401
from test_inventory_physical_apply_postgres import (
    physical_env,  # noqa: F401
    ready_document,
    approval,
)  # noqa: F401


async def test_auto_partial_registration_under_freeze_prevents_stale_apply(
    physical_env, monkeypatch  # noqa: F811
):
    import app.consumer as consumer

    env = physical_env
    doc, _ = await ready_document(env, "8")
    async with env["pool"].acquire() as conn:
        before_stock = await conn.fetchval("SELECT sum(quantity) FROM wms.inventory")
        before_movements = await conn.fetchval("SELECT count(*) FROM wms.movements")
    payload = {
        "supply": {"order_guid": "release-compat-guid", "supply_number": "release-compat"},
        "wild_groups": [
            {"wild": "inv-a", "mark_codes": ["REL-REGISTERED-1"]},
            {"wild": "release-missing-sku", "mark_codes": ["REL-REJECTED-1"]},
        ],
    }

    class Message:
        body = json.dumps(payload).encode()
        message_id = str(uuid4())
        exchange = "orders"
        routing_key = "orders.kiz.imported"
        correlation_id = None
        headers = {}
        acknowledged = False
        requeued = False

        async def ack(self):
            self.acknowledged = True

        async def nack(self, *, requeue):
            self.requeued = requeue

    async def pool():
        return env["pool"]

    monkeypatch.setattr(consumer, "get_db_pool", pool)
    message = Message()
    await consumer._process_kiz_import_message(message, queue_name="release-isolated-test")
    assert message.acknowledged and not message.requeued
    async with env["pool"].acquire() as conn:
        kiz = await conn.fetchrow(
            "SELECT lifecycle_status,location_id,container_id FROM wms.kiz WHERE kiz_code='REL-REGISTERED-1'"
        )
        assert dict(kiz) == {
            "lifecycle_status": "registered",
            "location_id": None,
            "container_id": None,
        }
        result = await conn.fetchval("SELECT business_result FROM wms.kiz_import_messages")
        result = json.loads(result) if isinstance(result, str) else result
        assert result["is_partial"] and result["new_kiz_count"] == 1
        assert result["rejected_groups"][0]["error_code"] == "UNKNOWN_PRODUCT"
        assert await conn.fetchval("SELECT sum(quantity) FROM wms.inventory") == before_stock
        assert await conn.fetchval("SELECT count(*) FROM wms.movements") == before_movements
        assert await conn.fetchval("SELECT count(*) FROM wms.receipt_items") == 0
    # The committed import is idempotent; a registration is never a physical receive.
    await consumer._process_kiz_import_message(message, queue_name="release-isolated-test")
    async with env["pool"].acquire() as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM wms.kiz WHERE kiz_code='REL-REGISTERED-1'")
            == 1
        )
    response = await env["client"].post(
        f"/api/inventory-documents/{doc['document_id']}/apply", json=approval(doc)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "INVENTORY_APPLICATION_STALE"
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval("SELECT sum(quantity) FROM wms.inventory") == before_stock
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory_applications") == 0


async def test_inventory_adjust_preserves_turnover_and_balance_history_contract(
    physical_env,  # noqa: F811
):
    env = physical_env
    async with env["pool"].acquire() as conn:
        day = str(await conn.fetchval("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date"))
    params = {"date_from": day, "date_to": day, "product_id": "inv-a"}
    before = await env["client"].get("/api/inventory-history/daily-turnover", params=params)
    assert before.status_code == 200, before.text
    doc, _ = await ready_document(env, "8")
    response = await env["client"].post(
        f"/api/inventory-documents/{doc['document_id']}/apply", json=approval(doc)
    )
    assert response.status_code == 200, response.text
    after = await env["client"].get("/api/inventory-history/daily-turnover", params=params)
    assert after.json() == before.json()  # adjust is deliberately not receive/ship turnover.
    balances = await env["client"].get("/api/inventory-history/daily-balances", params=params)
    assert balances.status_code == 200, balances.text
    assert balances.json()["items"][0]["days"][0]["closing_quantity"] == 12
    period = {"date_from": day, "date_to": day, "product_id": "inv-a"}
    history = await env["client"].get("/api/operations-history", params=period)
    summary = await env["client"].get("/api/operations-history/summary", params=period)
    assert history.status_code == summary.status_code == 200, (history.text, summary.text)
    assert summary.json()["total_operations"] == history.json()["total"]
    assert len([row for row in history.json()["items"] if row["operation_type"] == "adjust"]) == 1
