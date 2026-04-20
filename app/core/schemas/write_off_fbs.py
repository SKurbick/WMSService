"""Схемы для списания из ФБС зоны"""

import datetime
from typing import List, Optional
from pydantic import BaseModel, model_validator


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

    @model_validator(mode='after')
    def validate_quantity_equals_tasks_count(self):
        if self.quantity != len(self.assembly_tasks):
            raise ValueError(
                f'quantity ({self.quantity}) должно быть равно '
                f'количеству assembly_tasks ({len(self.assembly_tasks)})'
            )
        return self
