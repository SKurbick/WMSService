"""Сервис для отчётов (бизнес-логика)"""

from typing import List, Optional
from datetime import date
from app.core.schemas.report import (
    ZoneReportItem,
    TopProductItem,
    ABCAnalysisItem,
    TurnoverItem,
    BatchReportItem,
)
from app.infrastructure.database.repositories.report_repository import ReportRepository


class ReportService:
    """Сервис для получения отчётов"""

    def __init__(self, report_repository: ReportRepository):
        self.report_repo = report_repository

    async def get_zones_report(self) -> List[ZoneReportItem]:
        """
        Получить отчёт по зонам склада

        Показывает загруженность каждой зоны: количество занятых локаций,
        товаров, единиц и контейнеров.
        """
        results = await self.report_repo.get_zones_report()
        return [ZoneReportItem.model_validate(dict(r)) for r in results]

    async def get_top_products(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 10,
    ) -> List[TopProductItem]:
        """
        Получить топ товаров по активности движений

        Показывает самые активные товары за указанный период.
        """
        results = await self.report_repo.get_top_products(from_date, to_date, limit)
        return [TopProductItem.model_validate(dict(r)) for r in results]

    async def get_abc_analysis(
        self, from_date: date, to_date: date
    ) -> List[ABCAnalysisItem]:
        """
        Получить ABC-анализ товаров

        Классифицирует товары по частоте движений:
        - A: 80% движений (самые активные)
        - B: 15% движений
        - C: 5% движений (редко двигаются)
        """
        results = await self.report_repo.get_abc_analysis(from_date, to_date)
        return [ABCAnalysisItem.model_validate(dict(r)) for r in results]

    async def get_turnover_report(
        self, from_date: date, to_date: date
    ) -> List[TurnoverItem]:
        """
        Получить отчёт оборачиваемости товаров

        Показывает скорость оборота товаров:
        - turnover_ratio: коэффициент оборачиваемости (чем выше, тем лучше)
        - days_of_inventory: на сколько дней хватит запаса при текущих отгрузках
        """
        results = await self.report_repo.get_turnover_report(from_date, to_date)
        return [TurnoverItem.model_validate(dict(r)) for r in results]

    async def get_batches_report(
        self, product_id: Optional[str] = None
    ) -> List[BatchReportItem]:
        """
        Получить отчёт по партиям (FIFO/FEFO)

        Показывает остатки по партиям с датой первой приёмки.
        Сортировка по дате - старые партии должны отгружаться первыми (FIFO).
        """
        results = await self.report_repo.get_batches_report(product_id)
        return [BatchReportItem.model_validate(dict(r)) for r in results]
