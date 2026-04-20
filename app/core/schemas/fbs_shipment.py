"""Схемы для журнала отгрузок из ФБС зоны"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class FbsShipmentItemResponse(BaseModel):
    item_id: int
    product_id: str
    quantity: int
    author: str
    supply_id: str
    account: str
    assembly_tasks: list
    status: str
    error_message: Optional[str] = None
    retry_count: int
    movement_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class FbsShipmentListItem(BaseModel):
    """Элемент списка shipments — без raw_message и items (тяжёлые поля)."""
    shipment_id: int
    received_at: datetime
    total_items: int
    status: str
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None


class FbsShipmentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[FbsShipmentListItem]


class FbsShipmentDetailResponse(BaseModel):
    """Полные детали одного shipment — включает raw_message и items."""
    shipment_id: int
    received_at: datetime
    raw_message: Any
    total_items: int
    status: str
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None
    items: List[FbsShipmentItemResponse] = []


class FbsShipmentStatsResponse(BaseModel):
    total: int
    by_status: Dict[str, int]


class RetryRequest(BaseModel):
    shipment_ids: List[int] = []  # пустой список = все validation_failed


class RetryResultItem(BaseModel):
    shipment_id: int
    status: str
    error: Optional[str] = None


class RetryResponse(BaseModel):
    total_requested: int
    processed: int
    still_failed: int
    results: List[RetryResultItem]
