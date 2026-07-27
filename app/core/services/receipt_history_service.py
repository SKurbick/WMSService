"""Сборка typed истории документа поступления."""

from app.core.exceptions import ReceiptHistoryNotFoundError, ReceiptHistoryValidationError
from datetime import date

from app.core.receipt_history_ids import (
    build_receipt_history_row_id,
    build_receipt_revision_id,
    build_receipt_snapshot_row_id,
)
from app.core.schemas.receipt_history import (
    ReceiptHistoryResponse,
    ReceiptHistoryListItem,
    ReceiptHistoryListResponse,
    ReceiptRevision,
    ReceiptRevisionItem,
    ReceiptSnapshot,
    ReceiptSnapshotItem,
)
from app.infrastructure.database.repositories.receipt_history_repository import (
    ReceiptHistoryRepository,
)


class ReceiptHistoryService:
    SOURCE_TYPES = frozenset({"legacy_revision", "wms_snapshot_only"})

    def __init__(self, repository: ReceiptHistoryRepository):
        self.repository = repository

    async def get_history(self, guid: str, limit: int = 50, offset: int = 0):
        if not guid or not guid.strip():
            raise ReceiptHistoryValidationError("guid не может быть пустым")
        if not 1 <= limit <= 100 or offset < 0:
            raise ReceiptHistoryValidationError("Некорректные limit/offset")
        snapshot_rows, total, header_rows, item_rows = await self.repository.get_history(
            guid, limit, offset
        )
        if not snapshot_rows and total == 0:
            raise ReceiptHistoryNotFoundError(f"Документ поступления {guid} не найден")

        snapshot = self._snapshot(snapshot_rows) if snapshot_rows else None
        grouped = {}
        for record in item_rows:
            row = dict(record)
            grouped.setdefault(self._key(row), []).append(row)
        revisions = []
        for record in header_rows:
            row = dict(record)
            revision_at = row["revision_at"]
            revision_id = build_receipt_revision_id(revision_at, row["fallback_id"])
            payload = {
                key: row[key]
                for key in (
                    "revision_at",
                    "is_current",
                    "document_number",
                    "document_created_at",
                    "supply_date",
                    "update_document_datetime",
                    "event_status",
                    "supplier_name",
                    "supplier_code",
                    "author_of_the_change",
                    "our_organizations_name",
                    "order_guid",
                    "currency",
                    "invoice_number",
                    "transport_number",
                )
            }
            payload["revision_id"] = revision_id
            payload["items"] = [
                ReceiptRevisionItem.model_validate(item) for item in grouped.get(self._key(row), [])
            ]
            revisions.append(ReceiptRevision.model_validate(payload))

        latest_snapshot_row = (
            max(snapshot_rows, key=lambda row: (row["updated_at"], row["receipt_item_id"]))
            if snapshot_rows
            else None
        )
        document_number = (
            latest_snapshot_row["document_number"]
            if latest_snapshot_row
            else (header_rows[0]["document_number"] if header_rows else None)
        )
        return ReceiptHistoryResponse(
            guid=guid,
            document_number=document_number,
            total_revisions=total,
            limit=limit,
            offset=offset,
            current_snapshot=snapshot,
            revisions=revisions,
        )

    async def list_history(
        self,
        date_from: date,
        date_to: date,
        source_type: str | None = None,
        guid: str | None = None,
        document_number: str | None = None,
        supplier_name: str | None = None,
        supplier_code: str | None = None,
        event_status: str | None = None,
        author: str | None = None,
        order_guid: str | None = None,
        product_id: str | None = None,
        is_current: bool | None = None,
        include_undated: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> ReceiptHistoryListResponse:
        if date_from > date_to:
            raise ReceiptHistoryValidationError("date_from не может быть позже date_to")
        if (date_to - date_from).days + 1 > 366:
            raise ReceiptHistoryValidationError("Период не может превышать 366 календарных дней")
        if source_type is not None and source_type not in self.SOURCE_TYPES:
            raise ReceiptHistoryValidationError(f"Неизвестный source_type: {source_type}")
        if not 1 <= limit <= 100 or offset < 0:
            raise ReceiptHistoryValidationError("Некорректные limit/offset")
        filters = (
            date_from,
            date_to,
            source_type,
            guid,
            document_number,
            supplier_name,
            supplier_code,
            event_status,
            author,
            order_guid,
            product_id,
            is_current,
            include_undated,
        )
        counts, rows = await self.repository.get_history_list(filters, limit, offset)
        items = []
        for record in rows:
            row = dict(record)
            if row["source_type"] == "legacy_revision":
                row["revision_id"] = build_receipt_revision_id(
                    row["revision_at"], row["fallback_id"]
                )
                row["row_id"] = build_receipt_history_row_id(
                    row["guid"], row["revision_at"], row["fallback_id"]
                )
            else:
                row["revision_id"] = None
                row["row_id"] = build_receipt_snapshot_row_id(row["guid"])
            items.append(ReceiptHistoryListItem.model_validate(row))
        return ReceiptHistoryListResponse(
            date_from=date_from,
            date_to=date_to,
            timezone="Europe/Moscow",
            total=counts["total"],
            total_documents=counts["total_documents"],
            limit=limit,
            offset=offset,
            items=items,
        )

    @staticmethod
    def _key(row):
        return (row.get("revision_key_at"), row.get("fallback_id"))

    @staticmethod
    def _snapshot(rows):
        header = max(rows, key=lambda row: (row["updated_at"], row["receipt_item_id"]))
        revision_at = (
            header["update_document_datetime"]
            or header["document_created_at"]
            or header["supply_date"]
        )
        return ReceiptSnapshot(
            revision_at=revision_at,
            event_status=header["event_status"],
            document_created_at=header["document_created_at"],
            supply_date=header["supply_date"],
            update_document_datetime=header["update_document_datetime"],
            supplier_name=header["supplier_name"],
            supplier_code=header["supplier_code"],
            author_of_the_change=header["author_of_the_change"],
            our_organizations_name=header["our_organizations_name"],
            order_guid=header["order_guid"],
            currency=header["currency"],
            items=[ReceiptSnapshotItem.model_validate(dict(row)) for row in rows],
        )
