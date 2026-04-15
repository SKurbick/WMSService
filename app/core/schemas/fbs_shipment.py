"""Схемы для журнала отгрузок из ФБС зоны"""

from datetime import datetime
from typing import List, Optional

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


class FbsShipmentResponse(BaseModel):
    shipment_id: int
    received_at: datetime
    total_items: int
    status: str
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None
    items: List[FbsShipmentItemResponse] = []
