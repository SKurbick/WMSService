"""Idempotency boundary for caller-owned container operation transactions."""

import json

from app.core.container_operation_idempotency import (
    ContainerOperationAcquisition,
    ContainerOperationDisposition,
    container_operation_fingerprint,
)
from app.core.exceptions import ContainerOperationIdempotencyConflictError
from app.core.json_validation import validate_jsonb_value


class ContainerOperationIdempotencyService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _row(row):
        value = dict(row)
        if isinstance(value.get("result_payload"), str):
            value["result_payload"] = json.loads(value["result_payload"])
        return value

    @staticmethod
    def _require_transaction(conn):
        if not conn.is_in_transaction():
            raise RuntimeError("Container operation requires an open transaction")

    async def acquire(self, conn, *, data, intent):
        self._require_transaction(conn)
        operation_type = intent["operation_type"]
        fingerprint = container_operation_fingerprint(intent)
        created = await self.repository.try_create(
            conn,
            operation_type=operation_type,
            container_id=data.container_id,
            source_system=data.source_system,
            external_operation_id=data.external_operation_id,
            fingerprint=fingerprint,
            author=data.author,
        )
        if created is not None:
            return ContainerOperationAcquisition(
                ContainerOperationDisposition.NEW, self._row(created)
            )

        existing = await self.repository.get_for_update(
            conn,
            operation_type=operation_type,
            source_system=data.source_system,
            external_operation_id=data.external_operation_id,
        )
        if existing is None:
            raise RuntimeError("Container idempotency row disappeared")
        operation = self._row(existing)
        if operation["request_fingerprint"] != fingerprint:
            raise ContainerOperationIdempotencyConflictError(
                "Idempotency key уже зарегистрирован для другого container operation intent",
                operation_id=operation["operation_id"],
            )
        if operation["result_payload"] is None:
            raise RuntimeError("Committed container operation has no stored result")
        return ContainerOperationAcquisition(ContainerOperationDisposition.REPLAY, operation)

    async def store_result(self, conn, *, operation_id, payload):
        self._require_transaction(conn)
        validate_jsonb_value(payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        row = await self.repository.store_result(
            conn, operation_id=operation_id, result_payload=encoded
        )
        if row is None:
            raise RuntimeError("Container operation result already stored")
