"""Схемы для списания из ФБС зоны"""

import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


class ShipmentOfGoodsUpdate(BaseModel):
    author: str
    supply_id: str
    product_id: str
    warehouse_id: int
    delivery_type: str
    wb_warehouse: Optional[str] = None
    account: str
    quantity: int
    shipment_date: Optional[datetime.datetime] = None
    product_reserves_id: Optional[int] = None


class WriteOffAccordingToFBS(ShipmentOfGoodsUpdate):
    assembly_tasks: List[str]

    @field_validator('quantity')
    def validate_quantity_equals_tasks_count(cls, v: int, info) -> int:
        assembly_tasks = info.data.get('assembly_tasks', [])
        if v != len(assembly_tasks):
            raise ValueError(
                f'quantity ({v}) должно быть равно количеству assembly_tasks ({len(assembly_tasks)})'
            )
        return v
