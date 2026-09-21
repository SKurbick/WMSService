"""HTTP contracts for explicit container physical operations."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.json_validation import validate_jsonb_value

Text100 = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Text200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ProductId = Annotated[str, StringConstraints(min_length=1, max_length=50)]


def _text(value: str) -> str:
    validate_jsonb_value(value)
    if value != value.strip():
        raise ValueError("Поле не должно содержать окружающие пробелы")
    return value


class ContainerFillItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_line_id: Text200
    product_id: ProductId
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    batch_number: str | None = Field(default=None, max_length=50)
    kiz_codes: list[Text200] = Field(default_factory=list)

    _validate_text = field_validator("external_line_id", "product_id")(_text)

    @field_validator("batch_number")
    @classmethod
    def validate_batch(cls, value):
        if value is not None:
            _text(value)
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def reject_float(cls, value):
        if isinstance(value, float):
            raise ValueError("quantity нельзя передавать как float; используйте decimal string")
        return value

    @model_validator(mode="after")
    def validate_kiz_codes(self):
        if len(self.kiz_codes) != len(set(self.kiz_codes)):
            raise ValueError("duplicate KIZ code внутри item запрещён")
        if Decimal(len(self.kiz_codes)) > self.quantity:
            raise ValueError("количество kiz_codes не может превышать quantity")
        if self.kiz_codes and self.batch_number is not None:
            raise ValueError("KIZ внутри container operation поддерживается только без batch")
        return self


class ContainerFillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100
    external_operation_id: Text200
    author: Text100
    container_id: int = Field(strict=True, gt=0)
    items: list[ContainerFillItem] = Field(min_length=1)

    _validate_text = field_validator("source_system", "external_operation_id", "author")(_text)

    @model_validator(mode="after")
    def validate_lines(self):
        line_ids = [item.external_line_id for item in self.items]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("duplicate external_line_id запрещён")
        kiz_codes = [code for item in self.items for code in item.kiz_codes]
        if len(kiz_codes) != len(set(kiz_codes)):
            raise ValueError("один KIZ не может участвовать в нескольких items")
        return self


class ContainerFillResultItem(BaseModel):
    external_line_id: str
    product_id: str
    batch_number: str | None
    quantity: Decimal
    movement_refs: list[int] = Field(min_length=2, max_length=2)
    kiz_codes: list[str] = Field(default_factory=list)


class ContainerFillResponse(BaseModel):
    operation_id: int
    operation_type: Literal["fill"]
    source_system: str
    external_operation_id: str
    container_id: int
    container_qr_code: str
    items: list[ContainerFillResultItem]


class ContainerExtractItem(ContainerFillItem):
    pass


class ContainerExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100
    external_operation_id: Text200
    author: Text100
    container_id: int = Field(strict=True, gt=0)
    items: list[ContainerExtractItem] = Field(min_length=1)

    _validate_text = field_validator("source_system", "external_operation_id", "author")(_text)

    @model_validator(mode="after")
    def validate_lines_and_scopes(self):
        line_ids = [item.external_line_id for item in self.items]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("duplicate external_line_id запрещён")
        scopes = [(item.product_id, item.batch_number) for item in self.items]
        if len(scopes) != len(set(scopes)):
            raise ValueError("duplicate product/batch scope запрещён для extract")
        kiz_codes = [code for item in self.items for code in item.kiz_codes]
        if len(kiz_codes) != len(set(kiz_codes)):
            raise ValueError("один KIZ не может участвовать в нескольких items")
        return self


class ContainerExtractResultItem(ContainerFillResultItem):
    pass


class ContainerExtractResponse(BaseModel):
    operation_id: int
    operation_type: Literal["extract"]
    source_system: str
    external_operation_id: str
    container_id: int
    container_qr_code: str
    container_status: Literal["empty", "open"]
    items: list[ContainerExtractResultItem]


class ContainerMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100
    external_operation_id: Text200
    author: Text100
    container_id: int = Field(strict=True, gt=0)
    to_location_code: Text100

    _validate_text = field_validator(
        "source_system", "external_operation_id", "author", "to_location_code"
    )(_text)


class ContainerMoveResultItem(BaseModel):
    product_id: str
    batch_number: str | None
    quantity: Decimal
    movement_refs: list[int] = Field(min_length=1, max_length=1)
    kiz_codes: list[str] = Field(default_factory=list)


class ContainerMoveResponse(BaseModel):
    operation_id: int
    operation_type: Literal["move"]
    source_system: str
    external_operation_id: str
    container_id: int
    container_qr_code: str
    container_status: Literal["empty", "open", "sealed"]
    from_location_code: str
    to_location_code: str
    items: list[ContainerMoveResultItem]


class ContainerUnpackAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100
    external_operation_id: Text200
    author: Text100
    container_id: int = Field(strict=True, gt=0)

    _validate_text = field_validator("source_system", "external_operation_id", "author")(_text)


class ContainerUnpackAllResultItem(BaseModel):
    product_id: str
    batch_number: str | None
    quantity: Decimal
    movement_refs: list[int] = Field(min_length=2, max_length=2)
    kiz_codes: list[str] = Field(default_factory=list)


class ContainerUnpackAllResponse(BaseModel):
    operation_id: int
    operation_type: Literal["unpack_all"]
    source_system: str
    external_operation_id: str
    container_id: int
    container_qr_code: str
    container_status: Literal["empty"]
    location_code: str
    items: list[ContainerUnpackAllResultItem]
