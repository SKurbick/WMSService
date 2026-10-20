"""Репозиторий для системных операций"""

from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record
from app.infrastructure.database.queries import system as queries
from app.core.exceptions import (
    ContainerInventoryIntegrityError,
    NegativeCalculatedInventoryError,
)
from app.core.kiz_errors import KizConflictError
from app.infrastructure.database.queries.kiz import INTEGRITY as KIZ_INTEGRITY


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
        2. Проверяет target projection против active КИЗ
        3. UPSERT available projection, удаляет obsolete rows, проверяет KIZ integrity
        4. Возвращает статистику available остатков
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                # Freeze ledger writers before projection. EXCLUSIVE inventory lock also
                # blocks SELECT FOR UPDATE in assignment/terminal until maintenance commits.
                # A conflicting legacy lock order may deadlock: rollback maps to HTTP 409.
                await conn.execute("LOCK TABLE wms.movements IN SHARE MODE")
                await conn.execute("LOCK TABLE wms.inventory IN EXCLUSIVE MODE")
                container_conflicts = await conn.fetch(
                    queries.CHECK_CONTAINER_PROJECTION, product_id
                )
                if container_conflicts:
                    raise ContainerInventoryIntegrityError(
                        "Container projection invariant нарушен до пересчета inventory",
                        diagnostics=[dict(row) for row in container_conflicts],
                    )

                negative_rows = await conn.fetch(
                    queries.CHECK_NEGATIVE_CALCULATED_INVENTORY, product_id
                )
                if negative_rows:
                    details = _format_negative_inventory_rows(negative_rows)
                    raise NegativeCalculatedInventoryError(
                        "Пересчет inventory из movements дал отрицательный available-остаток: "
                        f"{details}"
                    )

                kiz_conflicts = await conn.fetch(queries.CHECK_CALCULATED_KIZ, product_id)
                if kiz_conflicts:
                    raise KizConflictError(
                        "Пересчет уменьшит физический остаток ниже active КИЗ",
                        diagnostics=[dict(row) for row in kiz_conflicts],
                    )
                await conn.execute(queries.RECALCULATE_INVENTORY, product_id)
                await conn.execute(queries.DELETE_AVAILABLE_INVENTORY, product_id)
                final_conflicts = await conn.fetch(KIZ_INTEGRITY, product_id)
                if final_conflicts:
                    raise KizConflictError(
                        "Нарушение KIZ integrity после пересчета",
                        diagnostics=[dict(row) for row in final_conflicts],
                    )

                final_container_conflicts = await conn.fetch(
                    queries.CHECK_CONTAINER_PROJECTION, product_id
                )
                if final_container_conflicts:
                    raise ContainerInventoryIntegrityError(
                        "Container projection invariant нарушен после пересчета inventory",
                        diagnostics=[dict(row) for row in final_container_conflicts],
                    )

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
