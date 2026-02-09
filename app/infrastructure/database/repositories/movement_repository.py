"""Репозиторий для работы с движениями товаров"""

from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record
from app.infrastructure.database.queries import movements as queries


class MovementRepository:
    """Репозиторий для работы с таблицей wms.movements"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def create(self, data: dict) -> Record:
        """
        Создать движение товара

        Триггер update_inventory_from_movement() автоматически обновит inventory.
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.CREATE_MOVEMENT,
                data["movement_type"],
                data["product_id"],
                data.get("from_location_code"),
                data.get("to_location_code"),
                data["quantity"],
                data.get("batch_number"),
                data.get("container_code"),
                data.get("user_name"),
                data.get("reason"),
            )
            return result

    async def get_movements(
        self,
        product_id: Optional[str] = None,
        container_code: Optional[str] = None,
        movement_type: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Record]:
        """Получить движения с фильтрами"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                queries.GET_MOVEMENTS,
                product_id,
                container_code,
                movement_type,
                from_date,
                to_date,
                limit,
                offset,
            )
            return results

    async def get_by_product(self, product_id: str, limit: int = 100) -> List[Record]:
        """Получить историю движений по товару"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                queries.GET_MOVEMENTS_BY_PRODUCT, product_id, limit
            )
            return results
