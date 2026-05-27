"""Pydantic схемы для мягких резервов товара"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


class RabbitReservationOrder(BaseModel):
    order_id: int
    status: str
    created_at: Optional[datetime] = None


class RabbitReservationMessage(BaseModel):
    wild: str
    orders: list[RabbitReservationOrder]


class ProductAvailabilityResponse(BaseModel):
    product_id: str
    physical_qty: float = Field(default=0)
    reserved_qty: float = Field(default=0)
    free_qty: float = Field(default=0)
    shortage_qty: float = Field(default=0)

    class Config:
        from_attributes = True


class ProductAvailabilityTotalsResponse(BaseModel):
    physical_qty: float = Field(default=0)
    reserved_qty: float = Field(default=0)
    free_qty: float = Field(default=0)
    shortage_qty: float = Field(default=0)
    products_total: int = Field(default=0)
    products_with_shortage: int = Field(default=0)
    products_with_active_reserve: int = Field(default=0)

    class Config:
        from_attributes = True


class StockReservationOrderResponse(BaseModel):
    reservation_order_id: int
    source_type: str
    product_id: str
    external_order_id: int
    external_status: str
    is_reserved: bool
    reserved_qty: Decimal
    external_created_at: Optional[datetime] = None
    last_event_at: datetime
    raw_payload: Optional[Any] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StockReservationEventResponse(BaseModel):
    reservation_event_id: int
    source_type: str
    product_id: Optional[str] = None
    external_order_id: Optional[int] = None
    external_status: Optional[str] = None
    reserved_qty: Optional[Decimal] = None
    external_created_at: Optional[datetime] = None
    event_received_at: datetime
    processing_result: str
    error_message: Optional[str] = None
    raw_payload: Any

    class Config:
        from_attributes = True


class ReservationListQueryParams(BaseModel):
    product_id: Optional[str] = None
    external_order_id: Optional[int] = None
    is_reserved: Optional[bool] = None
    external_status: Optional[str] = None
    source_type: Optional[str] = None
    older_than_hours: Optional[int] = None
    limit: int = 100
    offset: int = 0


class ReservationEventsQueryParams(BaseModel):
    product_id: Optional[str] = None
    external_order_id: Optional[int] = None
    external_status: Optional[str] = None
    processing_result: Optional[str] = None
    source_type: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    limit: int = 100
    offset: int = 0
