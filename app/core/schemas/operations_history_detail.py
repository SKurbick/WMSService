"""Typed schemas детальной карточки операции."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_serializer


class DecimalJsonModel(BaseModel):
    @field_serializer(
        "quantity",
        "quantity_per_kit",
        "total_quantity",
        when_used="json",
        check_fields=False,
    )
    def serialize_decimal(self, value: Decimal) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class OperationWarning(BaseModel):
    code: Literal["missing_movement_link", "ambiguous_movement_link"] = Field(
        description="Машиночитаемый код нефатальной проблемы связи."
    )
    message: str = Field(
        description="Человекочитаемое объяснение проблемы.", examples=["Movement 123 was not found"]
    )
    reference: str = Field(
        description="ID item или movement, к которому относится warning.", examples=["123"]
    )


class MovementDetail(DecimalJsonModel):
    event_id: str = Field(description="Составной movement:<movement_id>:<UTC epoch microseconds>.")
    movement_id: int = Field(
        description="Локальный ID movement; без created_at глобально недостаточен."
    )
    movement_type: str = Field(
        description="Фактическое строковое значение БД; неизвестные historical types не отбрасываются."
    )
    product_id: str = Field(description="SKU физического движения.")
    product_name: str | None = Field(default=None, description="Название товара либо null.")
    quantity: Decimal = Field(
        description="Положительная величина; направление задают from/to location."
    )
    from_location_id: int | None = None
    from_location_code: str | None = None
    to_location_id: int | None = None
    to_location_code: str | None = None
    batch_number: str | None = None
    container_code: str | None = None
    user_name: str | None = None
    reason: str | None = None
    source_type: str | None = None
    source_id: int | None = None
    source_item_id: int | None = None
    metadata: dict[str, Any] = Field(
        description="Фактический JSON metadata movement; пустой object при отсутствии."
    )
    created_at: datetime = Field(
        description="Timezone-aware timestamp, входящий в identity movement."
    )


class KitOperationHeader(DecimalJsonModel):
    operation_id: int
    operation_type: str
    kit_product_id: str
    kit_product_name: str | None = None
    quantity: Decimal
    operation_location_id: int | None = None
    location_id: int
    location_code: str | None = None
    author: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class KitOperationDetailItem(DecimalJsonModel):
    item_id: int
    role: str
    product_id: str
    product_name: str | None = None
    quantity_per_kit: Decimal
    total_quantity: Decimal
    movement_id: int | None = None
    movement_created_at: datetime | None = None
    movement_link_status: Literal["resolved", "missing", "ambiguous"] = Field(
        description="resolved — одна строка по ID+timestamp; missing — ни одной; ambiguous — несколько."
    )


class ReSortingOperationHeader(DecimalJsonModel):
    operation_id: int
    from_product_id: str
    from_product_name: str | None = None
    to_product_id: str
    to_product_name: str | None = None
    quantity: Decimal
    operation_location_id: int
    location_id: int
    location_code: str
    reason: str
    author: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class ReSortingOperationDetailItem(DecimalJsonModel):
    item_id: int
    role: str
    product_id: str
    product_name: str | None = None
    quantity: Decimal
    movement_id: int | None = None
    movement_created_at: datetime | None = None
    movement_link_status: Literal["resolved", "missing", "ambiguous"]


class FbsShipmentHeader(BaseModel):
    shipment_id: int
    source: str
    status: str
    received_at: datetime
    completed_at: datetime | None = None
    total_items: int
    error_message: str | None = None
    raw_message: Any = Field(
        description="Исходное FBS-сообщение как отдельное фактическое поле header."
    )


class FbsShipmentDetailItem(DecimalJsonModel):
    item_id: int
    product_id: str
    product_name: str | None = None
    quantity: Decimal
    author: str | None = None
    supply_id: str | None = None
    account: str | None = None
    assembly_tasks: Any = Field(
        description="Фактические assembly_tasks FBS item без преобразования бизнес-семантики."
    )
    warehouse_id: int | None = None
    delivery_type: str | None = None
    wb_warehouse: str | None = None
    shipment_date: date | None = None
    status: str
    error_message: str | None = None
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None = None
    movement_id: int | None = None
    movement_link_status: Literal["not_linked", "resolved", "missing", "ambiguous"] = Field(
        description="not_linked — ID отсутствует; resolved — один candidate; missing — 0; ambiguous — несколько."
    )
    movement_event_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MovementHeader(MovementDetail):
    pass


class OperationDetailBase(BaseModel):
    event_id: str = Field(description="Детерминированный ID открытого события.")
    source_type: str = Field(description="Discriminator source-specific response.")
    operation_type: str = Field(description="Нормализованный тип операции.")
    operation_name: str = Field(description="Отображаемое имя типа операции.")
    status: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    author: str | None = None
    movements: list[MovementDetail] = Field(
        description="Физические movement-строки, структурно связанные с операцией."
    )
    warnings: list[OperationWarning] = Field(
        description="Нефатальные missing/ambiguous проблемы movement links; не меняют HTTP 200."
    )


class KitOperationDetailResponse(OperationDetailBase):
    source_type: Literal["kit_operation"]
    header: KitOperationHeader = Field(description="Фактический header kit operation.")
    items: list[KitOperationDetailItem] = Field(
        description="Компоненты и результат комплектации/разукомплектации."
    )


class ReSortingOperationDetailResponse(OperationDetailBase):
    source_type: Literal["re_sorting_operation"]
    header: ReSortingOperationHeader = Field(description="Фактический header пересортицы.")
    items: list[ReSortingOperationDetailItem] = Field(
        description="Исходящая и входящая роли пересортицы."
    )


class FbsShipmentDetailResponse(OperationDetailBase):
    source_type: Literal["fbs_shipment"]
    header: FbsShipmentHeader = Field(
        description="Фактический FBS shipment header, включая raw_message."
    )
    items: list[FbsShipmentDetailItem] = Field(
        description="Все FBS shipment items и status movement link."
    )


class MovementDetailResponse(OperationDetailBase):
    source_type: Literal["movement"]
    header: MovementHeader = Field(description="Фактическая standalone movement-строка.")
    items: list[MovementDetail] = Field(description="Всегда пустой список для standalone movement.")


OperationDetailResponse = Annotated[
    KitOperationDetailResponse
    | ReSortingOperationDetailResponse
    | FbsShipmentDetailResponse
    | MovementDetailResponse,
    Field(discriminator="source_type"),
]
