"""Схемы единого read-only списка бизнес-операций WMS."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer


class OperationHistoryItem(BaseModel):
    event_id: str = Field(
        description="Детерминированный ID: kit_operation:<id>, re_sorting:<id>, fbs_shipment:<id> или movement:<id>:<UTC epoch us>.",
        examples=["kit_operation:42"],
    )
    source_type: str = Field(
        description="Адаптер источника: kit_operation, re_sorting_operation, fbs_shipment или movement.",
        examples=["kit_operation"],
    )
    operation_type: str = Field(
        description="Нормализованный фактический тип операции.", examples=["kit_assembly"]
    )
    operation_name: str = Field(
        description="Русское имя известного типа; для неизвестного historical type равно operation_type.",
        examples=["Комплектация"],
    )
    status: str | None = Field(
        default=None,
        description="Фактический status business header; у standalone movement всегда null.",
        examples=["completed"],
    )
    created_at: datetime = Field(description="Timezone-aware время создания события.")
    completed_at: datetime | None = Field(
        default=None, description="Фактическое время завершения, если источник его хранит."
    )
    author: str | None = Field(
        default=None, description="Фактический автор; null при отсутствии или неоднозначности."
    )
    location_id: int | None = Field(
        default=None,
        description="Основная location; у movement с двумя сторонами и FBS может быть null.",
    )
    location_code: str | None = Field(
        default=None, description="Код основной location, если она однозначна."
    )
    product_count: int = Field(description="Количество различных товаров события.", examples=[3])
    total_quantity: Decimal = Field(
        description="Kit: комплекты; re-sorting: количество операции; FBS: сумма items; movement: quantity строки. Не является изменением общего остатка.",
        examples=[1],
    )
    external_reference: str | None = Field(
        default=None, description="Однозначная внешняя ссылка; сейчас supply_id для FBS либо null."
    )

    @field_serializer("total_quantity", when_used="json")
    def serialize_quantity(self, value: Decimal) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class OperationsHistoryResponse(BaseModel):
    date_from: date = Field(description="Первый включённый московский день.")
    date_to: date = Field(description="Последний включённый московский день.")
    timezone: str = Field(default="Europe/Moscow", description="Timezone календарного фильтра.")
    total: int = Field(description="Число событий после фильтров до пагинации.")
    limit: int = Field(description="Размер страницы после UNION всех источников.")
    offset: int = Field(description="Смещение после UNION всех источников.")
    items: list[OperationHistoryItem] = Field(
        description="События в порядке created_at DESC, event_id DESC."
    )
