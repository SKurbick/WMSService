"""Pydantic схемы для инвентаря (остатков)"""

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.enums import InventoryStatus


class InventoryItemResponse(BaseModel):
    """Элемент остатка товара"""

    inventory_id: int = Field(..., description="ID записи остатка")
    product_id: str = Field(..., description="ID товара")
    product_name: Optional[str] = Field(None, description="Название товара")
    location_code: str = Field(..., description="Код локации")
    zone_type: Optional[str] = Field(None, description="Тип зоны")
    quantity: int = Field(..., description="Количество")
    status: InventoryStatus = Field(..., description="Статус остатка")
    batch_number: Optional[str] = Field(None, description="Номер партии")
    container_code: Optional[str] = Field(None, description="Код контейнера")
    updated_at: datetime = Field(..., description="Дата обновления")

    class Config:
        from_attributes = True


class InventoryInLocationResponse(BaseModel):
    """Остаток в локации"""

    inventory_id: int
    product_id: str
    product_name: Optional[str] = None
    category: Optional[str] = None
    quantity: int
    status: InventoryStatus
    batch_number: Optional[str] = None
    container_code: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class InventorySummaryResponse(BaseModel):
    """Агрегированный остаток товара"""

    product_id: str
    product_name: Optional[str] = None
    category: Optional[str] = None
    total_quantity: int = Field(default=0, description="Общее количество")
    locations_count: int = Field(default=0, description="Количество локаций")
    in_containers: int = Field(default=0, description="Количество в контейнерах")
    loose: int = Field(default=0, description="Количество россыпью")
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True


class InventoryInContainerResponse(BaseModel):
    """Остаток в контейнере"""

    product_id: str
    product_name: Optional[str] = None
    quantity: int
    batch_number: Optional[str] = None
    location_code: str
    zone_type: Optional[str] = None

    class Config:
        from_attributes = True


class LooseInventoryResponse(BaseModel):
    """Россыпь в локации"""

    product_id: str
    product_name: Optional[str] = None
    quantity: int
    batch_number: Optional[str] = None
    status: InventoryStatus

    class Config:
        from_attributes = True


class InventorySearchResult(BaseModel):
    """Результат поиска товара"""

    product_id: str
    product_name: Optional[str] = None
    location_code: str
    zone_type: Optional[str] = None
    quantity: int
    container_code: Optional[str] = None
    batch_number: Optional[str] = None
    status: InventoryStatus

    class Config:
        from_attributes = True
