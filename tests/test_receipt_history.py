from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import ReceiptHistoryNotFoundError, ReceiptHistoryValidationError
from app.core.services.receipt_history_service import ReceiptHistoryService


UTC = timezone.utc
NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)
NAIVE = datetime(2026, 7, 22, 12, 30)


class FakeRepository:
    result = ([], 0, [], [])

    async def get_history(self, guid, limit, offset):
        self.args = (guid, limit, offset)
        return self.result


def snapshot_row(**changes):
    row = {
        "receipt_item_id": 1,
        "guid": "g",
        "product_id": "sku",
        "product_name": "Товар",
        "quantity": Decimal("10.50"),
        "document_number": "ПТУ-1",
        "supplier_name": "Поставщик",
        "supplier_code": "SUP",
        "created_at": NOW,
        "updated_at": NOW,
        "document_created_at": NOW,
        "supply_date": NOW,
        "update_document_datetime": NOW,
        "event_status": "Проведён",
        "author_of_the_change": "Иванов",
        "our_organizations_name": "Орг",
        "order_guid": "order",
        "currency": "RUB",
    }
    row.update(changes)
    return row


def header(**changes):
    row = {
        "revision_key_at": NAIVE,
        "revision_at": NOW,
        "fallback_id": None,
        "max_legacy_row_id": 10,
        "is_current": True,
        "document_number": "ПТУ-1",
        "document_created_at": NOW,
        "supply_date": NOW,
        "update_document_datetime": NOW,
        "event_status": "Проведён",
        "supplier_name": "Поставщик",
        "supplier_code": "SUP",
        "author_of_the_change": "Иванов",
        "our_organizations_name": "Орг",
        "order_guid": "order",
        "currency": "RUB",
        "invoice_number": None,
        "transport_number": None,
    }
    row.update(changes)
    return row


def item(**changes):
    row = {
        "legacy_row_id": 10,
        "revision_key_at": NAIVE,
        "fallback_id": None,
        "product_id": "sku",
        "product_name": "Товар",
        "quantity": Decimal("10.50"),
        "amount_with_vat": Decimal("1000"),
        "amount_without_vat": Decimal("833.33"),
        "planned_cost": Decimal("900"),
        "pack_count": None,
        "pack_multiplicity": None,
        "is_valid": True,
    }
    row.update(changes)
    return row


@pytest.mark.asyncio
async def test_history_builds_snapshot_and_one_revision():
    repo = FakeRepository()
    repo.result = ([snapshot_row()], 1, [header()], [item()])
    response = await ReceiptHistoryService(repo).get_history("g")
    assert response.total_revisions == 1
    assert response.current_snapshot.items[0].quantity == Decimal("10.50")
    assert response.revisions[0].items[0].legacy_row_id == 10
    assert response.revisions[0].revision_id.startswith("receipt_revision:")
    assert '"quantity":10.5' in response.model_dump_json()


@pytest.mark.asyncio
async def test_duplicate_products_are_not_aggregated():
    repo = FakeRepository()
    repo.result = ([], 1, [header()], [item(), item(legacy_row_id=11)])
    response = await ReceiptHistoryService(repo).get_history("g")
    assert [x.legacy_row_id for x in response.revisions[0].items] == [10, 11]


@pytest.mark.asyncio
async def test_fallback_revision_is_separate_and_stable():
    repo = FakeRepository()
    h = header(revision_key_at=None, revision_at=None, fallback_id=77)
    repo.result = ([], 1, [h], [item(revision_key_at=None, fallback_id=77)])
    response = await ReceiptHistoryService(repo).get_history("g")
    assert response.revisions[0].revision_id == "receipt_revision:legacy:77"


@pytest.mark.asyncio
async def test_legacy_only_and_snapshot_only_are_200_models():
    repo = FakeRepository()
    repo.result = ([], 1, [header()], [item()])
    assert (await ReceiptHistoryService(repo).get_history("g")).current_snapshot is None
    repo.result = ([snapshot_row()], 0, [], [])
    assert (await ReceiptHistoryService(repo).get_history("g")).revisions == []


@pytest.mark.asyncio
async def test_latest_snapshot_row_provides_header_but_all_items_remain():
    repo = FakeRepository()
    repo.result = (
        [
            snapshot_row(updated_at=NOW, event_status="old"),
            snapshot_row(
                receipt_item_id=2,
                product_id="z",
                updated_at=NOW.replace(second=1),
                event_status="new",
            ),
        ],
        0,
        [],
        [],
    )
    response = await ReceiptHistoryService(repo).get_history("g")
    assert response.current_snapshot.event_status == "new"
    assert len(response.current_snapshot.items) == 2


@pytest.mark.asyncio
async def test_unknown_and_invalid_requests():
    with pytest.raises(ReceiptHistoryNotFoundError):
        await ReceiptHistoryService(FakeRepository()).get_history("missing")
    with pytest.raises(ReceiptHistoryValidationError):
        await ReceiptHistoryService(FakeRepository()).get_history(" ")
    with pytest.raises(ReceiptHistoryValidationError):
        await ReceiptHistoryService(FakeRepository()).get_history("g", 101, 0)


def test_route_registered_without_operations_history_conflict():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/receipts/{guid}/history" in paths
    assert "/api/operations-history/{event_id}" in paths
