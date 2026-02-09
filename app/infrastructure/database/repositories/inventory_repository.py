"""Репозиторий для работы с инвентарём (остатками)"""

from typing import List, Optional
from asyncpg import Pool, Record
from app.infrastructure.database.queries import inventory as queries


class InventoryRepository:
    """Репозиторий для работы с таблицей wms.inventory"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_by_product(self, product_id: str) -> List[Record]:
        """Получить остатки товара по всем локациям"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_INVENTORY_BY_PRODUCT, product_id)
            return results

    async def get_by_location(self, location_id: int) -> List[Record]:
        """Получить все остатки в локации"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_INVENTORY_BY_LOCATION, location_id)
            return results

    async def get_summary(self, category: Optional[str] = None) -> List[Record]:
        """Получить агрегированные остатки по всем товарам"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_INVENTORY_SUMMARY, category)
            return results

    async def get_in_container(self, qr_code: str) -> List[Record]:
        """Получить остатки в контейнере"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_INVENTORY_IN_CONTAINER, qr_code)
            return results

    async def get_loose(self, location_id: int) -> List[Record]:
        """Получить россыпь в локации (без контейнера)"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_LOOSE_INVENTORY, location_id)
            return results

    async def search(self, query: str) -> List[Record]:
        """Поиск товара по product_id, названию, batch_number или container_code"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.SEARCH_INVENTORY, query)
            return results
