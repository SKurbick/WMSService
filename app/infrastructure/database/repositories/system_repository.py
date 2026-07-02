"""Репозиторий для системных операций"""

from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record
from app.infrastructure.database.queries import system as queries
from app.core.exceptions import NegativeCalculatedInventoryError


def _format_negative_inventory_rows(rows: List[Record]) -> str:
    details = []
    for row in rows:
        details.append(
            "product_id={product_id}, location_id={location_id}, "
            "batch_number={batch_number}, container_code={container_code}, "
            "calculated_quantity={calculated_quantity}".format(**dict(row))
        )
    return "; ".join(details)


class SystemRepository:
    """Репозиторий для системных операций над БД"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def validate_integrity(self) -> List[Record]:
        """Проверить целостность данных между inventory и movements"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.VALIDATE_INTEGRITY)
            return results

    async def get_audit_summary(self) -> Record:
        """Получить read-only агрегированные проверки известных рисков"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.GET_AUDIT_SUMMARY)
            return result

    async def recalculate_inventory(
        self,
        product_id: Optional[str] = None,
        from_date: Optional[date] = None,
    ) -> Record:
        """
        Пересчитать остатки из movements

        Выполняет транзакцию:
        1. Проверяет, что пересчет available из movements не дает отрицательных остатков
        2. Удаляет только available записи inventory
        3. Пересчитывает available остатки из movements
        4. Возвращает статистику available остатков
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                negative_rows = await conn.fetch(
                    queries.CHECK_NEGATIVE_CALCULATED_INVENTORY, product_id
                )
                if negative_rows:
                    details = _format_negative_inventory_rows(negative_rows)
                    raise NegativeCalculatedInventoryError(
                        "Пересчет inventory из movements дал отрицательный available-остаток: "
                        f"{details}"
                    )

                await conn.execute(queries.DELETE_AVAILABLE_INVENTORY, product_id)
                await conn.execute(queries.RECALCULATE_INVENTORY, product_id)

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
