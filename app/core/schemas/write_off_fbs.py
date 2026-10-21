"""Схемы для списания из ФБС зоны"""

import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.kiz_code import KizCode, KIZ_CODE_DESCRIPTION


class KizBinding(BaseModel):
    assembly_task: str
    kiz_code: KizCode = Field(description=KIZ_CODE_DESCRIPTION)

    @field_validator("assembly_task")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("значение не должно быть пустым")
        return normalized


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
    kiz_bindings: Optional[List[KizBinding]] = None

    @model_validator(mode="after")
    def validate_task_contract(self):
        if self.quantity != len(self.assembly_tasks):
            raise ValueError(
                f"quantity ({self.quantity}) должно быть равно "
                f"количеству assembly_tasks ({len(self.assembly_tasks)})"
            )
        if len(self.assembly_tasks) != len(set(self.assembly_tasks)):
            raise ValueError("assembly_tasks содержит дубли")

        bindings = self.kiz_bindings or []
        binding_tasks = [binding.assembly_task for binding in bindings]
        binding_codes = [binding.kiz_code for binding in bindings]
        unknown_tasks = sorted(set(binding_tasks) - set(self.assembly_tasks))
        if unknown_tasks:
            raise ValueError(
                "kiz_bindings содержит СЗ вне assembly_tasks: " + ", ".join(unknown_tasks)
            )
        if len(binding_tasks) != len(set(binding_tasks)):
            raise ValueError("kiz_bindings содержит повторную привязку СЗ")
        if len(binding_codes) != len(set(binding_codes)):
            raise ValueError("kiz_bindings содержит повторный kiz_code")
        if len(bindings) > self.quantity:
            raise ValueError("количество kiz_bindings превышает quantity")
        return self
