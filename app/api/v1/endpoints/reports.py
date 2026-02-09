"""API endpoints для отчётов"""

from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from datetime import date

from app.core.schemas.report import (
    ZoneReportItem,
    TopProductItem,
    ABCAnalysisItem,
    TurnoverItem,
    BatchReportItem,
)
from app.core.services.report_service import ReportService
from app.api.v1.dependencies import get_report_service

router = APIRouter(prefix="/reports", tags=["Отчёты"])


@router.get("/zones", response_model=List[ZoneReportItem])
async def get_zones_report(
    service: ReportService = Depends(get_report_service),
):
    """
    Отчёт по зонам склада

    Показывает загруженность каждой зоны: количество занятых локаций,
    уникальных товаров, общее количество единиц и контейнеров.

    **Возвращает:**
    - Список зон с метриками загруженности
    """
    return await service.get_zones_report()


@router.get("/top-products", response_model=List[TopProductItem])
async def get_top_products(
    from_date: Optional[date] = Query(None, description="Дата начала периода"),
    to_date: Optional[date] = Query(None, description="Дата окончания периода"),
    limit: int = Query(10, ge=1, le=100, description="Количество товаров в топе"),
    service: ReportService = Depends(get_report_service),
):
    """
    Топ товаров по движениям

    Показывает самые активные товары за указанный период.

    **Параметры:**
    - **from_date**: Дата начала периода (опционально)
    - **to_date**: Дата окончания периода (опционально)
    - **limit**: Количество товаров в топе (по умолчанию 10)

    **Возвращает:**
    - Список топ товаров с количеством движений
    """
    return await service.get_top_products(from_date, to_date, limit)


@router.get("/abc-analysis", response_model=List[ABCAnalysisItem])
async def get_abc_analysis(
    from_date: date = Query(..., description="Дата начала периода"),
    to_date: date = Query(..., description="Дата окончания периода"),
    service: ReportService = Depends(get_report_service),
):
    """
    ABC-анализ товаров

    Классифицирует товары по частоте движений:
    - **A**: 80% всех движений (самые активные товары)
    - **B**: следующие 15% движений
    - **C**: оставшиеся 5% (редко перемещаемые товары)

    **Параметры:**
    - **from_date**: Дата начала периода (обязательно)
    - **to_date**: Дата окончания периода (обязательно)

    **Возвращает:**
    - Список товаров с ABC-классификацией
    """
    return await service.get_abc_analysis(from_date, to_date)


@router.get("/turnover", response_model=List[TurnoverItem])
async def get_turnover_report(
    from_date: date = Query(..., description="Дата начала периода"),
    to_date: date = Query(..., description="Дата окончания периода"),
    service: ReportService = Depends(get_report_service),
):
    """
    Отчёт оборачиваемости товаров

    Показывает скорость оборота товаров за период:
    - **turnover_ratio**: коэффициент оборачиваемости (отгружено / средний остаток)
    - **days_of_inventory**: на сколько дней хватит запаса при текущих отгрузках

    **Параметры:**
    - **from_date**: Дата начала периода (обязательно)
    - **to_date**: Дата окончания периода (обязательно)

    **Возвращает:**
    - Список товаров с метриками оборачиваемости
    """
    return await service.get_turnover_report(from_date, to_date)


@router.get("/batches", response_model=List[BatchReportItem])
async def get_batches_report(
    product_id: Optional[str] = Query(None, description="Фильтр по ID товара"),
    service: ReportService = Depends(get_report_service),
):
    """
    Отчёт по партиям (FIFO/FEFO)

    Показывает остатки товаров по партиям с датой первой приёмки.
    Отсортировано по дате приёмки - старые партии должны отгружаться первыми.

    **Параметры:**
    - **product_id**: Фильтр по ID товара (опционально)

    **Возвращает:**
    - Список партий с количеством и датой приёмки
    """
    return await service.get_batches_report(product_id)
