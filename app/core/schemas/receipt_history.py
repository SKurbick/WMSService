"""Typed read models истории документа поступления."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class DecimalJsonModel(BaseModel):
    @field_serializer(
        "quantity",
        "amount_with_vat",
        "amount_without_vat",
        "planned_cost",
        "pack_count",
        "pack_multiplicity",
        when_used="json",
        check_fields=False,
    )
    def serialize_decimal(self, value: Decimal | None) -> int | float | None:
        if value is None:
            return None
        return int(value) if value == value.to_integral_value() else float(value)


class ReceiptSnapshotItem(DecimalJsonModel):
    receipt_item_id: int = Field(description="PK текущей WMS snapshot-строки.")
    product_id: str = Field(description="Идентификатор товара.")
    product_name: str | None = Field(
        default=None, description="Название из public.products либо null."
    )
    quantity: Decimal = Field(description="Текущее количество товара в snapshot.")
    created_at: datetime = Field(description="Timezone-aware время создания WMS-строки.")
    updated_at: datetime = Field(
        description="Timezone-aware время последнего обновления WMS-строки."
    )


class ReceiptSnapshot(BaseModel):
    revision_at: datetime | None = Field(
        default=None,
        description="Приоритетная документная дата наиболее поздно обновлённой snapshot-строки.",
    )
    event_status: str | None = None
    document_created_at: datetime | None = None
    supply_date: datetime | None = None
    update_document_datetime: datetime | None = None
    supplier_name: str | None = None
    supplier_code: str | None = None
    author_of_the_change: str | None = None
    our_organizations_name: str | None = None
    order_guid: str | None = None
    currency: str | None = None
    items: list[ReceiptSnapshotItem] = Field(
        description="Все текущие WMS-строки документа, без исторических revisions."
    )


class ReceiptRevisionItem(DecimalJsonModel):
    legacy_row_id: int = Field(description="Фактический id source row legacy-таблицы.")
    product_id: str = Field(description="local_vendor_code исходной строки.")
    product_name: str | None = Field(
        default=None, description="Название товара из products либо legacy source."
    )
    quantity: Decimal = Field(description="Количество исходной legacy-строки.")
    amount_with_vat: Decimal | None = Field(default=None, description="Сумма строки с НДС.")
    amount_without_vat: Decimal | None = Field(default=None, description="Сумма строки без НДС.")
    planned_cost: Decimal | None = Field(
        default=None, description="Плановая стоимость legacy-строки."
    )
    pack_count: Decimal | None = Field(
        default=None, description="Количество упаковок из source row."
    )
    pack_multiplicity: Decimal | None = Field(
        default=None, description="Кратность упаковки из source row."
    )
    is_valid: bool | None = Field(
        default=None,
        description="Legacy-признак актуальности строки; true хотя бы у одной строки делает revision текущей.",
    )


class ReceiptRevision(BaseModel):
    revision_id: str = Field(
        description="receipt_revision:<UTC epoch us> либо receipt_revision:legacy:<row id>."
    )
    revision_at: datetime | None = Field(
        default=None,
        description="Точный timezone-aware момент revision; null для fallback legacy revision.",
    )
    is_current: bool = Field(
        description="True, если внутри revision есть хотя бы одна строка is_valid=true."
    )
    document_number: str | None = None
    document_created_at: datetime | None = None
    supply_date: datetime | None = None
    update_document_datetime: datetime | None = None
    event_status: str | None = None
    supplier_name: str | None = None
    supplier_code: str | None = None
    author_of_the_change: str | None = None
    our_organizations_name: str | None = None
    order_guid: str | None = None
    currency: str | None = None
    invoice_number: str | None = None
    transport_number: str | None = None
    items: list[ReceiptRevisionItem] = Field(
        description="Source rows revision; одинаковые product_id не агрегируются."
    )


class ReceiptHistoryResponse(BaseModel):
    guid: str = Field(description="Точный строковый ID документа; не обязан быть UUID.")
    document_number: str | None = Field(
        default=None, description="Номер из latest snapshot header либо первой revision страницы."
    )
    total_revisions: int = Field(description="Число legacy revisions до пагинации.")
    limit: int = Field(description="Количество revisions на странице.")
    offset: int = Field(description="Смещение по revisions.")
    current_snapshot: ReceiptSnapshot | None = Field(
        default=None,
        description="Текущее состояние из wms.receipt_items; не является historical revision и может быть null.",
    )
    revisions: list[ReceiptRevision] = Field(
        description="Страница legacy revisions в порядке current/date/id."
    )


class ReceiptHistoryListItem(DecimalJsonModel):
    row_id: str = Field(description="Глобально стабильный base64url-ключ строки списка.")
    source_type: Literal["legacy_revision", "wms_snapshot_only"]
    guid: str = Field(description="GUID для перехода в GET /api/receipts/{guid}/history.")
    revision_id: str | None = Field(
        default=None, description="ID legacy revision; null для WMS-only snapshot."
    )
    revision_at: datetime | None = Field(
        default=None, description="Время revision/snapshot; null только у undated legacy."
    )
    is_current: bool = Field(description="Legacy bool_or(is_valid); у WMS-only всегда true.")
    has_current_snapshot: bool
    snapshot_updated_at: datetime | None = None
    document_number: str | None = None
    document_created_at: datetime | None = None
    supply_date: datetime | None = None
    update_document_datetime: datetime | None = None
    event_status: str | None = None
    supplier_name: str | None = None
    supplier_code: str | None = None
    author_of_the_change: str | None = None
    our_organizations_name: str | None = None
    order_guid: str | None = None
    currency: str | None = None
    invoice_number: str | None = None
    transport_number: str | None = None
    item_count: int = Field(description="Количество всех source rows revision/snapshot.")
    product_count: int = Field(description="Количество уникальных непустых SKU.")
    total_quantity: Decimal = Field(
        description="Сумма quantity всех строк, не только filter match."
    )

    @field_serializer("total_quantity", when_used="json")
    def serialize_total_quantity(self, value: Decimal) -> int | float:
        return int(value) if value == value.to_integral_value() else float(value)


class ReceiptHistoryListResponse(BaseModel):
    date_from: date
    date_to: date
    timezone: str = Field(default="Europe/Moscow")
    total: int = Field(description="Строк после фильтров до пагинации.")
    total_documents: int = Field(description="Уникальных GUID после фильтров.")
    limit: int
    offset: int
    items: list[ReceiptHistoryListItem]
