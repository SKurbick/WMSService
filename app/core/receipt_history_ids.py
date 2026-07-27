"""Read-only identifiers строк и ревизий поступлений."""

import base64
from datetime import datetime

from app.core.operations_history import datetime_to_epoch_us


def encode_receipt_guid(guid: str) -> str:
    return base64.urlsafe_b64encode(guid.encode("utf-8")).decode("ascii").rstrip("=")


def build_receipt_revision_id(revision_at: datetime | None, legacy_row_id: int | None) -> str:
    if revision_at is not None:
        return f"receipt_revision:{datetime_to_epoch_us(revision_at)}"
    return f"receipt_revision:legacy:{legacy_row_id}"


def build_receipt_history_row_id(
    guid: str, revision_at: datetime | None, legacy_row_id: int | None
) -> str:
    encoded_guid = encode_receipt_guid(guid)
    if revision_at is not None:
        return f"receipt_revision:{encoded_guid}:{datetime_to_epoch_us(revision_at)}"
    return f"receipt_revision:{encoded_guid}:legacy:{legacy_row_id}"


def build_receipt_snapshot_row_id(guid: str) -> str:
    return f"receipt_snapshot:{encode_receipt_guid(guid)}"
