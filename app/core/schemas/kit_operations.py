"""Pydantic схемы для операций комплектации и разукомплектации."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.enums import KitOperationItemRole, KitOperationStatus, KitOperationType


class KitOperationCreate(BaseModel):
    """Запрос на комплектацию или разукомплектацию комплекта."""

    operation_type: KitOperationType = Field(
        ...,
        description=(
            "Что передавать в operation_type: `assembly` - собрать комплект из компонентов; "
            "`disassembly` - разобрать комплект обратно на компоненты. Другие значения не принимаются."
        ),
        examples=["assembly"],
    )
    kit_product_id: str = Field(
        ...,
        min_length=1,
        description="ID товара-комплекта из public.products.id.",
        examples=["metawild_test"],
    )
    quantity: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Количество комплектов для сборки или разборки. Должно быть больше 0.",
        examples=["3.00"],
    )
    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Пользователь или системный автор операции.",
        examples=["manual-test"],
    )
    location_code: str = Field(
        ...,
        min_length=1,
        description=(
            "Код WMS-локации. Локация должна быть активной и разрешённой в "
            "wms.operation_locations для operation_code='kit_operations', scope='direct'."
        ),
        examples=["PUSHKINO-КОМПЛЕКТАЦИЯ"],
    )

    class Config:
        json_schema_extra = {
            "description": (
                "В `operation_type` передавайте только одно из двух значений: "
                "`assembly` для сборки комплекта или `disassembly` для разборки комплекта."
            ),
            "examples": [
                {
                    "operation_type": "assembly",
                    "kit_product_id": "metawild_test",
                    "quantity": "3.00",
                    "author": "manual-test",
                    "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                },
                {
                    "operation_type": "disassembly",
                    "kit_product_id": "metawild_test",
                    "quantity": "3.00",
                    "author": "manual-test",
                    "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                },
            ]
        }


class KitOperationLocationCreate(BaseModel):
    """Запрос на добавление разрешённой локации комплектации."""

    location_code: str = Field(
        ...,
        min_length=1,
        description="Код активной WMS-локации, которую нужно разрешить для kit_operations.",
        examples=["PUSHKINO-КОМПЛЕКТАЦИЯ"],
    )
    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Пользователь, который добавляет или реактивирует разрешение.",
        examples=["admin"],
    )
    metadata: Optional[dict] = Field(
        None,
        description="Произвольные служебные метаданные разрешения.",
        examples=[{"comment": "Основная зона комплектации"}],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                "author": "admin",
                "metadata": {
                    "comment": "Основная зона комплектации для metawild_test"
                },
            }
        }


class KitOperationLocationDeactivate(BaseModel):
    """Запрос на деактивацию разрешённой локации комплектации."""

    author: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Пользователь, который деактивирует разрешённую локацию.",
        examples=["admin"],
    )

    class Config:
        json_schema_extra = {"example": {"author": "admin"}}


class KitOperationLocationResponse(BaseModel):
    """Разрешённая локация для операций комплектов."""

    operation_location_id: int = Field(..., description="ID разрешения в wms.operation_locations.")
    operation_code: str = Field(..., description="Код операции. Для модуля комплектов: kit_operations.")
    location_id: int = Field(..., description="ID WMS-локации из wms.locations.")
    location_code: str = Field(..., description="Код WMS-локации.")
    location_name: Optional[str] = Field(None, description="Название WMS-локации.")
    scope: str = Field(..., description="Область действия. В MVP поддерживается только direct.")
    is_active: bool = Field(..., description="Активно ли разрешение.")
    author: Optional[str] = Field(None, description="Последний автор изменения разрешения.")
    metadata: Optional[dict] = Field(None, description="Служебные метаданные разрешения.")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "operation_location_id": 1,
                "operation_code": "kit_operations",
                "location_id": 123,
                "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                "location_name": "Комплектация",
                "scope": "direct",
                "is_active": True,
                "author": "admin",
                "metadata": {"comment": "Основная зона комплектации"},
                "created_at": "2026-07-07T12:00:00+03:00",
                "updated_at": "2026-07-07T12:00:00+03:00",
            }
        }


class KitOperationLocationListResponse(BaseModel):
    """Список разрешённых локаций для операций комплектов."""

    items: List[KitOperationLocationResponse]
    limit: int = Field(..., description="Лимит страницы.")
    offset: int = Field(..., description="Смещение страницы.")
    total: int = Field(..., description="Общее количество строк по фильтру.")

    class Config:
        json_schema_extra = {
            "example": {
                "items": [
                    {
                        "operation_location_id": 1,
                        "operation_code": "kit_operations",
                        "location_id": 123,
                        "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                        "location_name": "Комплектация",
                        "scope": "direct",
                        "is_active": True,
                        "author": "admin",
                        "metadata": {"comment": "Основная зона комплектации"},
                        "created_at": "2026-07-07T12:00:00+03:00",
                        "updated_at": "2026-07-07T12:00:00+03:00",
                    }
                ],
                "limit": 50,
                "offset": 0,
                "total": 1,
            }
        }


class KitOperationItemResponse(BaseModel):
    """Строка операции комплекта."""

    item_id: int = Field(..., description="ID строки операции.")
    role: KitOperationItemRole = Field(
        ...,
        description=(
            "Роль строки товара внутри операции. `component_consumption` - компонент, "
            "который списали при сборке комплекта; `kit_result` - готовый комплект, "
            "который получили при сборке; `kit_consumption` - готовый комплект, "
            "который списали при разукомплектации; `component_result` - компонент, "
            "который получили после разукомплектации. Для комплекта из двух компонентов "
            "обычно создаётся 3 строки на assembly и 3 строки на disassembly."
        ),
        examples=["component_consumption"],
        json_schema_extra={
            "x-role-descriptions": {
                "component_consumption": "Компонент списан при сборке комплекта.",
                "kit_result": "Готовый комплект получен при сборке.",
                "kit_consumption": "Готовый комплект списан при разукомплектации.",
                "component_result": "Компонент получен после разукомплектации.",
            }
        },
    )
    product_id: str = Field(..., description="ID товара компонента или комплекта.")
    quantity_per_kit: Decimal = Field(..., description="Количество товара на один комплект.")
    total_quantity: Decimal = Field(..., description="Итоговое количество по строке операции.")
    movement_id: Optional[int] = Field(None, description="ID созданного movement.")

    class Config:
        from_attributes = True


class KitOperationResponse(BaseModel):
    """Детальный ответ по операции комплекта."""

    operation_id: int = Field(..., description="ID операции комплекта.")
    operation_location_id: Optional[int] = Field(
        None,
        description="ID разрешённой локации из wms.operation_locations, использованной операцией.",
    )
    operation_type: KitOperationType
    kit_product_id: str
    quantity: Decimal
    location_code: str
    status: KitOperationStatus
    author: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    items: List[KitOperationItemResponse]

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "operation_id": 123,
                "operation_location_id": 1,
                "operation_type": "assembly",
                "kit_product_id": "metawild_test",
                "quantity": "3.00",
                "location_code": "PUSHKINO-КОМПЛЕКТАЦИЯ",
                "status": "completed",
                "author": "manual-test",
                "created_at": "2026-07-07T12:05:00+03:00",
                "completed_at": "2026-07-07T12:05:01+03:00",
                "items": [
                    {
                        "item_id": 1,
                        "role": "component_consumption",
                        "product_id": "testwild",
                        "quantity_per_kit": "2.00",
                        "total_quantity": "6.00",
                        "movement_id": 1001,
                    },
                    {
                        "item_id": 2,
                        "role": "component_consumption",
                        "product_id": "testwild2",
                        "quantity_per_kit": "1.00",
                        "total_quantity": "3.00",
                        "movement_id": 1002,
                    },
                    {
                        "item_id": 3,
                        "role": "kit_result",
                        "product_id": "metawild_test",
                        "quantity_per_kit": "1.00",
                        "total_quantity": "3.00",
                        "movement_id": 1003,
                    },
                ],
            }
        }


class KitOperationSummaryResponse(BaseModel):
    """Строка списка операций комплектов."""

    operation_id: int
    operation_location_id: Optional[int] = None
    operation_type: KitOperationType
    kit_product_id: str
    quantity: Decimal
    location_code: str
    status: KitOperationStatus
    author: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
