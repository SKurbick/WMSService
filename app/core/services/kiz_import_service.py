"""Raw ingestion и controlled B2 apply сообщений импорта КИЗ."""

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

import asyncpg
from app.core.kiz_code import normalize_kiz_code

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
    organization: str | None
    wild_group_count: int
    mark_code_count: int
    used_supply_guid_alias: bool


@dataclass(frozen=True)
class KizImportBusinessRequest:
    order_guid: str
    supply_number: str | None
    organization: str | None
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
        organization = None
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
                source_organization = supply.get("organization")
                if isinstance(source_organization, str) and source_organization.strip():
                    organization = source_organization.strip()

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
            organization=organization,
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
            organization=organization,
            wild_group_count=wild_group_count,
            mark_code_count=mark_code_count,
            used_supply_guid_alias=used_supply_guid_alias,
        )

    async def list_messages(self, limit: int, offset: int):
        return await self.repository.list_messages(limit, offset)

    async def get_message(self, message_id: int):
        return await self.repository.get_message(message_id)

    async def process_message(
        self, message_id: int, receipt_location_code: str | None = None
    ) -> dict[str, Any]:
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
                    return await self._build_replay_result(
                        connection,
                        message_id,
                        message["business_result"],
                    )

                try:
                    async with connection.transaction():
                        result = await self._apply_groups(
                            connection,
                            message_id=message_id,
                            message=message,
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

    async def _apply_groups(
        self, connection, *, message_id: int, message: dict[str, Any],
        receipt_location_code: str | None,
    ) -> dict[str, Any]:
        # Validate shared fields before applying any group. Keep raw inbox immutable.
        payload = message.get("raw_payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("wild_groups"), list):
            self._canonicalize(message)
        envelope = {**message, "raw_payload": {**payload, "wild_groups": []}}
        header = self._canonicalize(envelope)
        requests = []
        rejected = []
        owners: dict[str, set[int]] = {}
        duplicate_indexes = set()
        for index, group in enumerate(payload["wild_groups"]):
            try:
                request = self._canonicalize({
                    **message, "raw_payload": {**payload, "wild_groups": [group]},
                })
            except KizImportBusinessError as error:
                rejected.append(self._group_rejection(index, group, error))
                request = None
            requests.append(request)
            # Include valid codes from malformed groups in collision detection too:
            # never let group order choose which product owns an ambiguous identity.
            codes = group.get("mark_codes") if isinstance(group, dict) else None
            if isinstance(codes, list):
                for code in codes:
                    if isinstance(code, str) and code:
                        try:
                            code = normalize_kiz_code(code)
                        except ValueError:
                            continue
                        indexes = owners.setdefault(code, set())
                        indexes.add(index)
                        if len(indexes) > 1:
                            duplicate_indexes.update(indexes)

        results = []
        # Canonical product order matches the existing product/KIZ lock protocol.
        for index in sorted(range(len(requests)), key=lambda i: (
            next(iter(requests[i].codes_by_product), "") if requests[i] else "", i
        )):
            request = requests[index]
            if request is None:
                continue
            if index in duplicate_indexes:
                error = KizImportBusinessError(
                    "KIZ code повторяется в разных группах сообщения",
                    error_code="DUPLICATE_KIZ_CODE",
                )
                rejected.append(self._group_rejection(index, payload["wild_groups"][index], error))
                continue
            try:
                # A business failure rolls back this group's KIZ, events and links.
                # Database/system errors still roll back the whole delivery for retry.
                async with connection.transaction():
                    result = await self._apply_message(
                        connection, message_id=message_id, request=request,
                        receipt_location_code=receipt_location_code,
                    )
            except KizImportBusinessError as error:
                rejected.append(self._group_rejection(index, payload["wild_groups"][index], error))
            else:
                results.append(result)

        rejected.sort(key=lambda group: group["group_index"])
        if rejected and not results:
            first = rejected[0]
            raise KizImportBusinessError(
                json.dumps(rejected, ensure_ascii=False), error_code=first["error_code"],
            )

        # Preserve one successful result per product for legacy readers/replay.
        groups: dict[str, dict[str, Any]] = {}
        for result in results:
            for group in result["groups"]:
                product_id = group["product_id"]
                if product_id not in groups:
                    groups[product_id] = dict(group)
                else:
                    for field in ("requested_codes", "new_kiz", "existing_kiz", "terminal_existing"):
                        groups[product_id][field] += group[field]
        return {
            "message_id": message_id, "business_status": "applied",
            "order_guid": header.order_guid, "supply_number": header.supply_number,
            "organization": header.organization,
            **{field: sum(result[field] for result in results) for field in (
                "new_kiz_count", "existing_kiz_count", "terminal_existing_count",
            )},
            "groups": list(groups.values()),
            "is_partial": bool(rejected), "rejected_groups": rejected,
        }

    @staticmethod
    def _group_rejection(index, group, error) -> dict[str, Any]:
        return {
            "group_index": index,
            "product_id": group.get("wild") if isinstance(group, dict)
            and isinstance(group.get("wild"), str) else None,
            "error_code": error.error_code, "error_message": str(error),
        }

    async def get_integrity(self) -> dict[str, Any]:
        (
            capacity,
            orphans,
            holders,
            origins,
            invalid_links,
        ) = await self.repository.get_integrity_issues()
        return {
            "is_valid": not any((holders, origins, invalid_links)),
            "receipt_capacity_violations": [dict(row) for row in capacity],
            "orphan_receipt_kiz": [dict(row) for row in orphans],
            "registered_holder_violations": [dict(row) for row in holders],
            "registered_origin_violations": [dict(row) for row in origins],
            "invalid_message_kiz_links": [dict(row) for row in invalid_links],
        }

    async def _build_replay_result(
        self,
        connection,
        message_id: int,
        stored_result: dict[str, Any],
    ) -> dict[str, Any]:
        result = json.loads(json.dumps(stored_result, ensure_ascii=False, default=str))
        linked_rows = await self.repository.get_message_linked_kiz(connection, message_id)
        current_by_product: dict[str, int] = {}
        terminal_by_product: dict[str, int] = {}
        for row in linked_rows:
            target = (
                current_by_product
                if row["lifecycle_status"] in {"registered", "active"}
                else terminal_by_product
            )
            target[row["product_id"]] = target.get(row["product_id"], 0) + row["quantity"]

        for group in result["groups"]:
            product_id = group["product_id"]
            group["new_kiz"] = 0
            group["existing_kiz"] = current_by_product.get(product_id, 0)
            group["terminal_existing"] = terminal_by_product.get(product_id, 0)
        result["new_kiz_count"] = 0
        result["existing_kiz_count"] = sum(current_by_product.values())
        result["terminal_existing_count"] = sum(terminal_by_product.values())
        return result

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
                if not isinstance(code, str) or not code:
                    raise KizImportBusinessError(
                        "KIZ code должен быть непустой строкой",
                        error_code="INVALID_KIZ_CODE",
                    )
                try:
                    code = normalize_kiz_code(code)
                except ValueError as error:
                    raise KizImportBusinessError(
                        "KIZ code содержит NUL или некорректный Unicode",
                        error_code="INVALID_KIZ_CODE",
                    ) from error
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
        organization = supply.get("organization")
        if organization is not None:
            if not isinstance(organization, str) or not organization.strip():
                raise KizImportBusinessError(
                    "supply.organization должен быть непустой строкой",
                    error_code="INVALID_ORGANIZATION",
                )
            organization = organization.strip()
        return KizImportBusinessRequest(
            order_guid=resolved_guid,
            supply_number=supply_number,
            organization=organization,
            codes_by_product=codes_by_product,
        )

    async def _apply_message(
        self,
        connection,
        *,
        message_id: int,
        request: KizImportBusinessRequest,
        receipt_location_code: str | None,
    ) -> dict[str, Any]:
        original_codes = sorted(code for codes in request.codes_by_product.values() for code in codes)
        corrections = {
            row['kiz_code']: row
            for row in await self.repository.get_product_corrections(connection, original_codes)
        }
        resolved: dict[str, list[str]] = {}
        for product_id, codes in request.codes_by_product.items():
            for code in codes:
                correction = corrections.get(code)
                target = (
                    correction['new_product_id']
                    if correction and correction['old_product_id'] == product_id else product_id
                )
                resolved.setdefault(target, []).append(code)
            if not codes:
                resolved.setdefault(product_id, [])
        request = KizImportBusinessRequest(
            request.order_guid, request.supply_number, request.organization, resolved,
        )
        product_ids = sorted(request.codes_by_product)
        products = await self.repository.lock_products(connection, product_ids)
        missing_products = sorted(set(product_ids) - {row["id"] for row in products})
        if missing_products:
            raise KizImportBusinessError(
                f"Товары не найдены: {', '.join(missing_products)}",
                error_code="UNKNOWN_PRODUCT",
            )

        all_codes = sorted(code for codes in request.codes_by_product.values() for code in codes)
        existing_rows = await self.repository.lock_existing_codes(connection, all_codes)
        current_corrections = {
            row['kiz_code']: row
            for row in await self.repository.get_product_corrections(connection, original_codes)
        }
        if current_corrections != corrections:
            raise KizImportConcurrentConflictError('Коррекция товара КИЗ изменилась; повторите обработку')
        existing_by_code = {row["kiz_code"]: row for row in existing_rows}
        existing_current: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        existing_terminal: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        created_by_product: dict[str, list[Any]] = {product_id: [] for product_id in product_ids}
        metadata = {
            "source": self.SOURCE,
            "order_guid": request.order_guid,
            "supply_number": request.supply_number,
            "organization": request.organization,
            "kiz_import_message_id": message_id,
            "receipt_location_code": receipt_location_code,
        }
        for product_id in product_ids:
            for code in sorted(request.codes_by_product[product_id]):
                current = existing_by_code.get(code)
                created = False
                if current is None:
                    current = await self.repository.insert_kiz(
                        connection,
                        kiz_code=code,
                        product_id=product_id,
                        order_guid=request.order_guid,
                        organization=request.organization,
                        author=self.AUTHOR,
                        metadata=metadata,
                    )
                    created = current is not None
                    if current is None:
                        current = await self.repository.lock_kiz_by_code(connection, code)
                if created:
                    await self.repository.insert_registered_event(
                        connection,
                        kiz_id=current["kiz_id"],
                        product_id=product_id,
                        author=self.AUTHOR,
                        metadata=metadata,
                    )
                    created_by_product[product_id].append(current)
                else:
                    self._validate_existing_kiz(current, product_id, request.organization)
                    target = (
                        existing_current
                        if current["lifecycle_status"] in {"registered", "active"}
                        else existing_terminal
                    )
                    target[product_id].append(current)
                await self.repository.link_message_kiz(
                    connection, message_id, current["kiz_id"], created
                )

        diagnostics = {
            row["product_id"]: row
            for row in await self.repository.get_registration_context(
                connection, request.order_guid, product_ids, receipt_location_code
            )
        }
        groups = []
        for product_id in product_ids:
            context = diagnostics[product_id]
            physical = context["physical_quantity"]
            identified = context["identified_quantity"]
            groups.append(
                {
                    "product_id": product_id,
                    "receipt_quantity": _quantity(context["receipt_quantity"])
                    if context["receipt_quantity"] is not None
                    else None,
                    "requested_codes": len(request.codes_by_product[product_id]),
                    "new_kiz": len(created_by_product[product_id]),
                    "existing_kiz": len(existing_current[product_id]),
                    "terminal_existing": len(existing_terminal[product_id]),
                    "physical_quantity": _quantity(physical) if physical is not None else None,
                    "identified_quantity": identified,
                    "unidentified_quantity": _quantity(physical - identified)
                    if physical is not None and identified is not None
                    else None,
                }
            )

        return {
            "message_id": message_id,
            "business_status": "applied",
            "order_guid": request.order_guid,
            "supply_number": request.supply_number,
            "organization": request.organization,
            "new_kiz_count": sum(len(rows) for rows in created_by_product.values()),
            "existing_kiz_count": sum(len(rows) for rows in existing_current.values()),
            "terminal_existing_count": sum(len(rows) for rows in existing_terminal.values()),
            "groups": groups,
        }

    @staticmethod
    def _validate_existing_kiz(
        row,
        product_id: str,
        organization: str | None,
    ) -> None:
        if row is None or (
            row["product_id"] != product_id
            or row["origin_type"] != "receipt_import"
        ):
            code = row["kiz_code"] if row is not None else "unknown"
            raise KizImportBusinessError(
                f"KIZ {code} уже принадлежит другому origin/product",
                error_code="KIZ_OWNERSHIP_CONFLICT",
            )
        if (
            organization is not None
            and row["organization"] is not None
            and row["organization"] != organization
        ):
            raise KizImportBusinessError(
                f"KIZ {row['kiz_code']} уже принадлежит другой организации",
                error_code="KIZ_ORGANIZATION_CONFLICT",
            )
