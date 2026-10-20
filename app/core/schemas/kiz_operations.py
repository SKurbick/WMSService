"""HTTP contracts for explicit KIZ-aware physical operations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.core.json_validation import validate_jsonb_value

Text100 = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Text200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ProductId = Annotated[str, StringConstraints(min_length=1, max_length=50)]


def _text(value: str) -> str:
    validate_jsonb_value(value)
    if value != value.strip():
        raise ValueError("Поле не должно содержать окружающие пробелы")
    return value


class KizTransferItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_line_id: Text200 = Field(description="Стабильный идентификатор строки внешней операции.")
    product_id: ProductId = Field(description="Идентификатор товара/SKU.")
    from_location_code: Text100 = Field(description="Код точной исходной локации.")
    to_location_code: Text100 = Field(description="Код точной целевой локации.")
    quantity: int = Field(
        strict=True,
        gt=0,
        le=99_999_999,
        description=(
            "Полное количество штучных единиц в строке. Передаётся целым JSON-числом; "
            "строки, дробные значения и float не принимаются."
        ),
        examples=[5],
    )
    kiz_codes: list[Text200] = Field(default_factory=list,
                                     description="Явно выбранные active КИЗ; порядок незначим.")

    _validate_text = field_validator(
        "external_line_id", "product_id", "from_location_code", "to_location_code"
    )(_text)

    @field_validator("kiz_codes")
    @classmethod
    def unique_kiz(cls, values):
        for value in values:
            _text(value)
        if len(values) != len(set(values)):
            raise ValueError("duplicate KIZ внутри item запрещён")
        return values

    @model_validator(mode="after")
    def validate_line(self):
        if self.from_location_code == self.to_location_code:
            raise ValueError("source и destination должны различаться")
        if len(self.kiz_codes) > self.quantity:
            raise ValueError("quantity не может быть меньше числа selected KIZ")
        return self


class KizTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100 = Field(description="Стабильное имя внешней системы-источника.")
    external_operation_id: Text200 = Field(description="Идемпотентный идентификатор внешней команды.")
    author: Text100 = Field(description="Автор физической операции.")
    items: list[KizTransferItem] = Field(min_length=1, description="Непустой набор атомарных строк transfer.")

    _validate_text = field_validator(
        "source_system", "external_operation_id", "author"
    )(_text)

    @model_validator(mode="after")
    def validate_operation(self):
        line_ids = [item.external_line_id for item in self.items]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("duplicate external_line_id запрещён")
        kiz_codes = [code for item in self.items for code in item.kiz_codes]
        if len(kiz_codes) != len(set(kiz_codes)):
            raise ValueError("один КИЗ нельзя выбрать в нескольких items")
        return self


class KizTransferResultItem(BaseModel):
    external_line_id: str = Field(description="Идентификатор выполненной строки.")
    product_id: str = Field(description="Перемещённый товар/SKU.")
    from_location_code: str = Field(description="Исходная точная локация.")
    to_location_code: str = Field(description="Целевая точная локация.")
    quantity: int = Field(description="Перемещённое количество штучных единиц.", examples=[5])
    kiz_codes: list[str] = Field(description="Перемещённые explicit КИЗ.")
    movement_ref: int = Field(description="Стабильная ссылка на созданный physical movement.")


class KizTransferResponse(BaseModel):
    operation_id: int = Field(description="Внутренний идентификатор KIZ operation.")
    operation_type: Literal["transfer"] = Field(description="Тип операции; в Phase 4 только transfer.")
    source_system: str = Field(description="Система-источник идемпотентной команды.")
    external_operation_id: str = Field(description="Внешний идентификатор команды.")
    items: list[KizTransferResultItem] = Field(description="Сохранённый результат строк с movement_ref.")


class KizShipItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_line_id: Text200 = Field(description="Стабильный идентификатор строки внешней операции.")
    product_id: ProductId = Field(description="Идентификатор товара/SKU.")
    from_location_code: Text100 = Field(description="Код точной исходной локации.")
    quantity: int = Field(
        strict=True, gt=0, le=99_999_999, examples=[5],
        description=(
            "Полное количество штучных единиц для списания. Передаётся целым "
            "JSON-числом; строки, дробные значения и float не принимаются."
        ),
    )
    kiz_codes: list[Text200] = Field(
        default_factory=list,
        description="Явно отгружаемые active КИЗ; порядок незначим.",
    )

    _validate_text = field_validator(
        "external_line_id", "product_id", "from_location_code"
    )(_text)

    @field_validator("kiz_codes")
    @classmethod
    def unique_kiz(cls, values):
        for value in values:
            _text(value)
        if len(values) != len(set(values)):
            raise ValueError("duplicate KIZ внутри item запрещён")
        return values

    @model_validator(mode="after")
    def validate_line(self):
        if len(self.kiz_codes) > self.quantity:
            raise ValueError("quantity не может быть меньше числа selected KIZ")
        return self


class KizShipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Text100 = Field(description="Стабильное имя внешней системы-источника.")
    external_operation_id: Text200 = Field(description="Идемпотентный идентификатор внешней команды.")
    author: Text100 = Field(description="Автор физической операции.")
    items: list[KizShipItem] = Field(min_length=1, description="Непустой атомарный набор строк shipment.")

    _validate_text = field_validator(
        "source_system", "external_operation_id", "author"
    )(_text)

    @model_validator(mode="after")
    def validate_operation(self):
        line_ids = [item.external_line_id for item in self.items]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("duplicate external_line_id запрещён")
        kiz_codes = [code for item in self.items for code in item.kiz_codes]
        if len(kiz_codes) != len(set(kiz_codes)):
            raise ValueError("один КИЗ нельзя выбрать в нескольких items")
        return self


class KizShipResultItem(BaseModel):
    external_line_id: str = Field(description="Идентификатор выполненной строки.")
    product_id: str = Field(description="Списанный товар/SKU.")
    from_location_code: str = Field(description="Исходная точная локация.")
    quantity: int = Field(description="Списанное количество штучных единиц.", examples=[5])
    kiz_codes: list[str] = Field(description="Отгруженные explicit КИЗ.")
    movement_ref: int = Field(description="Стабильная ссылка на outgoing physical movement.")


class KizShipResponse(BaseModel):
    operation_id: int = Field(description="Внутренний идентификатор KIZ operation.")
    operation_type: Literal["ship"] = Field(description="Тип операции; для этого endpoint всегда ship.")
    source_system: str = Field(description="Система-источник идемпотентной команды.")
    external_operation_id: str = Field(description="Внешний идентификатор команды.")
    items: list[KizShipResultItem] = Field(description="Сохранённый результат строк с movement_ref.")
