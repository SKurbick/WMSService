"""Pydantic схемы для отчётов"""

from typing import Optional
from decimal import Decimal
from pydantic import BaseModel, Field
from datetime import datetime


class ZoneReportItem(BaseModel):
    """Элемент отчёта по зонам"""

    zone_type: str = Field(..., description="Тип зоны")
    occupied_locations: int = Field(default=0, description="Занятых локаций")
    products_count: int = Field(default=0, description="Количество уникальных товаров")
    total_units: int = Field(default=0, description="Общее количество единиц")
    containers_count: int = Field(default=0, description="Количество контейнеров")

    class Config:
        from_attributes = True


class TopProductItem(BaseModel):
    """Элемент топа товаров по движениям"""

    product_id: str
    product_name: Optional[str] = None
    category: Optional[str] = None
    movements_count: int = Field(..., description="Количество движений")
    total_moved: int = Field(..., description="Всего перемещено единиц")
    movement_types_count: int = Field(..., description="Типов движений")

    class Config:
        from_attributes = True


class ABCAnalysisItem(BaseModel):
    """Элемент ABC-анализа"""

    product_id: str
    product_name: Optional[str] = None
    movements_count: int
    total_quantity: int
    cumulative_percentage: Decimal = Field(..., description="Накопленный процент")
    abc_class: str = Field(..., description="Класс ABC (A, B или C)")

    class Config:
        from_attributes = True


class TurnoverItem(BaseModel):
    """Элемент отчёта оборачиваемости"""

    product_id: str
    product_name: Optional[str] = None
    shipped_quantity: int = Field(..., description="Отгружено единиц")
    avg_inventory: Decimal = Field(default=Decimal("0"), description="Средний остаток")
    turnover_ratio: Optional[Decimal] = Field(None, description="Коэффициент оборачиваемости")
    days_of_inventory: Optional[Decimal] = Field(None, description="Дней запаса")

    class Config:
        from_attributes = True


class BatchReportItem(BaseModel):
    """Элемент отчёта по партиям"""

    product_id: str
    product_name: Optional[str] = None
    batch_number: str = Field(..., description="Номер партии")
    location_code: str
    zone_type: Optional[str] = None
    total_quantity: int
    first_received_at: Optional[datetime] = Field(None, description="Дата первой приёмки")
    locations_count: int = Field(..., description="Количество локаций")

    class Config:
        from_attributes = True
