"""Сборка typed истории документа поступления."""

from app.core.exceptions import ReceiptHistoryNotFoundError, ReceiptHistoryValidationError
from app.core.operations_history import datetime_to_epoch_us
from app.core.schemas.receipt_history import (
    ReceiptHistoryResponse,
    ReceiptRevision,
    ReceiptRevisionItem,
    ReceiptSnapshot,
    ReceiptSnapshotItem,
)
from app.infrastructure.database.repositories.receipt_history_repository import (
    ReceiptHistoryRepository,
)


class ReceiptHistoryService:
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
            revision_id = (
                f"receipt_revision:{datetime_to_epoch_us(revision_at)}"
                if revision_at is not None
                else f"receipt_revision:legacy:{row['fallback_id']}"
            )
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
