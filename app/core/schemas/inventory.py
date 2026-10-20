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

    inventory_id: int = Field(description='Идентификатор строки физического остатка.')
    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    category: Optional[str] = Field(None, description='Категория товара; null, если не задана.')
    quantity: int = Field(description='Количество товара в указанной строке остатка.')
    status: InventoryStatus = Field(description='Статус остатка: available — доступен, reserved — зарезервирован, damaged — повреждён, quarantine — карантин.')
    batch_number: Optional[str] = Field(None, description='Номер партии; null означает отсутствие партии.')
    container_code: Optional[str] = Field(None, description='Код контейнера; null означает россыпь.')
    updated_at: datetime = Field(description='Дата и время последнего изменения строки остатка.')

    class Config:
        from_attributes = True


class InventorySummaryResponse(BaseModel):
    """Агрегированный остаток товара"""

    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    category: Optional[str] = Field(None, description='Категория товара; null, если не задана.')
    total_quantity: int = Field(default=0, description="Общее количество")
    locations_count: int = Field(default=0, description="Количество локаций")
    in_containers: int = Field(default=0, description="Количество в контейнерах")
    loose: int = Field(default=0, description="Количество россыпью")
    last_updated: Optional[datetime] = Field(None, description='Дата и время последнего обновления остатков в этой группе.')

    class Config:
        from_attributes = True


class InventoryLocationSummaryResponse(BaseModel):
    """Агрегированный остаток товара в локации и дочерних локациях"""

    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    category: Optional[str] = Field(None, description='Категория товара; null, если не задана.')
    total_quantity: int = Field(default=0, description="Общее количество")
    locations_count: int = Field(default=0, description="Количество локаций")
    in_containers: int = Field(default=0, description="Количество в контейнерах")
    loose: int = Field(default=0, description="Количество россыпью")
    last_updated: Optional[datetime] = Field(None, description='Дата и время последнего обновления остатков в этой группе.')

    class Config:
        from_attributes = True


class InventoryInContainerResponse(BaseModel):
    """Остаток в контейнере"""

    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    quantity: int = Field(description='Количество товара в указанной строке остатка.')
    batch_number: Optional[str] = Field(None, description='Номер партии; null означает отсутствие партии.')
    location_code: str = Field(description='Код адреса хранения.')
    zone_type: Optional[str] = Field(None, description='Тип складской зоны; может отсутствовать.')

    class Config:
        from_attributes = True


class LooseInventoryResponse(BaseModel):
    """Россыпь в локации"""

    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    quantity: int = Field(description='Количество товара в указанной строке остатка.')
    batch_number: Optional[str] = Field(None, description='Номер партии; null означает отсутствие партии.')
    status: InventoryStatus = Field(description='Статус остатка: available — доступен, reserved — зарезервирован, damaged — повреждён, quarantine — карантин.')

    class Config:
        from_attributes = True


class InventorySearchResult(BaseModel):
    """Результат поиска товара"""

    product_id: str = Field(description='Идентификатор товара (SKU).')
    product_name: Optional[str] = Field(None, description='Название товара; null, если отсутствует.')
    location_code: str = Field(description='Код адреса хранения.')
    zone_type: Optional[str] = Field(None, description='Тип складской зоны; может отсутствовать.')
    quantity: int = Field(description='Количество товара в указанной строке остатка.')
    container_code: Optional[str] = Field(None, description='Код контейнера; null означает россыпь.')
    batch_number: Optional[str] = Field(None, description='Номер партии; null означает отсутствие партии.')
    status: InventoryStatus = Field(description='Статус остатка: available — доступен, reserved — зарезервирован, damaged — повреждён, quarantine — карантин.')

    class Config:
        from_attributes = True
