"""Репозиторий для системных операций"""

from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record
from app.infrastructure.database.queries import system as queries


class SystemRepository:
    """Репозиторий для системных операций над БД"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def validate_integrity(self) -> List[Record]:
        """Проверить целостность данных между inventory и movements"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.VALIDATE_INTEGRITY)
            return results

    async def recalculate_inventory(
        self,
        product_id: Optional[str] = None,
        from_date: Optional[date] = None,
    ) -> Record:
        """
        Пересчитать остатки из movements

        Выполняет транзакцию:
        1. Удаляет записи из inventory
        2. Пересчитывает из movements
        3. Возвращает статистику
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Шаг 1: Очистка
                await conn.execute(queries.DELETE_INVENTORY, product_id)

                # Шаг 2: Пересчёт
                await conn.execute(
                    queries.RECALCULATE_INVENTORY, product_id, from_date
                )

                # Шаг 3: Статистика
                result = await conn.fetchrow(queries.GET_INVENTORY_STATS, product_id)
                return result

    async def create_snapshot(self, snapshot_date: Optional[date] = None) -> Record:
        """
        Создать снимок остатков

        Сохраняет текущее состояние inventory в таблицу snapshots.
        """
        async with self.pool.acquire() as conn:
            # Создание снимка
            await conn.execute(queries.CREATE_SNAPSHOT, snapshot_date)

            # Статистика
            result = await conn.fetchrow(queries.GET_SNAPSHOT_STATS, snapshot_date)
            return result

    async def refresh_materialized_views(self) -> Record:
        """
        Обновить материализованные представления

        Обновляет mv_product_stock CONCURRENTLY (без блокировки чтения).
        """
        async with self.pool.acquire() as conn:
            # Обновление представления
            await conn.execute(queries.REFRESH_MATERIALIZED_VIEW)

            # Статистика
            result = await conn.fetchrow(queries.GET_MATERIALIZED_VIEW_STATS)
            return result
