"""Internal Phase 3 API; it never owns or commits a database transaction."""

import json
from typing import Any, Mapping

from app.core.exceptions import KizOperationIdempotencyConflictError
from app.core.json_validation import validate_jsonb_value
from app.core.kiz_operation_idempotency import (
    KizOperationAcquisition,
    KizOperationDisposition,
    kiz_operation_fingerprint,
)


class KizOperationIdempotencyService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _require_transaction(conn):
        if not conn.is_in_transaction():
            raise RuntimeError("KIZ operation infrastructure requires an open transaction")

    @staticmethod
    def _row(row):
        value = dict(row)
        if isinstance(value.get("result_payload"), str):
            value["result_payload"] = json.loads(value["result_payload"])
        return value

    async def acquire(
        self,
        conn,
        *,
        operation_type: str,
        source_system: str,
        external_operation_id: str,
        author: str,
        intent: Mapping[str, Any],
    ) -> KizOperationAcquisition:
        """Acquire the idempotency row before future inventory/KIZ locks."""
        self._require_transaction(conn)
        if intent.get("operation_type") != operation_type:
            raise ValueError("operation_type должен совпадать с physical intent")
        fingerprint = kiz_operation_fingerprint(intent)
        created = await self.repository.try_create(
            conn,
            operation_type=operation_type,
            source_system=source_system,
            external_operation_id=external_operation_id,
            request_fingerprint=fingerprint,
            author=author,
        )
        if created is not None:
            return KizOperationAcquisition(
                KizOperationDisposition.NEW, self._row(created)
            )

        existing = await self.repository.get_for_update(
            conn,
            source_system=source_system,
            operation_type=operation_type,
            external_operation_id=external_operation_id,
        )
        if existing is None:
            raise RuntimeError("Idempotency row disappeared after unique conflict")
        operation = self._row(existing)
        if operation["request_fingerprint"] != fingerprint:
            raise KizOperationIdempotencyConflictError(
                "Idempotency key уже зарегистрирован для другого KIZ physical intent",
                operation_id=operation["operation_id"],
            )
        if operation["result_payload"] is None:
            raise RuntimeError("Committed KIZ operation has no stored result")
        return KizOperationAcquisition(KizOperationDisposition.REPLAY, operation)

    async def create_item(self, conn, *, operation_id: int, external_line_id: str):
        self._require_transaction(conn)
        row = await self.repository.create_item(
            conn,
            operation_id=operation_id,
            external_line_id=external_line_id,
        )
        return self._row(row)

    async def attach_movement(
        self, conn, *, operation_item_id: int, movement_ref: int
    ):
        self._require_transaction(conn)
        row = await self.repository.attach_movement(
            conn,
            operation_item_id=operation_item_id,
            movement_ref=movement_ref,
        )
        if row is None:
            raise RuntimeError("KIZ operation item not found or movement already attached")
        return self._row(row)

    async def store_successful_result(
        self, conn, *, operation_id: int, result_payload: Mapping[str, Any]
    ):
        self._require_transaction(conn)
        validate_jsonb_value(result_payload)
        encoded = json.dumps(
            result_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        row = await self.repository.store_result(
            conn, operation_id=operation_id, result_payload=encoded
        )
        if row is None:
            raise RuntimeError("KIZ operation not found or result already stored")
        return self._row(row)

    async def load_stored_result(self, conn, *, operation_id: int):
        self._require_transaction(conn)
        row = await self.repository.get(conn, operation_id=operation_id)
        if row is None:
            return None
        return self._row(row)["result_payload"]
