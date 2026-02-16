"""Репозиторий для работы с локациями"""

from typing import List, Optional
from asyncpg import Pool, Record
from app.infrastructure.database.queries import locations as queries


class LocationRepository:
    """Репозиторий для работы с таблицей wms.locations"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_zones_hierarchy(self, max_level: int = 5) -> List[Record]:
        """Получить иерархию зон с ограничением по уровню"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_ZONES_HIERARCHY, max_level)
            return results

    async def get_zones(self) -> List[Record]:
        """Получить список всех активных зон (level = 1)"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_ZONES)
            return results

    async def create(self, data: dict) -> Record:
        """Создать локацию"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.CREATE_LOCATION,
                data.get("parent_location_id"),
                data["name"],
                data["zone_type"],
                data["level"],
                data.get("max_weight", 0),
                data.get("max_volume", 0),
                data.get("is_active", True),
                data.get("is_pickable", False),
                data.get("metadata"),
            )
            return result

    async def get_by_id(self, location_id: int) -> Optional[Record]:
        """Получить локацию по ID"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.GET_LOCATION_BY_ID, location_id)
            return result

    async def get_by_code(self, location_code: str) -> Optional[Record]:
        """Получить локацию по коду"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.GET_LOCATION_BY_CODE, location_code)
            return result

    async def get_children(self, location_id: int, recursive: bool = True) -> List[Record]:
        """
        Получить дочерние локации
        
        Args:
            location_id: ID родительской локации
            recursive: Если True - все потомки, если False - только прямые дети
        """
        async with self.pool.acquire() as conn:
            query = queries.GET_CHILDREN_RECURSIVE if recursive else queries.GET_CHILDREN_DIRECT
            results = await conn.fetch(query, location_id)
            return results

    async def update(self, location_id: int, data: dict) -> Record:
        """Обновить локацию"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.UPDATE_LOCATION,
                location_id,
                data.get("name"),
                data.get("zone_type"),
                data.get("max_weight"),
                data.get("max_volume"),
                data.get("is_active"),
                data.get("is_pickable"),
                data.get("metadata"),
            )
            return result

    async def deactivate(self, location_id: int) -> Record:
        """Деактивировать локацию"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.DEACTIVATE_LOCATION, location_id)
            return result

    async def find_available(
        self, product_id: str, quantity: int, zone_type: str = "storage"
    ) -> Optional[Record]:
        """
        Найти свободную ячейку для размещения товара
        
        Использует PostgreSQL функцию wms.find_available_location()
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.FIND_AVAILABLE_LOCATION, product_id, quantity, zone_type
            )
            return result
