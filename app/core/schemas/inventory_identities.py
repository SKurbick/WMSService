"""Explicit identity observations; quantities always exclude scanned identified units."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core.kiz_code import KizCode
from app.core.schemas.inventory_documents import Command, PositiveBigint, Input, Sku
from decimal import Decimal


class IdentityAccounting(Command):
    containers: bool = False


class ContainerCount(Input):
    product_id: Sku
    quantity: Decimal = Field(ge=0, max_digits=10, decimal_places=2)

    @field_validator("quantity", mode="before")
    @classmethod
    def no_float(cls, value):
        if isinstance(value, float):
            raise ValueError("Use a decimal string")
        return value


class IdentityScan(Command):
    kind: Literal["kiz", "container"]
    kiz_code: KizCode | None = None
    container_qr: str | None = Field(default=None, min_length=1, max_length=100)
    location_id: PositiveBigint
    holder_qr: str | None = Field(default=None, min_length=1, max_length=100)
    contents: list[ContainerCount] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "kiz":
            if self.kiz_code is None or self.container_qr is not None or self.contents:
                raise ValueError("KIZ scan requires kiz_code and no container contents")
        elif self.container_qr is None or self.kiz_code is not None or self.holder_qr:
            raise ValueError("Container scan requires container_qr")
        ids = [r.product_id for r in self.contents]
        if len(set(ids)) != len(ids):
            raise ValueError("Repeated product in container count")
        return self
