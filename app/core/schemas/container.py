"""Pydantic схемы для контейнеров"""

from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.enums import ContainerStatus, ContainerType


class ContainerContent(BaseModel):
    """Содержимое контейнера"""

    product_id: str = Field(..., description="ID товара")
    quantity: int = Field(..., ge=1, description="Количество")
    batch_number: Optional[str] = Field(None, description="Номер партии")
    is_scanned: bool = Field(default=False, description="Отсканирован ли товар")


class ContainerRegister(BaseModel):
    """Схема для регистрации контейнера"""

    qr_code: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^\S(?:.*\S)?$",
        description="Неизменяемый внешний QR-код контейнера, уникальный навсегда.",
    )
    container_type: ContainerType = Field(..., description="Тип контейнера")
    location_code: str = Field(..., description="Код локации размещения")
    contents: List[ContainerContent] = Field(..., description="Содержимое контейнера")


class ContainerRegisterResponse(BaseModel):
    """Ответ при регистрации контейнера"""

    container_id: int = Field(..., description="ID созданного контейнера")
    qr_code: str = Field(..., description="QR-код контейнера")
    items_registered: int = Field(..., description="Количество зарегистрированных позиций")

    class Config:
        from_attributes = True


class ContainerContentResponse(BaseModel):
    """Содержимое контейнера в ответе API"""

    product_id: str
    product_name: Optional[str] = None
    quantity: int
    batch_number: Optional[str] = None
    is_scanned: bool = False


class ContainerResponse(BaseModel):
    """Схема для ответа API - детали контейнера"""

    container_id: int = Field(..., description="ID контейнера")
    qr_code: str = Field(..., description="QR-код")
    container_type: ContainerType = Field(..., description="Тип контейнера")
    status: ContainerStatus = Field(..., description="Статус контейнера")
    location_code: Optional[str] = Field(None, description="Код локации")
    zone_type: Optional[str] = Field(None, description="Тип зоны")
    parent_container_id: Optional[int] = Field(None, description="ID родительского контейнера")
    parent_qr_code: Optional[str] = Field(None, description="QR-код родительского контейнера")
    metadata: Optional[dict] = Field(None, description="Метаданные")
    created_at: datetime = Field(..., description="Дата создания")
    updated_at: datetime = Field(..., description="Дата обновления")
    contents: Optional[List[dict]] = Field(None, description="Содержимое контейнера")

    class Config:
        from_attributes = True


class ContainerStatusUpdate(BaseModel):
    """Схема для обновления статуса контейнера"""

    status: ContainerStatus = Field(..., description="Новый статус")


class ContainerStatusUpdateResponse(BaseModel):
    """Ответ при обновлении статуса контейнера"""

    container_id: int
    qr_code: str
    status: ContainerStatus
    updated_at: datetime

    class Config:
        from_attributes = True


class ContainerHistoryItem(BaseModel):
    """Элемент истории контейнера"""

    movement_id: int
    movement_type: str
    product_id: str
    product_name: Optional[str] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    quantity: int
    batch_number: Optional[str] = None
    user_name: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ContainerInLocation(BaseModel):
    """Контейнер в локации (краткая информация)"""

    container_id: int
    qr_code: str
    container_type: ContainerType
    status: ContainerStatus
    products_count: int = Field(default=0, description="Количество уникальных товаров")
    total_units: int = Field(default=0, description="Общее количество единиц товара")
    created_at: datetime

    class Config:
        from_attributes = True
