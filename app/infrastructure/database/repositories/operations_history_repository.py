"""Репозиторий единого read-only списка операций."""

from datetime import date

from asyncpg import Pool, Record

from app.infrastructure.database.queries import operations_history as queries


class OperationsHistoryRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_page(
        self,
        date_from: date,
        date_to: date,
        source_type: str | None,
        operation_type: str | None,
        product_id: str | None,
        location_id: int | None,
        author: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[bool, int, list[Record]]:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                location_exists = location_id is None or bool(
                    await connection.fetchval(queries.LOCATION_EXISTS, location_id)
                )
                if not location_exists:
                    return False, 0, []
                arguments = (
                    date_from,
                    date_to,
                    source_type,
                    operation_type,
                    product_id,
                    location_id,
                    author,
                    status,
                )
                total = await connection.fetchval(queries.COUNT_OPERATIONS_HISTORY, *arguments)
                rows = await connection.fetch(
                    queries.GET_OPERATIONS_HISTORY, *arguments, limit, offset
                )
                return True, total, rows

    async def get_kit_detail(self, operation_id: int):
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                header = await connection.fetchrow(queries.GET_KIT_OPERATION_HEADER, operation_id)
                if not header:
                    return None, []
                return header, await connection.fetch(
                    queries.GET_KIT_ITEMS_WITH_MOVEMENTS, operation_id
                )

    async def get_re_sorting_detail(self, operation_id: int):
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                header = await connection.fetchrow(queries.GET_RE_SORTING_HEADER, operation_id)
                if not header:
                    return None, []
                return header, await connection.fetch(
                    queries.GET_RE_SORTING_ITEMS_WITH_MOVEMENTS, operation_id
                )

    async def get_fbs_detail(self, shipment_id: int):
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                header = await connection.fetchrow(queries.GET_FBS_SHIPMENT_HEADER, shipment_id)
                if not header:
                    return None, [], []
                items = await connection.fetch(queries.GET_FBS_SHIPMENT_ITEMS, shipment_id)
                movement_ids = sorted(
                    {row["movement_id"] for row in items if row["movement_id"] is not None}
                )
                movements = (
                    await connection.fetch(queries.GET_MOVEMENTS_BY_IDS, movement_ids)
                    if movement_ids
                    else []
                )
                return header, items, movements

    async def get_movement_detail(self, movement_id: int, created_at):
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await connection.fetch(
                    queries.GET_MOVEMENT_BY_IDENTITY, movement_id, created_at
                )
