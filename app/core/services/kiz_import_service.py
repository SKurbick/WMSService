"""Raw ingestion и controlled B2 apply сообщений импорта КИЗ."""

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

import asyncpg

from app.core.exceptions import (
    KizImportBusinessError,
    KizImportConcurrentConflictError,
    KizImportMessageNotFoundError,
)
from app.infrastructure.database.repositories.kiz_import_repository import KizImportRepository


@dataclass(frozen=True)
class KizImportIngestionResult:
    message_id: int
    parse_status: str
    order_guid: str | None
    supply_number: str | None
    wild_group_count: int
    mark_code_count: int
    used_supply_guid_alias: bool


@dataclass(frozen=True)
class KizImportBusinessRequest:
    order_guid: str
    supply_number: str | None
    codes_by_product: dict[str, list[str]]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


def _quantity(value: Decimal | int) -> str:
    return format(Decimal(value), "f")


class KizImportService:
    AUTHOR = "kiz-import"
    SOURCE = "orders.kiz.imported"
    CONCURRENT_SQLSTATES = {"40001", "40P01", "23505", "23514", "P7501"}

    def __init__(self, repository: KizImportRepository):
        self.repository = repository

    async def ingest(
        self,
        body: bytes,
        *,
        exchange_name: str | None,
        routing_key: str | None,
        rabbit_message_id: str | None,
        correlation_id: str | None,
        headers: Mapping[str, Any] | None,
    ) -> KizImportIngestionResult:
        parse_error = None
        raw_payload = None
        try:
            raw_body = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raw_body = f"base64:{base64.b64encode(body).decode('ascii')}"
            parse_error = f"body is not valid UTF-8: {error}"

        if parse_error is None:
            try:
                raw_payload = json.loads(raw_body)
            except json.JSONDecodeError as error:
                parse_error = str(error)

        parse_status = "received" if parse_error is None else "invalid_json"
        order_guid = None
        supply_number = None
        used_supply_guid_alias = False
        wild_group_count = 0
        mark_code_count = 0

        if isinstance(raw_payload, dict):
            supply = raw_payload.get("supply")
            if isinstance(supply, dict):
                if supply.get("order_guid") is not None:
                    order_guid = str(supply["order_guid"])
                elif supply.get("supply_guid") is not None:
                    order_guid = str(supply["supply_guid"])
                    used_supply_guid_alias = True
                if supply.get("supply_number") is not None:
                    supply_number = str(supply["supply_number"])

            wild_groups = raw_payload.get("wild_groups")
            if isinstance(wild_groups, list):
                wild_group_count = len(wild_groups)
                mark_code_count = sum(
                    len(group.get("mark_codes", []))
                    for group in wild_groups
                    if isinstance(group, dict) and isinstance(group.get("mark_codes"), list)
                )

        row = await self.repository.create_message(
            raw_body=raw_body,
            raw_payload=raw_payload,
            parse_status=parse_status,
            parse_error=parse_error,
            order_guid=order_guid,
            supply_number=supply_number,
            wild_group_count=wild_group_count,
            mark_code_count=mark_code_count,
            exchange_name=exchange_name,
            routing_key=routing_key,
            rabbit_message_id=rabbit_message_id,
            correlation_id=correlation_id,
            headers=_json_safe(headers or {}),
        )
        return KizImportIngestionResult(
            message_id=row["message_id"],
            parse_status=parse_status,
            order_guid=order_guid,
            supply_number=supply_number,
            wild_group_count=wild_group_count,
            mark_code_count=mark_code_count,
            used_supply_guid_alias=used_supply_guid_alias,
        )

    async def list_messages(self, limit: int, offset: int):
        return await self.repository.list_messages(limit, offset)

    async def get_message(self, message_id: int):
        return await self.repository.get_message(message_id)

    async def process_message(self, message_id: int, receipt_location_code: str) -> dict[str, Any]:
        rejection: KizImportBusinessError | None = None
        result: dict[str, Any] | None = None
        try:
            async with self.repository.transaction() as connection:
                message = await self.repository.lock_message(connection, message_id)
                if message is None:
                    raise KizImportMessageNotFoundError(
                        f"KIZ import message {message_id} не найдено"
                    )
                if message["business_status"] == "applied":
                    return message["business_result"]

                try:
                    request = self._canonicalize(message)
                    async with connection.transaction():
                        result = await self._apply_message(
                            connection,
                            message_id=message_id,
                            request=request,
                            receipt_location_code=receipt_location_code,
                        )
                except KizImportBusinessError as error:
                    rejection = error
                    await self.repository.mark_rejected(
                        connection,
                        message_id,
                        error.error_code,
                        str(error),
                    )
                else:
                    await self.repository.mark_applied(connection, message_id, result)
        except asyncpg.PostgresError as error:
            if error.sqlstate in self.CONCURRENT_SQLSTATES:
                raise KizImportConcurrentConflictError(
                    "Состояние KIZ/остатка изменилось конкурентно; повторите обработку"
                ) from error
            raise

        if rejection is not None:
            raise rejection
        return result

    async def get_integrity(self) -> dict[str, Any]:
        capacity, orphans = await self.repository.get_integrity_issues()
        return {
            "is_valid": not capacity and not orphans,
            "receipt_capacity_violations": [dict(row) for row in capacity],
            "orphan_receipt_kiz": [dict(row) for row in orphans],
        }

    def _canonicalize(self, message: dict[str, Any]) -> KizImportBusinessRequest:
        if message["parse_status"] != "received" or not isinstance(message["raw_payload"], dict):
            raise KizImportBusinessError(
                "Сообщение не содержит корректный JSON object",
                error_code="INVALID_RAW_PAYLOAD",
            )

        payload = message["raw_payload"]
        supply = payload.get("supply")
        if not isinstance(supply, dict):
            raise KizImportBusinessError(
                "Поле supply обязательно и должно быть object",
                error_code="INVALID_SUPPLY",
            )

        order_guid = supply.get("order_guid")
        supply_guid = supply.get("supply_guid")
        if order_guid is not None and supply_guid is not None and order_guid != supply_guid:
            raise KizImportBusinessError(
                "supply.order_guid и supply.supply_guid различаются",
                error_code="ORDER_GUID_MISMATCH",
            )
        resolved_guid = order_guid if order_guid is not None else supply_guid
        if not isinstance(resolved_guid, str) or not resolved_guid:
            raise KizImportBusinessError(
                "supply.order_guid обязателен",
                error_code="ORDER_GUID_REQUIRED",
            )

        wild_groups = payload.get("wild_groups")
        if not isinstance(wild_groups, list):
            raise KizImportBusinessError(
                "Поле wild_groups обязательно и должно быть array",
                error_code="INVALID_WILD_GROUPS",
            )

        codes_by_product: dict[str, list[str]] = {}
        seen_codes: set[str] = set()
        for group in wild_groups:
            if not isinstance(group, dict):
                raise KizImportBusinessError(
                    "Каждый элемент wild_groups должен быть object",
                    error_code="INVALID_WILD_GROUP",
                )
            product_id = group.get("wild")
            codes = group.get("mark_codes")
            if not isinstance(product_id, str) or not product_id:
                raise KizImportBusinessError(
                    "wild обязателен и должен быть непустой строкой",
                    error_code="INVALID_PRODUCT_ID",
                )
            if not isinstance(codes, list):
                raise KizImportBusinessError(
                    f"mark_codes для {product_id} должен быть array",
                    error_code="INVALID_MARK_CODES",
                )
            product_codes = codes_by_product.setdefault(product_id, [])
            for code in codes:
                if not isinstance(code, str) or not code or code[0].isspace() or code[-1].isspace():
                    raise KizImportBusinessError(
                        "KIZ code должен быть непустой строкой без крайних пробелов",
                        error_code="INVALID_KIZ_CODE",
                    )
                if code in seen_codes:
                    raise KizImportBusinessError(
                        f"KIZ code повторяется в сообщении: {code}",
                        error_code="DUPLICATE_KIZ_CODE",
                    )
                seen_codes.add(code)
                product_codes.append(code)

        supply_number = supply.get("supply_number")
        if supply_number is not None:
            supply_number = str(supply_number)
        return KizImportBusinessRequest(
            order_guid=resolved_guid,
            supply_number=supply_number,
            codes_by_product=codes_by_product,
        )

    async def _apply_message(
        self,
        connection,
        *,
        message_id: int,
        request: KizImportBusinessRequest,
        receipt_location_code: str,
    ) -> dict[str, Any]:
        product_ids = sorted(request.codes_by_product)
        location = await self.repository.lock_receipt_location(
            connection,
            receipt_location_code,
        )
        if location is None:
            raise KizImportBusinessError(
                f"Активная receipt location {receipt_location_code} не найдена",
                error_code="RECEIPT_LOCATION_NOT_FOUND",
            )
        location_id = location["location_id"]

        products = await self.repository.lock_products(connection, product_ids)
        existing_products = {row["id"] for row in products}
        missing_products = sorted(set(product_ids) - existing_products)
        if missing_products:
            raise KizImportBusinessError(
                f"Товары не найдены: {', '.join(missing_products)}",
                error_code="UNKNOWN_PRODUCT",
            )

        receipt_rows = await self.repository.lock_receipt_items(
            connection,
            request.order_guid,
        )
        if not receipt_rows:
            raise KizImportBusinessError(
                f"Поступление {request.order_guid} не найдено",
                error_code="UNKNOWN_RECEIPT",
            )
        receipt_by_product = {row["product_id"]: Decimal(row["quantity"]) for row in receipt_rows}
        missing_receipt_products = sorted(set(product_ids) - set(receipt_by_product))
        if missing_receipt_products:
            raise KizImportBusinessError(
                "Товары отсутствуют в поступлении: " + ", ".join(missing_receipt_products),
                error_code="PRODUCT_NOT_IN_RECEIPT",
            )

        inventory_rows = await self.repository.lock_loose_inventory(
            connection,
            location_id,
            product_ids,
        )
        physical_by_product = {product_id: Decimal("0") for product_id in product_ids}
        for row in inventory_rows:
            physical_by_product[row["product_id"]] += Decimal(row["quantity"])

        all_codes = sorted(code for codes in request.codes_by_product.values() for code in codes)
        existing_rows = await self.repository.lock_existing_codes(connection, all_codes)
        await self.repository.lock_active_receipt_kiz(
            connection,
            request.order_guid,
            product_ids,
        )
        await self.repository.lock_active_loose_kiz(
            connection,
            location_id,
            product_ids,
        )

        existing_by_code = {row["kiz_code"]: row for row in existing_rows}
        active_receipt_counts = await self.repository.count_active_receipt_kiz(
            connection,
            request.order_guid,
            product_ids,
        )
        active_loose_counts = await self.repository.count_active_loose_kiz(
            connection,
            location_id,
            product_ids,
        )

        planned_new: dict[str, list[str]] = {}
        existing_active: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        existing_terminal: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        for product_id in product_ids:
            new_codes = []
            for code in request.codes_by_product[product_id]:
                current = existing_by_code.get(code)
                if current is None:
                    new_codes.append(code)
                    continue
                self._validate_existing_kiz(current, product_id, request.order_guid)
                target = (
                    existing_active
                    if current["lifecycle_status"] == "active"
                    else existing_terminal
                )
                target[product_id].append(current)
            planned_new[product_id] = new_codes

            receipt_quantity = receipt_by_product[product_id]
            current_receipt_count = active_receipt_counts.get(product_id, 0)
            if current_receipt_count > receipt_quantity:
                raise KizImportBusinessError(
                    f"Active receipt KIZ уже превышают quantity для {product_id}",
                    error_code="RECEIPT_KIZ_INTEGRITY_CONFLICT",
                )
            if Decimal(current_receipt_count + len(new_codes)) > receipt_quantity:
                raise KizImportBusinessError(
                    f"Недостаточно receipt quantity для {product_id}",
                    error_code="RECEIPT_QUANTITY_EXCEEDED",
                )

            physical_quantity = physical_by_product[product_id]
            identified_quantity = active_loose_counts.get(product_id, 0)
            if Decimal(identified_quantity) > physical_quantity:
                raise KizImportBusinessError(
                    f"Active KIZ уже превышают loose physical для {product_id}",
                    error_code="PHYSICAL_KIZ_INTEGRITY_CONFLICT",
                )
            if Decimal(identified_quantity + len(new_codes)) > physical_quantity:
                raise KizImportBusinessError(
                    f"Недостаточно unidentified loose units для {product_id}",
                    error_code="INSUFFICIENT_UNIDENTIFIED_QUANTITY",
                )

        metadata = {
            "source": self.SOURCE,
            "order_guid": request.order_guid,
            "supply_number": request.supply_number,
            "kiz_import_message_id": message_id,
        }
        created_by_product: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        for product_id in product_ids:
            for code in sorted(planned_new[product_id]):
                created = await self.repository.insert_kiz(
                    connection,
                    kiz_code=code,
                    product_id=product_id,
                    location_id=location_id,
                    order_guid=request.order_guid,
                    author=self.AUTHOR,
                    metadata=metadata,
                )
                if created is None:
                    current = await self.repository.lock_kiz_by_code(connection, code)
                    self._validate_existing_kiz(current, product_id, request.order_guid)
                    target = (
                        existing_active
                        if current["lifecycle_status"] == "active"
                        else existing_terminal
                    )
                    target[product_id].append(current)
                    continue
                await self.repository.insert_assigned_event(
                    connection,
                    kiz_id=created["kiz_id"],
                    product_id=product_id,
                    location_id=location_id,
                    author=self.AUTHOR,
                    metadata=metadata,
                )
                created_by_product[product_id].append(created)

        for product_id in product_ids:
            for row in created_by_product[product_id]:
                await self.repository.link_message_kiz(
                    connection,
                    message_id,
                    row["kiz_id"],
                    True,
                )
            for row in existing_active[product_id] + existing_terminal[product_id]:
                await self.repository.link_message_kiz(
                    connection,
                    message_id,
                    row["kiz_id"],
                    False,
                )

        final_receipt_counts = await self.repository.count_active_receipt_kiz(
            connection,
            request.order_guid,
            product_ids,
        )
        final_loose_counts = await self.repository.count_active_loose_kiz(
            connection,
            location_id,
            product_ids,
        )
        groups = []
        for product_id in product_ids:
            receipt_quantity = receipt_by_product[product_id]
            physical_quantity = physical_by_product[product_id]
            receipt_identified = final_receipt_counts.get(product_id, 0)
            identified = final_loose_counts.get(product_id, 0)
            if (
                Decimal(receipt_identified) > receipt_quantity
                or Decimal(identified) > physical_quantity
            ):
                raise KizImportBusinessError(
                    f"Final KIZ invariant нарушен для {product_id}",
                    error_code="FINAL_KIZ_INTEGRITY_CONFLICT",
                )
            groups.append(
                {
                    "product_id": product_id,
                    "receipt_quantity": _quantity(receipt_quantity),
                    "requested_codes": len(request.codes_by_product[product_id]),
                    "new_kiz": len(created_by_product[product_id]),
                    "existing_kiz": len(existing_active[product_id]),
                    "terminal_existing": len(existing_terminal[product_id]),
                    "physical_quantity": _quantity(physical_quantity),
                    "identified_quantity": identified,
                    "unidentified_quantity": _quantity(physical_quantity - identified),
                }
            )

        return {
            "message_id": message_id,
            "business_status": "applied",
            "order_guid": request.order_guid,
            "supply_number": request.supply_number,
            "new_kiz_count": sum(len(rows) for rows in created_by_product.values()),
            "existing_kiz_count": sum(len(rows) for rows in existing_active.values()),
            "terminal_existing_count": sum(len(rows) for rows in existing_terminal.values()),
            "groups": groups,
        }

    @staticmethod
    def _validate_existing_kiz(row, product_id: str, order_guid: str) -> None:
        if row is None or (
            row["product_id"] != product_id
            or row["origin_type"] != "receipt_import"
            or row["origin_reference"] != order_guid
        ):
            code = row["kiz_code"] if row is not None else "unknown"
            raise KizImportBusinessError(
                f"KIZ {code} уже принадлежит другому origin/product",
                error_code="KIZ_OWNERSHIP_CONFLICT",
            )
