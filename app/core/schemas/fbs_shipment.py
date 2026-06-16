"""Схемы для журнала отгрузок из ФБС зоны"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import json
from pydantic import BaseModel, Field, field_validator
from app.core.enums import FbsShipmentSource


class FbsShipmentItemResponse(BaseModel):
    """Позиция (товар) внутри одной отгрузки"""

    item_id: int = Field(description="Внутренний ID позиции")
    product_id: str = Field(description="Артикул товара")
    quantity: int = Field(description="Количество единиц к списанию")
    author: str = Field(description="Инициатор отгрузки")
    supply_id: str = Field(description="ID поставки/отправления")
    account: str = Field(description="Аккаунт маркетплейса")
    assembly_tasks: Any = Field(description="Список ID заданий сборки")
    status: str = Field(
        description="Статус позиции: new, success, failed, pending_retry, retry_exhausted"
    )
    error_message: Optional[str] = Field(
        None, description="Текст ошибки, если обработка завершилась неудачей"
    )
    retry_count: int = Field(description="Количество выполненных попыток обработки")
    movement_id: Optional[int] = Field(
        None, description="ID созданного движения товара (заполняется при success)"
    )
    created_at: datetime = Field(description="Дата создания позиции")
    updated_at: datetime = Field(description="Дата последнего обновления позиции")

    @field_validator("assembly_tasks", mode="before")
    @classmethod
    def parse_assembly_tasks(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class FbsShipmentListItem(BaseModel):
    """Элемент списка отгрузок — без raw_message и items (тяжёлые поля)"""

    shipment_id: int = Field(description="Внутренний ID записи журнала")
    received_at: datetime = Field(description="Дата и время получения сообщения из RabbitMQ")
    total_items: int = Field(description="Общее количество позиций в отгрузке")
    status: str = Field(
        description="Статус обработки: processing, completed, partially_completed, failed, validation_failed"
    )
    source: FbsShipmentSource = Field(description="Источник FBS-отгрузки")
    error_message: Optional[str] = Field(
        None, description="Текст ошибки (для validation_failed — текст исключения Pydantic)"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Дата завершения обработки всех позиций"
    )


class FbsShipmentListResponse(BaseModel):
    """Список отгрузок с пагинацией"""

    total: int = Field(description="Общее количество записей, соответствующих фильтрам")
    limit: int = Field(description="Максимальное количество записей в ответе")
    offset: int = Field(description="Смещение от начала выборки")
    items: List[FbsShipmentListItem] = Field(description="Список записей журнала отгрузок")


class FbsShipmentDetailResponse(BaseModel):
    """Полные детали одной отгрузки — включает raw_message и позиции"""

    shipment_id: int = Field(description="Внутренний ID записи журнала")
    received_at: datetime = Field(description="Дата и время получения сообщения из RabbitMQ")
    raw_message: Any = Field(description="Исходный JSON из RabbitMQ (для отладки и анализа)")
    total_items: int = Field(description="Общее количество позиций в отгрузке")
    status: str = Field(
        description="Статус обработки: processing, completed, partially_completed, failed, validation_failed"
    )
    source: FbsShipmentSource = Field(description="Источник FBS-отгрузки")
    error_message: Optional[str] = Field(
        None, description="Текст ошибки (для validation_failed — текст исключения Pydantic)"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Дата завершения обработки всех позиций"
    )
    items: List[FbsShipmentItemResponse] = Field(
        default=[], description="Позиции отгрузки (пустой список для validation_failed)"
    )


class FbsShipmentStatsResponse(BaseModel):
    """Сводная статистика отгрузок по статусам"""

    total: int = Field(description="Общее количество записей в журнале")
    by_status: Dict[str, int] = Field(description="Количество записей по каждому статусу")


class RetryRequest(BaseModel):
    """Запрос на переобработку отгрузок"""

    shipment_ids: List[int] = Field(
        default=[],
        description="Список ID для переобработки. Пустой список = переобработать все записи со статусом validation_failed",
    )


class RetryResultItem(BaseModel):
    """Результат переобработки одной отгрузки"""

    shipment_id: int = Field(description="ID записи журнала")
    status: str = Field(description="Новый статус после переобработки")
    error: Optional[str] = Field(None, description="Текст ошибки, если переобработка не удалась")


class RetryResponse(BaseModel):
    """Итог массовой переобработки отгрузок"""

    total_requested: int = Field(description="Количество записей, найденных для переобработки")
    processed: int = Field(description="Количество успешно обработанных записей")
    still_failed: int = Field(
        description="Количество записей, оставшихся со статусом validation_failed"
    )
    results: List[RetryResultItem] = Field(description="Детальный результат по каждой записи")
