"""Сервис комплектации и разукомплектации комплектов."""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from app.core.enums import KitOperationItemRole, KitOperationStatus, KitOperationType, MovementType
from app.core.exceptions import (
    KitOperationConflictError,
    KitOperationNotFoundError,
    KitOperationValidationError,
    LocationNotFoundError,
    ProductNotFoundError,
)
from app.core.schemas.kit_operations import (
    KitOperationCreate,
    KitOperationItemResponse,
    KitOperationLocationCreate,
    KitOperationLocationDeactivate,
    KitOperationLocationListResponse,
    KitOperationLocationResponse,
    KitOperationResponse,
    KitOperationSummaryResponse,
)
from app.infrastructure.database.repositories.kit_operation_repository import KitOperationRepository


class KitOperationService:
    """Бизнес-логика операций с комплектами."""

    SOURCE_TYPE = "kit_operation"

    def __init__(self, repository: KitOperationRepository):
        self.repository = repository

    async def create_operation(self, data: KitOperationCreate) -> KitOperationResponse:
        pool = self.repository.pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                location, operation_location = await self._validate_operation_location(
                    conn, data.location_code
                )
                kit_components = await self._validate_kit_and_components(conn, data.kit_product_id)
                location_id = location["location_id"]

                await self.repository.lock_operation_scope(conn, data.kit_product_id, location_id)
                await self._check_consumption_stock(
                    conn=conn,
                    operation_type=data.operation_type,
                    kit_product_id=data.kit_product_id,
                    kit_components=kit_components,
                    quantity=data.quantity,
                    location_id=location_id,
                )

                operation = await self.repository.create_operation(
                    conn,
                    operation_type=data.operation_type.value,
                    kit_product_id=data.kit_product_id,
                    quantity=data.quantity,
                    operation_location_id=operation_location["operation_location_id"],
                    location_id=location_id,
                    location_code=location["location_code"],
                    author=data.author,
                )
                items = await self._create_items_and_movements(
                    conn=conn,
                    operation_id=operation["operation_id"],
                    operation_type=data.operation_type,
                    kit_product_id=data.kit_product_id,
                    kit_components=kit_components,
                    quantity=data.quantity,
                    location_id=location_id,
                    author=data.author,
                )
                completed = await self.repository.complete_operation(conn, operation["operation_id"])

        return self._build_response(completed, completed["location_code"], items)

    async def list_operation_locations(
        self, *, is_active: Optional[bool] = None, limit: int = 50, offset: int = 0
    ) -> KitOperationLocationListResponse:
        rows = await self.repository.list_kit_operation_locations(
            is_active=is_active, limit=limit, offset=offset
        )
        total = await self.repository.count_kit_operation_locations(is_active=is_active)
        return KitOperationLocationListResponse(
            items=[self._operation_location_response(row) for row in rows],
            limit=limit,
            offset=offset,
            total=total,
        )

    async def create_operation_location(
        self, data: KitOperationLocationCreate
    ) -> KitOperationLocationResponse:
        pool = self.repository.pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                location = await self.repository.get_location_by_code(conn, data.location_code)
                if not location:
                    raise LocationNotFoundError(f"Location '{data.location_code}' not found")
                if not location["is_active"]:
                    raise KitOperationConflictError("Location inactive")

                row = await self.repository.create_or_reactivate_kit_operation_location(
                    conn,
                    location_id=location["location_id"],
                    location_code=location["location_code"],
                    author=data.author,
                    metadata=json.dumps(data.metadata or {}, ensure_ascii=False),
                )
        return self._operation_location_response(row, location_name=location["name"])

    async def deactivate_operation_location(
        self, operation_location_id: int, data: KitOperationLocationDeactivate
    ) -> KitOperationLocationResponse:
        pool = self.repository.pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await self.repository.get_kit_operation_location(conn, operation_location_id)
                if not existing:
                    raise KitOperationNotFoundError(
                        f"Kit operation location {operation_location_id} not found"
                    )
                if not existing["is_active"]:
                    return self._operation_location_response(existing)
                row = await self.repository.deactivate_kit_operation_location(
                    conn, operation_location_id, data.author
                )
        return self._operation_location_response(row)

    async def get_operation(self, operation_id: int) -> KitOperationResponse:
        operation = await self.repository.get_operation(operation_id)
        if not operation:
            raise KitOperationNotFoundError(f"Kit operation {operation_id} not found")
        items = await self.repository.get_items(operation_id)
        return self._build_response(operation, operation["location_code"], items)

    async def list_operations(
        self,
        *,
        operation_type: Optional[KitOperationType] = None,
        kit_product_id: Optional[str] = None,
        status: Optional[KitOperationStatus] = None,
        location_code: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[KitOperationSummaryResponse]:
        rows = await self.repository.list_operations(
            operation_type=operation_type.value if operation_type else None,
            kit_product_id=kit_product_id,
            status=status.value if status else None,
            location_code=location_code,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return [KitOperationSummaryResponse.model_validate(dict(row)) for row in rows]

    async def _validate_operation_location(self, conn, location_code: str):
        location = await self.repository.get_location_by_code(conn, location_code)
        if not location:
            raise LocationNotFoundError(f"Location '{location_code}' not found")
        if not location["is_active"]:
            raise KitOperationConflictError("Location inactive")

        operation_location = await self.repository.get_active_kit_operation_location(
            conn, location["location_code"], location["location_id"]
        )
        if not operation_location:
            raise KitOperationConflictError(
                "Kit operation is not allowed on this location"
            )
        return location, operation_location

    async def _validate_kit_and_components(self, conn, kit_product_id: str) -> Dict[str, Decimal]:
        kit_product = await self.repository.get_kit_product(conn, kit_product_id)
        if not kit_product:
            raise ProductNotFoundError(f"Kit product '{kit_product_id}' not found")
        if not kit_product["is_active"]:
            raise KitOperationConflictError("Kit product inactive")
        if not kit_product["is_kit"]:
            raise KitOperationConflictError("Kit product is not a kit")

        components = self._parse_components(kit_product["kit_components"])
        component_rows = await self.repository.get_products_by_ids(conn, list(components.keys()))
        found = {row["id"]: row for row in component_rows}
        for product_id in components:
            if product_id not in found:
                raise ProductNotFoundError(f"Component product '{product_id}' not found")
            if not found[product_id]["is_active"]:
                raise KitOperationConflictError(f"Component product '{product_id}' inactive")
        return components

    def _parse_components(self, raw_components) -> Dict[str, Decimal]:
        if isinstance(raw_components, str):
            try:
                raw_components = json.loads(raw_components)
            except json.JSONDecodeError as exc:
                raise KitOperationValidationError("kit_components invalid") from exc
        if not isinstance(raw_components, dict) or not raw_components:
            raise KitOperationValidationError("kit_components empty or invalid")

        components: Dict[str, Decimal] = {}
        for product_id, quantity_per_kit in raw_components.items():
            try:
                quantity = Decimal(str(quantity_per_kit))
            except (InvalidOperation, TypeError) as exc:
                raise KitOperationValidationError("quantity_per_kit invalid") from exc
            if quantity <= 0:
                raise KitOperationValidationError("quantity_per_kit must be greater than 0")
            components[str(product_id)] = quantity
        return components

    async def _check_consumption_stock(
        self,
        *,
        conn,
        operation_type: KitOperationType,
        kit_product_id: str,
        kit_components: Dict[str, Decimal],
        quantity: Decimal,
        location_id: int,
    ) -> None:
        if operation_type == KitOperationType.ASSEMBLY:
            consumption = {
                product_id: quantity_per_kit * quantity
                for product_id, quantity_per_kit in kit_components.items()
            }
        else:
            consumption = {kit_product_id: quantity}

        for product_id, required_quantity in consumption.items():
            row = await self.repository.get_loose_inventory_for_update(conn, product_id, location_id)
            loose_quantity = row["quantity"] if row else Decimal("0")
            if loose_quantity >= required_quantity:
                continue

            container_quantity = await self.repository.get_container_inventory_quantity(
                conn, product_id, location_id
            )
            if container_quantity and container_quantity > 0:
                raise KitOperationConflictError(
                    "Kit operation supports only loose stock in MVP"
                )
            raise KitOperationConflictError(
                f"Insufficient loose stock for product '{product_id}'"
            )

    async def _create_items_and_movements(
        self,
        *,
        conn,
        operation_id: int,
        operation_type: KitOperationType,
        kit_product_id: str,
        kit_components: Dict[str, Decimal],
        quantity: Decimal,
        location_id: int,
        author: str,
    ) -> List[dict]:
        specs = self._movement_specs(operation_type, kit_product_id, kit_components, quantity, location_id)
        items = []
        for spec in specs:
            item = await self.repository.create_item(
                conn,
                operation_id=operation_id,
                role=spec["role"],
                product_id=spec["product_id"],
                quantity_per_kit=spec["quantity_per_kit"],
                total_quantity=spec["total_quantity"],
            )
            metadata = {
                "role": spec["role"],
                "operation_type": operation_type.value,
                "kit_product_id": kit_product_id,
            }
            movement = await self.repository.create_movement(
                conn,
                movement_type=spec["movement_type"],
                product_id=spec["product_id"],
                from_location_id=spec["from_location_id"],
                to_location_id=spec["to_location_id"],
                quantity=spec["total_quantity"],
                user_name=author,
                reason=spec["reason"],
                metadata=json.dumps(metadata, ensure_ascii=False),
                source_id=operation_id,
                source_item_id=item["item_id"],
            )
            updated_item = await self.repository.set_item_movement(
                conn, item["item_id"], movement["movement_id"], movement["created_at"]
            )
            items.append(dict(updated_item))
        return items

    def _movement_specs(
        self,
        operation_type: KitOperationType,
        kit_product_id: str,
        kit_components: Dict[str, Decimal],
        quantity: Decimal,
        location_id: int,
    ) -> List[dict]:
        if operation_type == KitOperationType.ASSEMBLY:
            specs = [
                {
                    "role": KitOperationItemRole.COMPONENT_CONSUMPTION.value,
                    "product_id": product_id,
                    "quantity_per_kit": quantity_per_kit,
                    "total_quantity": quantity_per_kit * quantity,
                    "movement_type": MovementType.KIT_ASSEMBLY.value,
                    "from_location_id": location_id,
                    "to_location_id": None,
                    "reason": "Kit assembly component consumption",
                }
                for product_id, quantity_per_kit in kit_components.items()
            ]
            specs.append(
                {
                    "role": KitOperationItemRole.KIT_RESULT.value,
                    "product_id": kit_product_id,
                    "quantity_per_kit": Decimal("1"),
                    "total_quantity": quantity,
                    "movement_type": MovementType.KIT_ASSEMBLY.value,
                    "from_location_id": None,
                    "to_location_id": location_id,
                    "reason": "Kit assembly result",
                }
            )
            return specs

        specs = [
            {
                "role": KitOperationItemRole.KIT_CONSUMPTION.value,
                "product_id": kit_product_id,
                "quantity_per_kit": Decimal("1"),
                "total_quantity": quantity,
                "movement_type": MovementType.KIT_DISASSEMBLY.value,
                "from_location_id": location_id,
                "to_location_id": None,
                "reason": "Kit disassembly kit consumption",
            }
        ]
        specs.extend(
            {
                "role": KitOperationItemRole.COMPONENT_RESULT.value,
                "product_id": product_id,
                "quantity_per_kit": quantity_per_kit,
                "total_quantity": quantity_per_kit * quantity,
                "movement_type": MovementType.KIT_DISASSEMBLY.value,
                "from_location_id": None,
                "to_location_id": location_id,
                "reason": "Kit disassembly component result",
            }
            for product_id, quantity_per_kit in kit_components.items()
        )
        return specs

    def _build_response(self, operation, location_code: str, items) -> KitOperationResponse:
        payload = dict(operation)
        payload["location_code"] = location_code
        payload["items"] = [
            KitOperationItemResponse.model_validate(dict(item)) for item in items
        ]
        return KitOperationResponse.model_validate(payload)

    def _operation_location_response(
        self, row, *, location_name: Optional[str] = None
    ) -> KitOperationLocationResponse:
        payload = dict(row)
        if isinstance(payload.get("metadata"), str):
            payload["metadata"] = json.loads(payload["metadata"]) if payload["metadata"] else {}
        if location_name is not None:
            payload["location_name"] = location_name
        return KitOperationLocationResponse.model_validate(payload)
