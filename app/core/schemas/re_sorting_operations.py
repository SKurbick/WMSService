"""Pydantic-схемы операций пересортицы товара."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.enums import ReSortingOperationItemRole, ReSortingOperationStatus
from app.core.exceptions import ReSortingOperationValidationError

LOCATION = "PUSHKINO-ПЕРЕСОРТИЦА"
REASON = "Товар того же вида, но другого цвета был учтён под неверным артикулом"
CREATED = "2026-07-16T10:15:30+03:00"
COMPLETED = "2026-07-16T10:15:31+03:00"


def _trimmed(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("значение не может быть пустым")
    return value


class ReSortingOperationCreate(BaseModel):
    """Запрос на переидентификацию physical loose-остатка между двумя SKU."""

    from_product_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="ID исходного товара из public.products.id. Его loose-остаток уменьшается.",
        examples=["wild100"],
    )
    to_product_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="ID целевого товара из public.products.id. Его loose-остаток увеличивается.",
        examples=["wild101"],
    )
    quantity: int = Field(
        ...,
        gt=0,
        strict=True,
        description="Целое положительное количество, одинаковое для расхода и прихода.",
        examples=[4],
    )
    location_code: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Код активной direct-локации, разрешённой для re_sorting_operations. Дочерние адреса не учитываются.",
        examples=[LOCATION],
    )
    reason: str = Field(
        ..., min_length=1, description="Обязательная бизнес-причина пересортицы.", examples=[REASON]
    )
    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Логин оператора или имя системного автора.",
        examples=["operator.pushkino"],
    )
    _trim_fields = field_validator(
        "from_product_id", "to_product_id", "location_code", "reason", "author"
    )(_trimmed)

    @model_validator(mode="after")
    def products_must_differ(self):
        if self.from_product_id == self.to_product_id:
            raise ReSortingOperationValidationError(
                "from_product_id и to_product_id должны различаться"
            )
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "from_product_id": "wild100",
                "to_product_id": "wild101",
                "quantity": 4,
                "location_code": LOCATION,
                "reason": REASON,
                "author": "operator.pushkino",
            }
        }


class ReSortingOperationLocationCreate(BaseModel):
    """Запрос на добавление или реактивацию локации пересортицы."""

    location_code: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Код существующей активной локации из wms.locations.",
        examples=[LOCATION],
    )
    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Автор изменения allow-list.",
        examples=["admin.wms"],
    )
    metadata: Optional[dict] = Field(
        None,
        description="Необязательные служебные метаданные разрешения.",
        examples=[{"comment": "Зона ручной пересортицы склада Пушкино"}],
    )
    _trim_fields = field_validator("location_code", "author")(_trimmed)

    class Config:
        json_schema_extra = {
            "example": {
                "location_code": LOCATION,
                "author": "admin.wms",
                "metadata": {"comment": "Зона ручной пересортицы склада Пушкино"},
            }
        }


class ReSortingOperationLocationDeactivate(BaseModel):
    """Запрос на деактивацию разрешённой локации пересортицы."""

    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Автор деактивации разрешения.",
        examples=["admin.wms"],
    )
    _trim_author = field_validator("author")(_trimmed)

    class Config:
        json_schema_extra = {"example": {"author": "admin.wms"}}


class ReSortingOperationLocationResponse(BaseModel):
    """Разрешённая direct-локация пересортицы."""

    operation_location_id: int = Field(..., description="ID разрешения в wms.operation_locations.")
    operation_code: str = Field(..., description="Код операции; всегда re_sorting_operations.")
    location_id: int = Field(..., description="ID локации из wms.locations.")
    location_code: str = Field(..., description="Фактический код WMS-локации.")
    location_name: Optional[str] = Field(None, description="Название WMS-локации.")
    scope: str = Field(..., description="Область действия; в MVP всегда direct.")
    is_active: bool = Field(..., description="Активно ли разрешение на пересортицу.")
    author: Optional[str] = Field(None, description="Автор последнего изменения.")
    metadata: Optional[dict] = Field(None, description="Служебные метаданные разрешения.")
    created_at: datetime = Field(..., description="Дата создания разрешения.")
    updated_at: datetime = Field(..., description="Дата последнего изменения разрешения.")

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "operation_location_id": 17,
                "operation_code": "re_sorting_operations",
                "location_id": 305,
                "location_code": LOCATION,
                "location_name": "Зона пересортицы",
                "scope": "direct",
                "is_active": True,
                "author": "admin.wms",
                "metadata": {"comment": "Зона ручной пересортицы склада Пушкино"},
                "created_at": CREATED,
                "updated_at": CREATED,
            }
        }


class ReSortingOperationLocationListResponse(BaseModel):
    """Страница разрешённых локаций пересортицы."""

    items: List[ReSortingOperationLocationResponse] = Field(
        ..., description="Локации текущей страницы."
    )
    limit: int = Field(..., description="Размер страницы.")
    offset: int = Field(..., description="Смещение от начала списка.")
    total: int = Field(..., description="Общее количество строк по фильтру.")

    class Config:
        json_schema_extra = {
            "example": {
                "items": [ReSortingOperationLocationResponse.Config.json_schema_extra["example"]],
                "limit": 50,
                "offset": 0,
                "total": 1,
            }
        }


class ReSortingOperationItemResponse(BaseModel):
    """Строка пересортицы, связанная с одним movement."""

    item_id: int = Field(..., description="ID строки операции.")
    operation_id: int = Field(..., description="ID операции пересортицы.")
    role: ReSortingOperationItemRole = Field(
        ...,
        description="source_outgoing — расход исходного SKU; target_incoming — приход целевого SKU.",
        examples=["source_outgoing"],
        json_schema_extra={
            "x-role-descriptions": {
                "source_outgoing": "Списание исходного loose-остатка.",
                "target_incoming": "Приход целевого loose-остатка.",
            }
        },
    )
    product_id: str = Field(..., description="ID товара этой стороны операции.")
    quantity: int = Field(..., description="Целое количество, равное quantity операции.")
    movement_id: Optional[int] = Field(None, description="ID связанного movement.")
    movement_created_at: Optional[datetime] = Field(
        None, description="Дата movement, часть составной audit-ссылки."
    )
    created_at: datetime = Field(..., description="Дата создания строки.")

    class Config:
        from_attributes = True


class ReSortingOperationSummaryResponse(BaseModel):
    """Заголовок операции пересортицы."""

    operation_id: int = Field(..., description="ID операции в wms.re_sorting_operations.")
    operation_location_id: int = Field(..., description="ID использованного allow-list разрешения.")
    from_product_id: str = Field(..., description="SKU, остаток которого уменьшен.")
    to_product_id: str = Field(..., description="SKU, остаток которого увеличен.")
    quantity: int = Field(..., description="Одинаковое целое количество расхода и прихода.")
    location_id: int = Field(..., description="ID точной direct-локации.")
    location_code: str = Field(..., description="Код точной direct-локации.")
    reason: str = Field(..., description="Бизнес-причина пересортицы.")
    author: str = Field(..., description="Автор операции.")
    status: ReSortingOperationStatus = Field(
        ..., description="Статус: processing, completed или failed."
    )
    error_message: Optional[str] = Field(
        None, description="Ошибка failed-операции; для completed равна null."
    )
    metadata: dict = Field(..., description="Служебные метаданные заголовка.")
    created_at: datetime = Field(..., description="Дата создания операции.")
    completed_at: Optional[datetime] = Field(None, description="Дата завершения операции.")
    updated_at: datetime = Field(..., description="Дата последнего изменения.")

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "operation_id": 4812,
                "operation_location_id": 17,
                "from_product_id": "wild100",
                "to_product_id": "wild101",
                "quantity": 4,
                "location_id": 305,
                "location_code": LOCATION,
                "reason": REASON,
                "author": "operator.pushkino",
                "status": "completed",
                "error_message": None,
                "metadata": {},
                "created_at": CREATED,
                "completed_at": COMPLETED,
                "updated_at": COMPLETED,
            }
        }


class ReSortingOperationResponse(ReSortingOperationSummaryResponse):
    """Детальная карточка пересортицы с двумя movement-строками."""

    items: List[ReSortingOperationItemResponse] = Field(
        default_factory=list,
        description="Для completed-операции ровно две строки: source_outgoing и target_incoming.",
    )

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                **ReSortingOperationSummaryResponse.Config.json_schema_extra["example"],
                "items": [
                    {
                        "item_id": 9623,
                        "operation_id": 4812,
                        "role": "source_outgoing",
                        "product_id": "wild100",
                        "quantity": 4,
                        "movement_id": 155001,
                        "movement_created_at": CREATED,
                        "created_at": CREATED,
                    },
                    {
                        "item_id": 9624,
                        "operation_id": 4812,
                        "role": "target_incoming",
                        "product_id": "wild101",
                        "quantity": 4,
                        "movement_id": 155002,
                        "movement_created_at": COMPLETED,
                        "created_at": COMPLETED,
                    },
                ],
            }
        }
