"""Репозиторий для операций комплектации и разукомплектации."""

from datetime import datetime
from typing import List, Optional

from asyncpg import Pool, Record

from app.infrastructure.database.queries import kit_operations as queries


class KitOperationRepository:
    """Доступ к таблицам wms.kit_operations и связанным справочникам."""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_location_by_code(self, conn, location_code: str) -> Optional[Record]:
        return await conn.fetchrow(queries.GET_LOCATION_BY_CODE, location_code)

    async def get_active_kit_operation_location(
        self, conn, location_code: str, location_id: int
    ) -> Optional[Record]:
        return await conn.fetchrow(
            queries.GET_ACTIVE_KIT_OPERATION_LOCATION, location_code, location_id
        )

    async def list_kit_operation_locations(
        self, *, is_active: Optional[bool] = None, limit: int = 50, offset: int = 0
    ) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.LIST_KIT_OPERATION_LOCATIONS, is_active, limit, offset)

    async def count_kit_operation_locations(self, *, is_active: Optional[bool] = None) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(queries.COUNT_KIT_OPERATION_LOCATIONS, is_active)

    async def create_or_reactivate_kit_operation_location(
        self, conn, *, location_id: int, location_code: str, author: str, metadata: str
    ) -> Record:
        return await conn.fetchrow(
            queries.CREATE_OR_REACTIVATE_KIT_OPERATION_LOCATION,
            location_id,
            location_code,
            author,
            metadata,
        )

    async def get_kit_operation_location(self, conn, operation_location_id: int) -> Optional[Record]:
        return await conn.fetchrow(queries.GET_KIT_OPERATION_LOCATION, operation_location_id)

    async def deactivate_kit_operation_location(
        self, conn, operation_location_id: int, author: str
    ) -> Optional[Record]:
        return await conn.fetchrow(
            queries.DEACTIVATE_KIT_OPERATION_LOCATION, operation_location_id, author
        )

    async def get_kit_product(self, conn, product_id: str) -> Optional[Record]:
        return await conn.fetchrow(queries.GET_KIT_PRODUCT, product_id)

    async def get_products_by_ids(self, conn, product_ids: List[str]) -> List[Record]:
        return await conn.fetch(queries.GET_PRODUCTS_BY_IDS, product_ids)

    async def lock_operation_scope(self, conn, kit_product_id: str, location_id: int) -> None:
        await conn.execute(queries.LOCK_KIT_OPERATION_SCOPE, f"{kit_product_id}:{location_id}")

    async def get_loose_inventory_for_update(
        self, conn, product_id: str, location_id: int
    ) -> Optional[Record]:
        return await conn.fetchrow(queries.GET_LOOSE_INVENTORY_FOR_UPDATE, product_id, location_id)

    async def get_container_inventory_quantity(self, conn, product_id: str, location_id: int):
        return await conn.fetchval(queries.GET_CONTAINER_INVENTORY_QUANTITY, product_id, location_id)

    async def create_operation(
        self,
        conn,
        *,
        operation_type: str,
        kit_product_id: str,
        quantity,
        operation_location_id: int,
        location_id: int,
        location_code: str,
        author: str,
    ) -> Record:
        return await conn.fetchrow(
            queries.CREATE_KIT_OPERATION,
            operation_type,
            kit_product_id,
            quantity,
            operation_location_id,
            location_id,
            location_code,
            author,
        )

    async def create_item(
        self,
        conn,
        *,
        operation_id: int,
        role: str,
        product_id: str,
        quantity_per_kit,
        total_quantity,
    ) -> Record:
        return await conn.fetchrow(
            queries.CREATE_KIT_OPERATION_ITEM,
            operation_id,
            role,
            product_id,
            quantity_per_kit,
            total_quantity,
        )

    async def create_movement(
        self,
        conn,
        *,
        movement_type: str,
        product_id: str,
        from_location_id: Optional[int],
        to_location_id: Optional[int],
        quantity,
        user_name: str,
        reason: str,
        metadata: str,
        source_id: int,
        source_item_id: int,
    ) -> Record:
        return await conn.fetchrow(
            queries.CREATE_KIT_MOVEMENT,
            movement_type,
            product_id,
            from_location_id,
            to_location_id,
            quantity,
            user_name,
            reason,
            metadata,
            source_id,
            source_item_id,
        )

    async def set_item_movement(
        self, conn, item_id: int, movement_id: int, movement_created_at: datetime
    ) -> Record:
        return await conn.fetchrow(queries.SET_ITEM_MOVEMENT, item_id, movement_id, movement_created_at)

    async def complete_operation(self, conn, operation_id: int) -> Record:
        return await conn.fetchrow(queries.COMPLETE_KIT_OPERATION, operation_id)

    async def get_operation(self, operation_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.GET_KIT_OPERATION, operation_id)

    async def get_items(self, operation_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_KIT_OPERATION_ITEMS, operation_id)

    async def list_operations(
        self,
        *,
        operation_type: Optional[str] = None,
        kit_product_id: Optional[str] = None,
        status: Optional[str] = None,
        location_code: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                queries.LIST_KIT_OPERATIONS,
                operation_type,
                kit_product_id,
                status,
                location_code,
                date_from,
                date_to,
                limit,
                offset,
            )
