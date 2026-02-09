"""Pydantic схемы для движений товаров"""

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime, date
from app.core.enums import MovementType


class MovementCreate(BaseModel):
    """Схема для создания движения"""

    movement_type: MovementType = Field(..., description="Тип движения")
    product_id: str = Field(..., description="ID товара")
    from_location_code: Optional[str] = Field(None, description="Код локации-источника")
    to_location_code: Optional[str] = Field(None, description="Код локации-назначения")
    quantity: int = Field(..., ge=1, description="Количество")
    batch_number: Optional[str] = Field(None, description="Номер партии")
    container_code: Optional[str] = Field(None, description="Код контейнера")
    user_name: Optional[str] = Field(None, description="Имя пользователя")
    reason: Optional[str] = Field(None, description="Причина/комментарий")


class MovementCreateResponse(BaseModel):
    """Ответ при создании движения"""

    movement_id: int = Field(..., description="ID созданного движения")
    movement_type: MovementType
    product_id: str
    from_location_id: Optional[int] = None
    to_location_id: Optional[int] = None
    quantity: int
    created_at: datetime

    class Config:
        from_attributes = True


class MovementResponse(BaseModel):
    """Движение товара в ответе API"""

    movement_id: int
    movement_type: MovementType
    product_id: str
    product_name: Optional[str] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    quantity: int
    batch_number: Optional[str] = None
    container_code: Optional[str] = None
    user_name: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MovementFilter(BaseModel):
    """Фильтры для получения движений"""

    product_id: Optional[str] = Field(None, description="Фильтр по ID товара")
    container_code: Optional[str] = Field(None, description="Фильтр по коду контейнера")
    movement_type: Optional[MovementType] = Field(None, description="Фильтр по типу движения")
    from_date: Optional[date] = Field(None, description="Дата начала периода")
    to_date: Optional[date] = Field(None, description="Дата окончания периода")
    limit: int = Field(default=100, ge=1, le=1000, description="Лимит записей")
    offset: int = Field(default=0, ge=0, description="Смещение")
