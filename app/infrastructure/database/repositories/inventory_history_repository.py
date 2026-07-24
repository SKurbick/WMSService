"""Репозиторий read-only истории остатков."""

from datetime import date

from asyncpg import Pool, Record

from app.infrastructure.database.queries import inventory_history as queries


class InventoryHistoryRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_daily_balance_page(
        self,
        date_from: date,
        date_to: date,
        product_id: str | None,
        location_id: int | None,
        include_subtree: bool,
        limit: int,
        offset: int,
    ) -> tuple[bool, int, list[Record]]:
        """Вернуть location check, count и страницу из одного DB snapshot."""
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                location_exists = location_id is None or bool(
                    await connection.fetchval(queries.LOCATION_EXISTS, location_id)
                )
                if not location_exists:
                    return False, 0, []
                total_products = await connection.fetchval(
                    queries.COUNT_DAILY_BALANCE_PRODUCTS,
                    date_from,
                    date_to,
                    product_id,
                    location_id,
                    include_subtree,
                )
                rows = await connection.fetch(
                    queries.GET_DAILY_BALANCES,
                    date_from,
                    date_to,
                    product_id,
                    location_id,
                    include_subtree,
                    limit,
                    offset,
                )
                return True, total_products, rows
