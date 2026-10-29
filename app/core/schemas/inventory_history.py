"""Схемы дневной истории остатков."""

from datetime import date as Date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_serializer


class DailyBalanceDay(BaseModel):
    date: Date = Field(description="Московский календарный день.", examples=["2026-07-01"])
    opening_quantity: Decimal = Field(
        description="Остаток на начало московского дня.", examples=[10]
    )
    incoming_quantity: Decimal = Field(
        description="Сумма входящих сторон movements за день.", examples=[5]
    )
    outgoing_quantity: Decimal = Field(
        description="Сумма исходящих сторон movements за день.", examples=[2]
    )
    closing_quantity: Decimal = Field(
        description="Остаток после всех movements дня: opening + incoming - outgoing.",
        examples=[13],
    )

    @field_serializer(
        "opening_quantity",
        "incoming_quantity",
        "outgoing_quantity",
        "closing_quantity",
        when_used="json",
    )
    def serialize_quantity(self, value: Decimal) -> int | float:
        """JSON не имеет Decimal: целые отдаём int, дробные — JSON number."""
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class DailyBalanceProduct(BaseModel):
    product_id: str = Field(description="Точный идентификатор товара/SKU.", examples=["wild1825"])
    product_name: str | None = Field(
        default=None,
        description="Название из public.products; null, если карточка отсутствует.",
        examples=["Товар"],
    )
    days: list[DailyBalanceDay] = Field(
        description="Полный календарный диапазон дней, включая дни без операций."
    )


class DailyBalancesResponse(BaseModel):
    date_from: Date = Field(
        description="Первый включённый календарный день.", examples=["2026-07-01"]
    )
    date_to: Date = Field(
        description="Последний включённый календарный день.", examples=["2026-07-03"]
    )
    timezone: str = Field(default="Europe/Moscow", description="Timezone расчёта границ и дней.")
    location_id: int | None = Field(
        default=None, description="Применённый location filter; null для всего WMS."
    )
    include_subtree: bool = Field(
        default=False, description="Включались ли потомки выбранной location."
    )
    total_products: int = Field(
        description="Число подходящих товаров до limit/offset.", examples=[1]
    )
    limit: int = Field(description="Размер страницы товаров.", examples=[100])
    offset: int = Field(description="Смещение по товарам.", examples=[0])
    items: list[DailyBalanceProduct] = Field(
        description="Товары страницы с полным диапазоном дней."
    )


class TurnoverQuantities(BaseModel):
    received_quantity: Decimal = Field(
        description="Поступило по receive за этот период, суммарно по всему WMS. JSON-число.",
        examples=[100.5],
    )
    shipped_quantity: Decimal = Field(
        description="Отгружено по ship за этот период, суммарно по всему WMS. JSON-число.",
        examples=[20],
    )

    @field_serializer("received_quantity", "shipped_quantity", when_used="json")
    def serialize_quantity(self, value: Decimal) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class DailyTurnoverDay(TurnoverQuantities):
    date: Date = Field(description="Московский календарный день.", examples=["2026-07-01"])


class DailyTurnoverProduct(BaseModel):
    product_id: str = Field(description="Точный идентификатор товара/SKU.", examples=["wild1825"])
    product_name: str | None = Field(
        default=None, description="Название товара; null, если отсутствует."
    )
    totals: TurnoverQuantities = Field(description="Итоги товара за весь запрошенный период.")
    days: list[DailyTurnoverDay] = Field(description="Все дни периода, включая дни без операций.")


class DailyTurnoverResponse(BaseModel):
    date_from: Date = Field(description="Запрошенный первый включённый московский день.")
    date_to: Date = Field(description="Запрошенный последний включённый московский день.")
    timezone: str = Field(default="Europe/Moscow", description="Timezone календарных дней.")
    group_by: Literal["day"] = Field(
        default="day", description="Дневной формат: у товара массив days."
    )
    total_products: int = Field(description="Число возвращённых товаров с receive/ship за период.")
    items: list[DailyTurnoverProduct] = Field(
        description="Все подходящие товары, product_id ASC; без пагинации."
    )


class MonthlyTurnoverMonth(TurnoverQuantities):
    month: Date = Field(
        description="Первый день московского месяца; количества ограничены датами запроса.",
        examples=["2026-01-01"],
    )


class MonthlyTurnoverProduct(BaseModel):
    product_id: str = Field(description="Точный идентификатор товара/SKU.", examples=["wild1825"])
    product_name: str | None = Field(
        default=None, description="Название товара; null, если отсутствует."
    )
    totals: TurnoverQuantities = Field(description="Итоги товара за запрошенный период.")
    months: list[MonthlyTurnoverMonth] = Field(
        description="Все месяцы периода, включая нулевые; крайние месяцы ограничены датами запроса."
    )


class MonthlyTurnoverResponse(BaseModel):
    date_from: Date = Field(description="Запрошенный первый включённый московский день.")
    date_to: Date = Field(description="Запрошенный последний включённый московский день.")
    timezone: str = Field(default="Europe/Moscow", description="Timezone группировки по месяцам.")
    group_by: Literal["month"] = Field(
        default="month", description="Месячный формат: у товара массив months."
    )
    total_products: int = Field(description="Число возвращённых товаров; равно длине items.")
    items: list[MonthlyTurnoverProduct] = Field(
        description="Все подходящие товары, product_id ASC; без пагинации."
    )


TurnoverResponse = Annotated[
    DailyTurnoverResponse | MonthlyTurnoverResponse, Field(discriminator="group_by")
]
