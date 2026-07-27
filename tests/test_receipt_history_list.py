from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import ReceiptHistoryValidationError
from app.core.receipt_history_ids import (
    build_receipt_history_row_id,
    build_receipt_revision_id,
    build_receipt_snapshot_row_id,
    encode_receipt_guid,
)
from app.core.services.receipt_history_service import ReceiptHistoryService
from app.infrastructure.database.queries import receipt_history as queries


AT = datetime(2026, 7, 22, 11, 23, 22, tzinfo=timezone.utc)


class FakeRepository:
    counts = {"total": 0, "total_documents": 0}
    rows = []

    async def get_history_list(self, filters, limit, offset):
        self.call = (filters, limit, offset)
        return self.counts, self.rows


def event(**changes):
    row = {
        "source_type": "legacy_revision",
        "guid": "document-guid",
        "revision_key_at": datetime(2026, 7, 22, 14, 23, 22),
        "fallback_id": None,
        "max_legacy_row_id": 100,
        "revision_at": AT,
        "is_current": True,
        "has_current_snapshot": True,
        "snapshot_updated_at": AT,
        "document_number": "ПТУ-123",
        "document_created_at": AT,
        "supply_date": AT,
        "update_document_datetime": AT,
        "event_status": "Проведён",
        "supplier_name": "Поставщик",
        "supplier_code": "SUP-1",
        "author_of_the_change": "Иванов",
        "our_organizations_name": "Организация",
        "order_guid": "order",
        "currency": "RUB",
        "invoice_number": "INV",
        "transport_number": "TR",
        "item_count": 3,
        "product_count": 2,
        "total_quantity": Decimal("16.50"),
        "has_filtered_product": True,
    }
    row.update(changes)
    return row


def test_id_codec_is_base64url_unpadded_and_epoch_is_exact():
    assert encode_receipt_guid("document-guid") == "ZG9jdW1lbnQtZ3VpZA"
    assert "=" not in encode_receipt_guid("document-guid")
    assert build_receipt_revision_id(AT, None).endswith("1784719402000000")
    assert build_receipt_history_row_id("document-guid", AT, None).startswith(
        "receipt_revision:ZG9jdW1lbnQtZ3VpZA:"
    )
    assert build_receipt_snapshot_row_id("wms-only") == "receipt_snapshot:d21zLW9ubHk"


@pytest.mark.asyncio
async def test_list_maps_legacy_and_wms_only_rows_with_full_totals():
    repo = FakeRepository()
    repo.counts = {"total": 2, "total_documents": 2}
    repo.rows = [
        event(),
        event(
            source_type="wms_snapshot_only",
            guid="wms-only",
            revision_key_at=None,
            fallback_id=None,
            max_legacy_row_id=None,
            invoice_number=None,
            transport_number=None,
            item_count=2,
            product_count=2,
            total_quantity=Decimal("7"),
            has_current_snapshot=True,
        ),
    ]
    result = await ReceiptHistoryService(repo).list_history(date(2026, 7, 1), date(2026, 7, 31))
    assert result.total == 2 and result.total_documents == 2
    assert result.items[0].revision_id.startswith("receipt_revision:")
    assert result.items[0].total_quantity == Decimal("16.50")
    assert result.items[1].revision_id is None
    assert result.items[1].row_id == "receipt_snapshot:d21zLW9ubHk"
    assert '"total_quantity":16.5' in result.model_dump_json()


@pytest.mark.asyncio
async def test_undated_revision_uses_legacy_ids():
    repo = FakeRepository()
    repo.counts = {"total": 1, "total_documents": 1}
    repo.rows = [event(revision_key_at=None, revision_at=None, fallback_id=77)]
    result = await ReceiptHistoryService(repo).list_history(
        date(2026, 7, 1), date(2026, 7, 31), include_undated=True
    )
    assert result.items[0].revision_id == "receipt_revision:legacy:77"
    assert result.items[0].row_id.endswith(":legacy:77")


@pytest.mark.asyncio
async def test_filters_are_forwarded_before_pagination():
    repo = FakeRepository()
    await ReceiptHistoryService(repo).list_history(
        date(2026, 7, 1),
        date(2026, 7, 31),
        "legacy_revision",
        "g",
        "doc",
        "supplier",
        "code",
        "status",
        "author",
        "order",
        "sku",
        True,
        True,
        10,
        5,
    )
    assert repo.call[0][2:] == (
        "legacy_revision",
        "g",
        "doc",
        "supplier",
        "code",
        "status",
        "author",
        "order",
        "sku",
        True,
        True,
    )
    assert repo.call[1:] == (10, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": date(2026, 8, 1), "date_to": date(2026, 7, 1)},
        {"date_from": date(2025, 1, 1), "date_to": date(2026, 1, 2)},
        {"date_from": date(2026, 7, 1), "date_to": date(2026, 7, 2), "source_type": "bad"},
    ],
)
async def test_domain_validation(kwargs):
    with pytest.raises(ReceiptHistoryValidationError):
        await ReceiptHistoryService(FakeRepository()).list_history(**kwargs)


def test_sql_keeps_grouping_totals_and_filters_separate():
    sql = queries.GET_RECEIPT_HISTORY_LIST
    assert "GROUP BY guid, revision_key_at, fallback_id" in sql
    assert "max(id) AS max_legacy_row_id" in sql
    assert "bool_or(is_valid IS TRUE)" in sql
    assert "count(*)::bigint AS item_count" in sql
    assert "count(DISTINCT local_vendor_code)" in sql
    assert "COALESCE(sum(quantity), 0::numeric)" in sql
    assert "has_filtered_product" in sql
    assert "LEFT JOIN legacy_guids" in sql and "lg.guid IS NULL" in sql
    assert "AT TIME ZONE 'Europe/Moscow'" in sql
    assert "LIMIT $14 OFFSET $15" in sql


def test_static_route_precedes_dynamic_detail_and_both_exist():
    from app.main import app

    paths = [route.path for route in app.routes]
    assert paths.index("/api/receipts/history") < paths.index("/api/receipts/{guid}/history")
