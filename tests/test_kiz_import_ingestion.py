import json

import pytest

from app import consumer
from app.api.v1.endpoints import kiz_import as endpoint
from app.core.services.kiz_import_service import KizImportService
from app.infrastructure.database.queries import kiz_import as queries


class RecordingRepository:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.rows = []

    async def create_message(self, **values):
        if self.fail:
            raise RuntimeError("database failed")
        row = {"message_id": len(self.rows) + 1, **values}
        self.rows.append(row)
        return row

    async def list_messages(self, limit, offset):
        return self.rows[offset : offset + limit]

    async def get_message(self, message_id):
        return next((row for row in self.rows if row["message_id"] == message_id), None)


async def ingest(repository, body: bytes):
    return await KizImportService(repository).ingest(
        body,
        exchange_name="orders",
        routing_key="orders.kiz.imported",
        rabbit_message_id="rabbit-1",
        correlation_id="correlation-1",
        headers={"attempt": 1, "binary": b"\x00\xff"},
    )


@pytest.mark.asyncio
async def test_valid_payload_is_saved_raw_with_diagnostics_and_special_symbols():
    repository = RecordingRepository()
    raw_body = (
        '{"supply":{"order_guid":"order-1","supply_number":"SUP-1"},'
        '"wild_groups":[{"wild":"testwild","mark_codes":["dgfhgdgfg!\\"NAk",'
        '"dfhjjdfhjdh%zbvf"]},{"wild":"testwild2","mark_codes":["K3"]}]}'
    )

    result = await ingest(repository, raw_body.encode())

    saved = repository.rows[0]
    assert saved["raw_body"] == raw_body
    assert saved["raw_payload"]["wild_groups"][0]["mark_codes"] == [
        'dgfhgdgfg!"NAk',
        "dfhjjdfhjdh%zbvf",
    ]
    assert result.order_guid == "order-1"
    assert result.supply_number == "SUP-1"
    assert result.wild_group_count == 2
    assert result.mark_code_count == 3
    assert result.parse_status == "received"
    assert saved["headers"]["binary"] == {"encoding": "base64", "value": "AP8="}


@pytest.mark.asyncio
async def test_supply_guid_alias_preserves_raw_and_reports_mismatch():
    repository = RecordingRepository()
    raw_body = '{"supply":{"supply_guid":"legacy-guid"}}'

    result = await ingest(repository, raw_body.encode())

    assert repository.rows[0]["raw_body"] == raw_body
    assert repository.rows[0]["raw_payload"] == {"supply": {"supply_guid": "legacy-guid"}}
    assert result.order_guid == "legacy-guid"
    assert result.used_supply_guid_alias is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"wild_groups": []},
        {"unexpected": {"nested": "preserved"}},
    ],
)
async def test_missing_and_unknown_fields_are_saved(payload):
    repository = RecordingRepository()

    result = await ingest(repository, json.dumps(payload).encode())

    assert repository.rows[0]["raw_payload"] == payload
    assert result.parse_status == "received"
    assert result.order_guid is None


@pytest.mark.asyncio
async def test_invalid_json_and_non_utf8_body_are_saved_for_diagnostics():
    invalid_json_repository = RecordingRepository()
    await ingest(invalid_json_repository, b'{"broken":')

    invalid_json = invalid_json_repository.rows[0]
    assert invalid_json["raw_body"] == '{"broken":'
    assert invalid_json["raw_payload"] is None
    assert invalid_json["parse_status"] == "invalid_json"
    assert invalid_json["parse_error"]

    binary_repository = RecordingRepository()
    await ingest(binary_repository, b"\xff\x00")

    binary = binary_repository.rows[0]
    assert binary["raw_body"] == "base64:/wA="
    assert binary["parse_status"] == "invalid_json"
    assert "not valid UTF-8" in binary["parse_error"]


@pytest.mark.asyncio
async def test_duplicate_delivery_creates_two_rows():
    repository = RecordingRepository()
    body = b'{"supply":{"order_guid":"order-1"}}'

    first = await ingest(repository, body)
    second = await ingest(repository, body)

    assert [first.message_id, second.message_id] == [1, 2]
    assert len(repository.rows) == 2


class FakeTransaction:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        self.state.append("transaction_enter")

    async def __aexit__(self, exc_type, exc, traceback):
        self.state.append("rollback" if exc_type else "commit")
        return False


class FakeConnection:
    def __init__(self, state, *, fail=False):
        self.state = state
        self.fail = fail

    def transaction(self):
        return FakeTransaction(self.state)

    async def fetchrow(self, query, *args):
        assert query == queries.INSERT_MESSAGE
        self.state.append("insert")
        if self.fail:
            raise RuntimeError("database failed")
        return {"message_id": 42}


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, state, *, fail=False):
        self.connection = FakeConnection(state, fail=fail)

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeMessage:
    def __init__(self, state):
        self.state = state
        self.body = b'{"supply":{"order_guid":"order-1"}}'
        self.exchange = "orders"
        self.routing_key = "orders.kiz.imported"
        self.message_id = "rabbit-1"
        self.correlation_id = "correlation-1"
        self.headers = {}
        self.acked = False
        self.nacked = False

    async def ack(self):
        assert self.state[-1] == "commit"
        self.state.append("ack")
        self.acked = True

    async def nack(self, *, requeue):
        self.state.append("nack")
        self.nacked = True
        assert requeue is True


@pytest.mark.asyncio
async def test_consumer_acks_only_after_database_commit(monkeypatch):
    state = []
    message = FakeMessage(state)

    async def fake_get_pool():
        return FakePool(state)

    monkeypatch.setattr(consumer, "get_db_pool", fake_get_pool)

    await consumer._process_kiz_import_message(message, queue_name="orders.kiz.imported")

    assert state == ["transaction_enter", "insert", "commit", "ack"]
    assert message.acked is True
    assert message.nacked is False


@pytest.mark.asyncio
async def test_database_failure_nacks_with_requeue_and_does_not_ack(monkeypatch):
    state = []
    message = FakeMessage(state)

    async def fake_get_pool():
        return FakePool(state, fail=True)

    monkeypatch.setattr(consumer, "get_db_pool", fake_get_pool)

    await consumer._process_kiz_import_message(message, queue_name="orders.kiz.imported")

    assert state == ["transaction_enter", "insert", "rollback", "nack"]
    assert message.acked is False
    assert message.nacked is True


def test_kiz_import_routes_are_registered_with_single_b2_write_boundary():
    methods_by_path = {route.path: route.methods for route in endpoint.router.routes}

    assert methods_by_path["/kiz-import/integrity"] == {"GET"}
    assert methods_by_path["/kiz-import/messages"] == {"GET"}
    assert methods_by_path["/kiz-import/messages/{message_id}/process"] == {"POST"}
    assert methods_by_path["/kiz-import/messages/{message_id}"] == {"GET"}


def test_kiz_import_sql_has_no_wms_business_side_effects():
    sql = queries.INSERT_MESSAGE.lower()
    assert "wms.kiz_import_messages" in sql
    for forbidden_table in (
        "wms.kiz ",
        "wms.kiz_events",
        "wms.kiz_movement_links",
        "wms.movements",
        "wms.inventory",
        "wms.receipt_items",
    ):
        assert forbidden_table not in sql
