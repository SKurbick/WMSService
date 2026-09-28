"""Схемы raw inbox и B2 business apply импорта КИЗ."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KizImportMessageListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: int
    received_at: datetime
    parse_status: str
    order_guid: str | None = None
    supply_number: str | None = None
    exchange_name: str | None = None
    routing_key: str | None = None
    wild_group_count: int
    mark_code_count: int
    business_status: str
    processed_at: datetime | None = None
    business_error_code: str | None = None
    business_error_message: str | None = None
    business_result: dict[str, Any] | None = None


class KizImportMessageDetail(KizImportMessageListItem):
    raw_body: str
    raw_payload: Any | None = None
    parse_error: str | None = None
    rabbit_message_id: str | None = None
    correlation_id: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class KizImportGroupResult(BaseModel):
    product_id: str
    receipt_quantity: Decimal
    requested_codes: int
    new_kiz: int
    existing_kiz: int
    terminal_existing: int
    physical_quantity: Decimal
    identified_quantity: int
    unidentified_quantity: Decimal


class KizImportProcessResult(BaseModel):
    message_id: int
    business_status: str
    order_guid: str
    supply_number: str | None = None
    new_kiz_count: int
    existing_kiz_count: int
    terminal_existing_count: int
    groups: list[KizImportGroupResult]


class KizReceiptCapacityViolation(BaseModel):
    order_guid: str
    product_id: str
    current_kiz_count: int
    receipt_quantity: Decimal


class KizOrphanReceiptOrigin(BaseModel):
    kiz_id: int
    kiz_code: str
    order_guid: str
    product_id: str
    lifecycle_status: str


class KizRegisteredHolderViolation(BaseModel):
    kiz_id: int
    kiz_code: str
    product_id: str
    location_id: int | None = None
    container_id: int | None = None


class KizRegisteredOriginViolation(BaseModel):
    kiz_id: int
    kiz_code: str
    product_id: str
    origin_type: str
    origin_reference: str | None = None


class KizInvalidMessageLink(BaseModel):
    message_id: int
    kiz_id: int
    message_order_guid: str | None = None
    origin_type: str
    origin_reference: str | None = None


class KizImportIntegrityResponse(BaseModel):
    is_valid: bool
    receipt_capacity_violations: list[KizReceiptCapacityViolation]
    orphan_receipt_kiz: list[KizOrphanReceiptOrigin]
    registered_holder_violations: list[KizRegisteredHolderViolation]
    registered_origin_violations: list[KizRegisteredOriginViolation]
    invalid_message_kiz_links: list[KizInvalidMessageLink]
