"""Repository for re-sorting operations."""
import json
from datetime import datetime
from typing import Optional
from asyncpg import Pool
from app.infrastructure.database.queries import re_sorting_operations as q


class ReSortingOperationRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_location_by_code(self, c, code):
        return await c.fetchrow(q.GET_LOCATION_BY_CODE, code)

    async def get_active_operation_location_for_share(self, c, code, lid):
        return await c.fetchrow(q.GET_ACTIVE_OPERATION_LOCATION_FOR_SHARE, code, lid)

    async def list_operation_locations(self, is_active=None, limit=50, offset=0):
        async with self.pool.acquire() as c:
            return await c.fetch(q.LIST_OPERATION_LOCATIONS, is_active, limit, offset)

    async def count_operation_locations(self, is_active=None):
        async with self.pool.acquire() as c:
            return await c.fetchval(q.COUNT_OPERATION_LOCATIONS, is_active)

    async def create_or_reactivate_operation_location(
        self, c, location_id, location_code, author, metadata
    ):
        return await c.fetchrow(
            q.CREATE_OR_REACTIVATE_OPERATION_LOCATION,
            location_id,
            location_code,
            author,
            json.dumps(metadata or {}, ensure_ascii=False),
        )

    async def get_operation_location(self, c, oid):
        return await c.fetchrow(q.GET_OPERATION_LOCATION, oid)

    async def deactivate_operation_location(self, c, oid, author):
        return await c.fetchrow(q.DEACTIVATE_OPERATION_LOCATION, oid, author)

    async def get_products(self, c, ids):
        return await c.fetch(q.GET_PRODUCTS, ids)

    async def lock_operation_scope(self, c, location_id, from_id, to_id):
        first, second = sorted((from_id, to_id))
        await c.execute(
            q.LOCK_OPERATION_SCOPE, f"re_sorting_operations:{location_id}:{first}:{second}"
        )

    async def get_loose_inventory_for_update(self, c, pid, lid):
        return await c.fetchrow(q.GET_LOOSE_INVENTORY_FOR_UPDATE, pid, lid)

    async def get_non_loose_inventory_quantity(self, c, pid, lid):
        return await c.fetchval(q.GET_NON_LOOSE_INVENTORY_QUANTITY, pid, lid)

    async def create_operation(self, c, **v):
        return await c.fetchrow(
            q.CREATE_OPERATION,
            v["operation_location_id"],
            v["from_product_id"],
            v["to_product_id"],
            v["quantity"],
            v["location_id"],
            v["location_code"],
            v["reason"],
            v["author"],
        )

    async def create_item(self, c, operation_id, role, product_id, quantity):
        return await c.fetchrow(q.CREATE_ITEM, operation_id, role, product_id, quantity)

    async def create_movement(
        self,
        c,
        product_id,
        from_location_id,
        to_location_id,
        quantity,
        author,
        reason,
        metadata,
        operation_id,
        item_id,
    ):
        return await c.fetchrow(
            q.CREATE_MOVEMENT,
            product_id,
            from_location_id,
            to_location_id,
            quantity,
            author,
            reason,
            json.dumps(metadata, ensure_ascii=False),
            operation_id,
            item_id,
        )

    async def set_item_movement(self, c, item_id, movement_id, created_at):
        return await c.fetchrow(q.SET_ITEM_MOVEMENT, item_id, movement_id, created_at)

    async def complete_operation(self, c, oid):
        return await c.fetchrow(q.COMPLETE_OPERATION, oid)

    async def get_operation(self, oid):
        async with self.pool.acquire() as c:
            return await c.fetchrow(q.GET_OPERATION, oid)

    async def get_items(self, oid):
        async with self.pool.acquire() as c:
            return await c.fetch(q.GET_ITEMS, oid)

    async def list_operations(
        self,
        from_product_id=None,
        to_product_id=None,
        status=None,
        location_code=None,
        date_from=None,
        date_to=None,
        limit=100,
        offset=0,
    ):
        async with self.pool.acquire() as c:
            return await c.fetch(
                q.LIST_OPERATIONS,
                from_product_id,
                to_product_id,
                status,
                location_code,
                date_from,
                date_to,
                limit,
                offset,
            )
