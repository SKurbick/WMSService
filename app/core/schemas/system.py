"""Pydantic схемы для системных операций"""

from typing import Optional
from pydantic import BaseModel, Field
from datetime import date, datetime


class RecalculateInventoryRequest(BaseModel):
    """Запрос на пересчёт остатков"""

    product_id: Optional[str] = Field(None, description="ID товара (опционально, если None - все)")
    from_date: Optional[date] = Field(None, description="Пересчитать с даты (опционально)")


class RecalculateInventoryResponse(BaseModel):
    """Результат пересчёта остатков"""

    inventory_records: int = Field(..., description="Количество записей в inventory")
    total_units: int = Field(..., description="Общее количество единиц")
    products_count: int = Field(..., description="Количество уникальных товаров")

    class Config:
        from_attributes = True


class CreateSnapshotRequest(BaseModel):
    """Запрос на создание снимка остатков"""

    snapshot_date: Optional[date] = Field(None, description="Дата снимка (по умолчанию сегодня)")


class CreateSnapshotResponse(BaseModel):
    """Результат создания снимка"""

    snapshot_date: date = Field(..., description="Дата снимка")
    records_count: int = Field(..., description="Количество записей")
    total_units: int = Field(..., description="Общее количество единиц")
    products_count: int = Field(..., description="Количество уникальных товаров")

    class Config:
        from_attributes = True


class RefreshViewsResponse(BaseModel):
    """Результат обновления материализованных представлений"""

    view_name: str = Field(..., description="Название представления")
    records_count: int = Field(..., description="Количество записей")
    total_units: int = Field(..., description="Общее количество единиц")
    refreshed_at: datetime = Field(..., description="Время обновления")

    class Config:
        from_attributes = True


class IntegrityCheckResult(BaseModel):
    """Результат проверки целостности данных"""

    product_id: str
    location_code: Optional[str] = None
    batch_number: Optional[str] = None
    container_code: Optional[str] = None
    from_movements: int = Field(..., description="Рассчитано из movements")
    from_inventory: int = Field(..., description="Текущее в inventory")
    difference: int = Field(..., description="Разница")

    class Config:
        from_attributes = True
