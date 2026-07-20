"""Transactional product re-sorting service."""
import json

from app.core.enums import ReSortingOperationItemRole
from app.core.exceptions import (
    LocationNotFoundError,
    ProductNotFoundError,
    ReSortingOperationConflictError,
    ReSortingOperationNotFoundError,
)
from app.core.schemas.re_sorting_operations import (
    ReSortingOperationLocationListResponse,
    ReSortingOperationLocationResponse,
    ReSortingOperationResponse,
    ReSortingOperationSummaryResponse,
)


class ReSortingOperationService:
    def __init__(self, repository):
        self.repository = repository

    async def create_operation(self, data):
        async with self.repository.pool.acquire() as c:
            async with c.transaction():
                location = await self.repository.get_location_by_code(c, data.location_code)
                if not location:
                    raise LocationNotFoundError(f"Локация '{data.location_code}' не найдена")
                if not location["is_active"]:
                    raise ReSortingOperationConflictError("Локация неактивна")
                permission = await self.repository.get_active_operation_location_for_share(
                    c, location["location_code"], location["location_id"]
                )
                if not permission:
                    raise ReSortingOperationConflictError(
                        "Локация не разрешена для операций пересортицы"
                    )
                products = {
                    r["id"]: r
                    for r in await self.repository.get_products(
                        c, [data.from_product_id, data.to_product_id]
                    )
                }
                for label, pid in (
                    ("Исходный", data.from_product_id),
                    ("Целевой", data.to_product_id),
                ):
                    if pid not in products:
                        raise ProductNotFoundError(f"{label} товар '{pid}' не найден")
                    if not products[pid]["is_active"]:
                        raise ReSortingOperationConflictError(f"{label} товар неактивен")
                await self.repository.lock_operation_scope(
                    c, location["location_id"], data.from_product_id, data.to_product_id
                )
                stock = await self.repository.get_loose_inventory_for_update(
                    c, data.from_product_id, location["location_id"]
                )
                if not stock or stock["quantity"] < data.quantity:
                    non_loose = await self.repository.get_non_loose_inventory_quantity(
                        c, data.from_product_id, location["location_id"]
                    )
                    if non_loose and non_loose > 0:
                        raise ReSortingOperationConflictError(
                            "Исходный остаток существует только в партии/контейнере либо loose-остатка недостаточно"
                        )
                    raise ReSortingOperationConflictError(
                        "Исходный loose-остаток отсутствует или его недостаточно"
                    )
                op = await self.repository.create_operation(
                    c,
                    operation_location_id=permission["operation_location_id"],
                    from_product_id=data.from_product_id,
                    to_product_id=data.to_product_id,
                    quantity=data.quantity,
                    location_id=location["location_id"],
                    location_code=location["location_code"],
                    reason=data.reason,
                    author=data.author,
                )
                items = []
                for role, pid, from_l, to_l in (
                    (
                        ReSortingOperationItemRole.SOURCE_OUTGOING.value,
                        data.from_product_id,
                        location["location_id"],
                        None,
                    ),
                    (
                        ReSortingOperationItemRole.TARGET_INCOMING.value,
                        data.to_product_id,
                        None,
                        location["location_id"],
                    ),
                ):
                    item = await self.repository.create_item(
                        c, op["operation_id"], role, pid, data.quantity
                    )
                    metadata = {
                        "role": role,
                        "operation_code": "re_sorting_operations",
                        "from_product_id": data.from_product_id,
                        "to_product_id": data.to_product_id,
                        "location_code": location["location_code"],
                    }
                    movement = await self.repository.create_movement(
                        c,
                        pid,
                        from_l,
                        to_l,
                        data.quantity,
                        data.author,
                        data.reason,
                        metadata,
                        op["operation_id"],
                        item["item_id"],
                    )
                    items.append(
                        await self.repository.set_item_movement(
                            c, item["item_id"], movement["movement_id"], movement["created_at"]
                        )
                    )
                op = await self.repository.complete_operation(c, op["operation_id"])
        return self._response(op, items)

    async def list_operation_locations(self, is_active=None, limit=50, offset=0):
        rows = await self.repository.list_operation_locations(is_active, limit, offset)
        total = await self.repository.count_operation_locations(is_active)
        return ReSortingOperationLocationListResponse(
            items=[self._operation_location_response(r) for r in rows],
            limit=limit,
            offset=offset,
            total=total,
        )

    async def create_operation_location(self, data):
        async with self.repository.pool.acquire() as c:
            async with c.transaction():
                location = await self.repository.get_location_by_code(c, data.location_code)
                if not location:
                    raise LocationNotFoundError(f"Локация '{data.location_code}' не найдена")
                if not location["is_active"]:
                    raise ReSortingOperationConflictError("Локация неактивна")
                row = await self.repository.create_or_reactivate_operation_location(
                    c,
                    location["location_id"],
                    location["location_code"],
                    data.author,
                    data.metadata,
                )
        return self._operation_location_response(row, location_name=location["name"])

    async def deactivate_operation_location(self, oid, data):
        async with self.repository.pool.acquire() as c:
            async with c.transaction():
                row = await self.repository.get_operation_location(c, oid)
                if not row:
                    raise ReSortingOperationNotFoundError(
                        f"Разрешённая локация пересортицы с ID {oid} не найдена"
                    )
                if row["is_active"]:
                    row = await self.repository.deactivate_operation_location(c, oid, data.author)
        return self._operation_location_response(row)

    async def get_operation(self, oid):
        op = await self.repository.get_operation(oid)
        if not op:
            raise ReSortingOperationNotFoundError(f"Операция пересортицы с ID {oid} не найдена")
        return self._response(op, await self.repository.get_items(oid))

    async def list_operations(self, **filters):
        if filters.get("status") is not None:
            filters["status"] = filters["status"].value
        return [
            ReSortingOperationSummaryResponse.model_validate(self._decode_metadata(dict(row)))
            for row in await self.repository.list_operations(**filters)
        ]

    @staticmethod
    def _decode_metadata(payload):
        if isinstance(payload.get("metadata"), str):
            payload["metadata"] = json.loads(payload["metadata"]) if payload["metadata"] else {}
        return payload

    def _operation_location_response(self, row, *, location_name=None):
        payload = self._decode_metadata(dict(row))
        if location_name is not None:
            payload["location_name"] = location_name
        return ReSortingOperationLocationResponse.model_validate(payload)

    def _response(self, op, items):
        payload = self._decode_metadata(dict(op))
        payload["items"] = [dict(i) for i in items]
        return ReSortingOperationResponse.model_validate(payload)
