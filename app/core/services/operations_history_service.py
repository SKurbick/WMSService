"""Сервис списка и typed detail бизнес-операций."""

import json
from datetime import date
from typing import Any

from app.core.exceptions import (
    LocationNotFoundError,
    OperationsHistoryEventIdError,
    OperationsHistoryNotFoundError,
    OperationsHistoryValidationError,
)
from app.core.operations_history import (
    ParsedOperationEventId,
    build_fbs_event_id,
    build_kit_event_id,
    build_movement_event_id,
    build_re_sorting_event_id,
    get_operation_name,
    parse_operation_event_id,
)
from app.core.schemas.operations_history import OperationHistoryItem, OperationsHistoryResponse
from app.core.schemas.operations_history_detail import (
    FbsShipmentDetailItem,
    FbsShipmentDetailResponse,
    FbsShipmentHeader,
    KitOperationDetailItem,
    KitOperationDetailResponse,
    KitOperationHeader,
    MovementDetail,
    MovementDetailResponse,
    MovementHeader,
    OperationWarning,
    ReSortingOperationDetailItem,
    ReSortingOperationDetailResponse,
    ReSortingOperationHeader,
)
from app.infrastructure.database.repositories.operations_history_repository import (
    OperationsHistoryRepository,
)


class OperationsHistoryService:
    TIMEZONE = "Europe/Moscow"
    SOURCE_TYPES = frozenset({"kit_operation", "re_sorting_operation", "fbs_shipment", "movement"})
    OPERATION_TYPES = frozenset(
        {
            "receive",
            "putaway",
            "transfer",
            "pick",
            "ship",
            "unpack",
            "adjust",
            "kit_assembly",
            "kit_disassembly",
            "re_sorting",
            "fbs_shipment",
        }
    )

    def __init__(self, repository: OperationsHistoryRepository):
        self.repository = repository

    async def get_operations(
        self,
        date_from: date,
        date_to: date,
        source_type: str | None = None,
        operation_type: str | None = None,
        product_id: str | None = None,
        location_id: int | None = None,
        author: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> OperationsHistoryResponse:
        if date_from > date_to:
            raise OperationsHistoryValidationError("date_from не может быть позже date_to")
        if (date_to - date_from).days + 1 > 366:
            raise OperationsHistoryValidationError("Период не может превышать 366 календарных дней")
        if source_type is not None and source_type not in self.SOURCE_TYPES:
            raise OperationsHistoryValidationError(f"Неизвестный source_type: {source_type}")
        if operation_type is not None and operation_type not in self.OPERATION_TYPES:
            raise OperationsHistoryValidationError(f"Неизвестный operation_type: {operation_type}")
        location_exists, total, rows = await self.repository.get_page(
            date_from,
            date_to,
            source_type,
            operation_type,
            product_id,
            location_id,
            author,
            status,
            limit,
            offset,
        )
        if not location_exists:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")
        items = []
        for record in rows:
            row = dict(record)
            row["event_id"] = self._canonical_event_id(row)
            row["operation_name"] = get_operation_name(row["operation_type"])
            items.append(OperationHistoryItem.model_validate(row))
        return OperationsHistoryResponse(
            date_from=date_from,
            date_to=date_to,
            timezone=self.TIMEZONE,
            total=total,
            limit=limit,
            offset=offset,
            items=items,
        )

    async def get_operation_detail(self, event_id: str):
        try:
            parsed = parse_operation_event_id(event_id)
        except ValueError as exc:
            raise OperationsHistoryEventIdError(str(exc)) from exc
        if parsed.prefix == "kit_operation":
            return await self._get_kit_detail(parsed)
        if parsed.prefix == "re_sorting":
            return await self._get_re_sorting_detail(parsed)
        if parsed.prefix == "fbs_shipment":
            return await self._get_fbs_detail(parsed)
        return await self._get_movement_detail(parsed)

    @staticmethod
    def _canonical_event_id(row: dict) -> str:
        parsed = parse_operation_event_id(row["event_id"])
        if parsed.prefix == "kit_operation":
            return build_kit_event_id(parsed.entity_id)
        if parsed.prefix == "re_sorting":
            return build_re_sorting_event_id(parsed.entity_id)
        if parsed.prefix == "fbs_shipment":
            return build_fbs_event_id(parsed.entity_id)
        return build_movement_event_id(parsed.entity_id, row["created_at"])

    @staticmethod
    def _decode_json(value: Any, default):
        if isinstance(value, str):
            return json.loads(value) if value else default
        return default if value is None else value

    @classmethod
    def _movement(cls, row: dict, prefix: str = "") -> MovementDetail:
        get = lambda name: row.get(f"{prefix}{name}")
        created_at, movement_id = get("created_at"), get("movement_id")
        return MovementDetail(
            event_id=build_movement_event_id(movement_id, created_at),
            movement_id=movement_id,
            movement_type=get("movement_type"),
            product_id=get("product_id"),
            product_name=get("product_name"),
            quantity=get("quantity"),
            from_location_id=get("from_location_id"),
            from_location_code=get("from_location_code"),
            to_location_id=get("to_location_id"),
            to_location_code=get("to_location_code"),
            batch_number=get("batch_number"),
            container_code=get("container_code"),
            user_name=get("user_name"),
            reason=get("reason"),
            source_type=get("source_type"),
            source_id=get("source_id"),
            source_item_id=get("source_item_id"),
            metadata=cls._decode_json(get("metadata"), {}),
            created_at=created_at,
        )

    @staticmethod
    def _warning(status: str, movement_id: int, reference: str) -> OperationWarning:
        if status == "missing":
            return OperationWarning(
                code="missing_movement_link",
                message=f"Movement {movement_id} was not found",
                reference=reference,
            )
        return OperationWarning(
            code="ambiguous_movement_link",
            message=f"Movement {movement_id} resolved to multiple rows",
            reference=reference,
        )

    async def _get_kit_detail(self, parsed: ParsedOperationEventId):
        header_row, rows = await self.repository.get_kit_detail(parsed.entity_id)
        if not header_row:
            raise OperationsHistoryNotFoundError(f"Kit operation {parsed.entity_id} не найдена")
        header = KitOperationHeader.model_validate(dict(header_row))
        items, movements, warnings = self._linked_operation_items(rows, kind="kit")
        operation_type = {"assembly": "kit_assembly", "disassembly": "kit_disassembly"}.get(
            header.operation_type, header.operation_type
        )
        return KitOperationDetailResponse(
            event_id=build_kit_event_id(header.operation_id),
            source_type="kit_operation",
            operation_type=operation_type,
            operation_name=get_operation_name(operation_type),
            status=header.status,
            created_at=header.created_at,
            completed_at=header.completed_at,
            author=header.author,
            header=header,
            items=items,
            movements=movements,
            warnings=warnings,
        )

    async def _get_re_sorting_detail(self, parsed: ParsedOperationEventId):
        header_row, rows = await self.repository.get_re_sorting_detail(parsed.entity_id)
        if not header_row:
            raise OperationsHistoryNotFoundError(
                f"Re-sorting operation {parsed.entity_id} не найдена"
            )
        header = ReSortingOperationHeader.model_validate(dict(header_row))
        items, movements, warnings = self._linked_operation_items(rows, kind="re_sorting")
        return ReSortingOperationDetailResponse(
            event_id=build_re_sorting_event_id(header.operation_id),
            source_type="re_sorting_operation",
            operation_type="re_sorting",
            operation_name=get_operation_name("re_sorting"),
            status=header.status,
            created_at=header.created_at,
            completed_at=header.completed_at,
            author=header.author,
            header=header,
            items=items,
            movements=movements,
            warnings=warnings,
        )

    def _linked_operation_items(self, rows, *, kind: str):
        grouped = {}
        for record in rows:
            row = dict(record)
            grouped.setdefault(row["item_id"], []).append(row)
        items, movements, warnings = [], {}, []
        for item_rows in grouped.values():
            row = item_rows[0]
            candidates = [r for r in item_rows if r["candidate_movement_id"] is not None]
            status = (
                "resolved" if len(candidates) == 1 else "missing" if not candidates else "ambiguous"
            )
            keys = (
                (
                    "item_id",
                    "role",
                    "product_id",
                    "product_name",
                    "quantity_per_kit",
                    "total_quantity",
                    "movement_id",
                    "movement_created_at",
                )
                if kind == "kit"
                else (
                    "item_id",
                    "role",
                    "product_id",
                    "product_name",
                    "quantity",
                    "movement_id",
                    "movement_created_at",
                )
            )
            payload = {key: row[key] for key in keys}
            payload["movement_link_status"] = status
            model = KitOperationDetailItem if kind == "kit" else ReSortingOperationDetailItem
            items.append(model.model_validate(payload))
            for candidate in candidates:
                movement = self._movement(candidate, "candidate_")
                movements.setdefault(movement.event_id, movement)
            if status != "resolved":
                warnings.append(self._warning(status, row["movement_id"], f"item:{row['item_id']}"))
        return items, list(movements.values()), warnings

    async def _get_fbs_detail(self, parsed: ParsedOperationEventId):
        header_row, item_rows, movement_rows = await self.repository.get_fbs_detail(
            parsed.entity_id
        )
        if not header_row:
            raise OperationsHistoryNotFoundError(f"FBS shipment {parsed.entity_id} не найден")
        header_payload = dict(header_row)
        header_payload["raw_message"] = self._decode_json(header_payload["raw_message"], [])
        header = FbsShipmentHeader.model_validate(header_payload)
        candidates = {}
        for movement_row in movement_rows:
            movement = self._movement(dict(movement_row))
            candidates.setdefault(movement.movement_id, []).append(movement)
        items, warnings, movements, authors = [], [], {}, []
        for record in item_rows:
            row = dict(record)
            row["assembly_tasks"] = self._decode_json(row["assembly_tasks"], [])
            movement_id = row["movement_id"]
            matches = candidates.get(movement_id, []) if movement_id is not None else []
            if movement_id is None:
                link_status, movement_event_id = "not_linked", None
            elif len(matches) == 1:
                link_status, movement_event_id = "resolved", matches[0].event_id
            elif not matches:
                link_status, movement_event_id = "missing", None
            else:
                link_status, movement_event_id = "ambiguous", None
            row["movement_link_status"], row["movement_event_id"] = link_status, movement_event_id
            items.append(FbsShipmentDetailItem.model_validate(row))
            for movement in matches:
                movements.setdefault(movement.event_id, movement)
            if link_status in {"missing", "ambiguous"}:
                warnings.append(self._warning(link_status, movement_id, f"item:{row['item_id']}"))
            authors.append(row.get("author"))
        nonempty_authors = [value for value in authors if value]
        author_values = set(nonempty_authors)
        author = (
            next(iter(author_values))
            if len(author_values) == 1 and len(authors) == len(nonempty_authors)
            else None
        )
        return FbsShipmentDetailResponse(
            event_id=build_fbs_event_id(header.shipment_id),
            source_type="fbs_shipment",
            operation_type="fbs_shipment",
            operation_name=get_operation_name("fbs_shipment"),
            status=header.status,
            created_at=header.received_at,
            completed_at=header.completed_at,
            author=author,
            header=header,
            items=items,
            movements=list(movements.values()),
            warnings=warnings,
        )

    async def _get_movement_detail(self, parsed: ParsedOperationEventId):
        rows = await self.repository.get_movement_detail(
            parsed.entity_id, parsed.movement_created_at
        )
        if not rows:
            raise OperationsHistoryNotFoundError(f"Movement {parsed.entity_id} не найден")
        movements = [self._movement(dict(row)) for row in rows]
        movement = movements[0]
        return MovementDetailResponse(
            event_id=build_movement_event_id(movement.movement_id, movement.created_at),
            source_type="movement",
            operation_type=movement.movement_type,
            operation_name=get_operation_name(movement.movement_type),
            status=None,
            created_at=movement.created_at,
            completed_at=None,
            author=movement.user_name,
            header=MovementHeader.model_validate(movement.model_dump()),
            items=[],
            movements=movements,
            warnings=[],
        )
