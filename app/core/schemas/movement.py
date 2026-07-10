"""Pydantic схемы для движений товаров"""

from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, date
from app.core.enums import MovementType


class MovementCreate(BaseModel):
    """Схема для создания движения"""

    movement_type: MovementType = Field(
        ...,
        description=(
            "Тип движения. Определяет бизнес-смысл операции. Фактическое изменение "
            "остатка зависит от направления: from_location_code уменьшает остаток, "
            "to_location_code увеличивает остаток. Для ручной корректировки используйте "
            "`adjust`."
        ),
        examples=["adjust"],
    )
    product_id: str = Field(
        ...,
        description=(
            "ID товара/SKU из public.products.id. В проекте обычно используется wild-код, "
            'например "wild1825".'
        ),
        examples=["wild1825"],
    )
    from_location_code: Optional[str] = Field(
        None,
        description=(
            "Код локации-источника. Если заполнен, quantity будет списано из этой "
            "локации. Для увеличения остатка через adjust оставьте null."
        ),
        examples=["RECEIVING-001"],
    )
    to_location_code: Optional[str] = Field(
        None,
        description=(
            "Код локации-получателя. Если заполнен, quantity будет добавлено в эту "
            "локацию. Для уменьшения остатка через adjust оставьте null."
        ),
        examples=["STORAGE-A-01"],
    )
    quantity: int = Field(
        ...,
        ge=1,
        description=(
            "Положительное количество товара. Для списания не используйте отрицательные "
            "значения: направление списания задается через from_location_code."
        ),
        examples=[10],
    )
    batch_number: Optional[str] = Field(
        None,
        description=(
            "Партия товара. Должна совпадать с партией в остатках при расходных "
            "операциях. null означает остаток без партии."
        ),
        examples=[None],
    )
    container_code: Optional[str] = Field(
        None,
        description=(
            "QR/code контейнера. Используется для контейнерных остатков. Для обычной "
            "россыпи передавайте null."
        ),
        examples=[None],
    )
    user_name: Optional[str] = Field(
        None,
        description="Пользователь/автор операции для аудита.",
        examples=["admin"],
    )
    reason: Optional[str] = Field(
        None,
        description=(
            "Причина операции. Для ручных корректировок обязательно передавать понятное "
            "описание причины."
        ),
        examples=["Ручная корректировка: добавление 10 шт после пересчета"],
    )

    class Config:
        json_schema_extra = {
            "description": (
                "Movement создаёт событие в wms.movements. Остатки в wms.inventory "
                "обновляются триггером после insert movement. Хотя бы одна сторона "
                "from_location_code или to_location_code должна быть заполнена."
            ),
            "examples": [
                {
                    "movement_type": "adjust",
                    "product_id": "wild1825",
                    "from_location_code": None,
                    "to_location_code": "RECEIVING-001",
                    "quantity": 10,
                    "batch_number": None,
                    "container_code": None,
                    "user_name": "admin",
                    "reason": "Ручная корректировка: добавление 10 шт после пересчета",
                }
            ],
        }


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


class MovementBulkCreateResponse(BaseModel):
    """Ответ при создании нескольких movements"""

    created: List[MovementCreateResponse] = Field(
        ...,
        description="Список созданных movements"
    )
    total: int = Field(
        ...,
        description="Количество созданных movements"
    )

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
