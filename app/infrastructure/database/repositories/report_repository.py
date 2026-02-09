"""Репозиторий для отчётов"""

from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record
from app.infrastructure.database.queries import reports as queries


class ReportRepository:
    """Репозиторий для получения данных отчётов"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_zones_report(self) -> List[Record]:
        """Получить отчёт по зонам склада"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_ZONES_REPORT)
            return results

    async def get_top_products(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 10,
    ) -> List[Record]:
        """Получить топ товаров по движениям"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                queries.GET_TOP_PRODUCTS, from_date, to_date, limit
            )
            return results

    async def get_abc_analysis(self, from_date: date, to_date: date) -> List[Record]:
        """Получить ABC-анализ товаров"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_ABC_ANALYSIS, from_date, to_date)
            return results

    async def get_turnover_report(self, from_date: date, to_date: date) -> List[Record]:
        """Получить отчёт оборачиваемости"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_TURNOVER_REPORT, from_date, to_date)
            return results

    async def get_batches_report(self, product_id: Optional[str] = None) -> List[Record]:
        """Получить отчёт по партиям (FIFO/FEFO)"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_BATCHES_REPORT, product_id)
            return results
