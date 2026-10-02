"""Atomic registered-to-active KIZ activation."""

import hashlib
import json
from collections import Counter
from decimal import Decimal
from typing import Any

import asyncpg

from app.core.exceptions import (
    KizActivationNotFoundError,
    KizConflictError,
)
from app.core.schemas.kiz_activation import KizActivationRequest
from app.infrastructure.database.repositories.kiz_activation_repository import (
    KizActivationRepository,
)


class KizActivationService:
    def __init__(self, repository: KizActivationRepository):
        self.repository = repository

    async def activate(self, request: KizActivationRequest) -> dict[str, Any]:
        request_hash = self._request_hash(request)
        try:
            async with self.repository.transaction() as connection:
                existing_operation = await self.repository.lock_operation_by_key(
                    connection,
                    request.idempotency_key,
                )
                if existing_operation is not None:
                    return self._replay_operation(existing_operation, request_hash)

                location = await self.repository.lock_location(
                    connection,
                    request.location_code,
                )
                if location is None:
                    raise KizActivationNotFoundError(
                        f"Локация '{request.location_code}' не найдена",
                        error_code="LOCATION_NOT_FOUND",
                    )
                if not location["is_active"]:
                    raise KizConflictError(f"Локация '{request.location_code}' неактивна")

                operation, was_created = await self.repository.create_or_lock_operation(
                    connection,
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                    user_name=request.user_name,
                )
                if operation is None:
                    raise KizConflictError("Не удалось заблокировать activation operation")
                if not was_created:
                    return self._replay_operation(operation, request_hash)

                sorted_codes = sorted(request.kiz_codes)
                rows = await self.repository.lock_kiz_by_codes(connection, sorted_codes)
                by_code = {row["kiz_code"]: row for row in rows}
                missing = [code for code in sorted_codes if code not in by_code]
                if missing:
                    raise KizActivationNotFoundError(
                        "KIZ не найдены: " + ", ".join(missing),
                        error_code="KIZ_NOT_FOUND",
                    )

                to_activate = []
                already_active = []
                for code in sorted_codes:
                    kiz = by_code[code]
                    if kiz["lifecycle_status"] == "registered":
                        if kiz["location_id"] is not None or kiz["container_id"] is not None:
                            raise KizConflictError(
                                f"Registered KIZ '{code}' имеет недопустимый holder"
                            )
                        to_activate.append(kiz)
                        continue
                    if kiz["lifecycle_status"] == "active":
                        if (
                            kiz["location_id"] == location["location_id"]
                            and kiz["container_id"] is None
                        ):
                            already_active.append(kiz)
                            continue
                        raise KizConflictError(f"KIZ '{code}' уже active в другом holder")
                    raise KizConflictError(
                        f"KIZ '{code}' имеет terminal lifecycle '{kiz['lifecycle_status']}'"
                    )

                new_by_product = Counter(row["product_id"] for row in to_activate)
                for product_id in sorted(new_by_product):
                    await self.repository.lock_product_scope(
                        connection,
                        location["location_id"],
                        product_id,
                    )
                    inventory = await self.repository.lock_loose_inventory(
                        connection,
                        product_id,
                        location["location_id"],
                    )
                    physical = Decimal(inventory["quantity"]) if inventory else Decimal("0")
                    identified = await self.repository.count_active_loose_kiz(
                        connection,
                        product_id,
                        location["location_id"],
                    )
                    if Decimal(identified + new_by_product[product_id]) > physical:
                        raise KizConflictError(
                            f"Недостаточно unidentified loose stock для '{product_id}'"
                        )

                operation_id = operation["operation_id"]
                for kiz in to_activate:
                    await self.repository.create_operation_item(
                        connection,
                        operation_id=operation_id,
                        kiz=kiz,
                    )
                for kiz in already_active:
                    await self.repository.create_operation_item(
                        connection,
                        operation_id=operation_id,
                        kiz=kiz,
                    )

                metadata = {
                    "operation_id": operation_id,
                    "operation_type": "activate",
                    "location_code": location["location_code"],
                    "idempotency_key": request.idempotency_key,
                }
                for kiz in to_activate:
                    await self.repository.authorize_activation(
                        connection,
                        operation_id=operation_id,
                        kiz=kiz,
                        location_id=location["location_id"],
                    )
                    activated = await self.repository.activate_kiz(
                        connection,
                        kiz["kiz_id"],
                        location["location_id"],
                    )
                    if activated is None:
                        raise KizConflictError(f"KIZ '{kiz['kiz_code']}' изменился конкурентно")
                    await self.repository.create_activated_event(
                        connection,
                        kiz=kiz,
                        location_id=location["location_id"],
                        user_name=request.user_name,
                        metadata={**metadata, "kiz_code": kiz["kiz_code"]},
                    )

                result = {
                    "operation_id": operation_id,
                    "location_code": location["location_code"],
                    "activated_count": len(to_activate),
                    "already_active_count": len(already_active),
                    "items": [
                        {
                            "kiz_code": row["kiz_code"],
                            "product_id": row["product_id"],
                            "lifecycle_status": "active",
                            "location_code": location["location_code"],
                        }
                        for row in rows
                    ],
                }
                await self.repository.complete_operation(
                    connection,
                    operation_id,
                    result,
                )
                return result
        except KizActivationNotFoundError:
            raise
        except KizConflictError:
            raise
        except asyncpg.PostgresError as error:
            if error.sqlstate in {"23505", "23514", "40001", "40P01", "P7501"}:
                raise KizConflictError(str(error)) from error
            raise

    @staticmethod
    def _request_hash(request: KizActivationRequest) -> str:
        canonical = json.dumps(
            {
                "kiz_codes": sorted(request.kiz_codes),
                "location_code": request.location_code,
                "user_name": request.user_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode_json(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)

    @classmethod
    def _replay_operation(cls, operation: Any, request_hash: str) -> dict[str, Any]:
        if operation["request_fingerprint"] != request_hash:
            raise KizConflictError("Idempotency key уже использован для другого activation intent")
        if operation["result_payload"] is None:
            raise KizConflictError("Activation operation ещё не завершена")
        return cls._decode_json(operation["result_payload"])
