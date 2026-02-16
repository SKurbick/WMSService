"""Pydantic схемы для локаций"""

from typing import Optional, List
from decimal import Decimal
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.enums import ZoneType

class ZoneResponse(BaseModel):
    """Схема для ответа - список зон"""

    location_id: int
    location_code: str
    name: str
    zone_type: ZoneType
    level: int
    path: str
    is_active: bool
    is_pickable: bool
    max_weight: Optional[Decimal] = Field(default=None, ge=0, description="Максимальный вес (кг)")
    max_volume: Optional[Decimal] = Field(default=None, ge=0, description="Максимальный объём (м³)")
    metadata: Optional[dict]
    warehouse_code: Optional[str] = Field(None, description="Код склада (родитель)")
    warehouse_name: Optional[str] = Field(None, description="Название склада")

    class Config:
        from_attributes = True

class LocationBase(BaseModel):
    """Базовая схема локации"""

    name: str = Field(..., min_length=1, max_length=100, description="Название локации")
    zone_type: ZoneType = Field(..., description="Тип зоны")
    level: int = Field(..., ge=1, le=5, description="Уровень в иерархии (1-5)")
    max_weight: Optional[Decimal] = Field(default=Decimal("0"), ge=0, description="Максимальный вес (кг)")
    max_volume: Optional[Decimal] = Field(default=Decimal("0"), ge=0, description="Максимальный объём (м³)")
    is_active: bool = Field(default=True, description="Активна ли локация")
    is_pickable: bool = Field(default=False, description="Можно ли комплектовать из этой локации")
    metadata: Optional[dict] = Field(None, description="Дополнительные данные (JSON)")


class LocationCreate(LocationBase):
    """Схема для создания локации"""

    parent_location_id: Optional[int] = Field(None, description="ID родительской локации")


class LocationUpdate(BaseModel):
    """Схема для обновления локации (частичное)"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    zone_type: Optional[ZoneType] = None
    max_weight: Optional[Decimal] = Field(None, ge=0)
    max_volume: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None
    is_pickable: Optional[bool] = None
    metadata: Optional[dict] = None


class LocationResponse(LocationBase):
    """Схема для ответа API"""

    location_id: int = Field(..., description="ID локации")
    location_code: str = Field(..., description="Код локации (автогенерируется)")
    path: str = Field(..., description="Путь в иерархии (LTREE)")
    parent_location_id: Optional[int] = Field(None, description="ID родителя")
    parent_location_code: Optional[str] = Field(None, description="Код родителя")
    parent_name: Optional[str] = Field(None, description="Название родителя")
    created_at: datetime = Field(..., description="Дата создания")
    updated_at: datetime = Field(..., description="Дата обновления")

    class Config:
        from_attributes = True


class LocationChildResponse(BaseModel):
    """Упрощённая схема для дочерних локаций"""

    location_id: int
    location_code: str
    name: str
    zone_type: ZoneType
    level: int
    path: str
    is_active: bool
    depth: Optional[int] = Field(None, description="Глубина вложенности от родителя")

    class Config:
        from_attributes = True


class LocationDeactivateResponse(BaseModel):
    """Схема для ответа деактивации"""

    location_id: int
    location_code: str
    is_active: bool

    class Config:
        from_attributes = True


class LocationTreeNode(BaseModel):
    """Узел дерева локаций (рекурсивная структура)"""

    location_id: int
    location_code: str
    name: str
    zone_type: ZoneType
    level: int
    path: str
    is_active: bool
    is_pickable: bool
    max_weight: Optional[Decimal] = None
    max_volume: Optional[Decimal] = None
    metadata: Optional[dict] = None
    children: List['LocationTreeNode'] = Field(default_factory=list, description="Дочерние локации")

    class Config:
        from_attributes = True


# обновить forward reference для рекурсии
LocationTreeNode.model_rebuild()