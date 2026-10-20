"""Схемы явной активации зарегистрированных КИЗ."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonBlankString = Annotated[str, StringConstraints(min_length=1)]


class KizActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kiz_codes: list[NonBlankString] = Field(
        ...,
        min_length=1,
        description="Коды существующих КИЗ без дубликатов и пробелов по краям. Товар определяется по КИЗ.",
        examples=[["B21-KIZ-1", "B21-KIZ-2"]],
    )
    location_code: NonBlankString = Field(
        description="Код активной локации с россыпью. Остатки дочерних адресов, партий и контейнеров не учитываются.",
        examples=["PUSHKINO-ПРИЁМКА"],
    )
    user_name: NonBlankString = Field(
        description="Имя оператора, подтверждающего сканирование; записывается в журнал операции.",
        examples=["operator.pushkino"],
    )
    idempotency_key: NonBlankString = Field(
        description="Ключ повторного запроса. Повтор с теми же кодами, локацией и оператором возвращает сохранённый результат. Порядок кодов не учитывается.",
        examples=["kiz-activation-20261019-001"],
    )

    @field_validator("kiz_codes")
    @classmethod
    def validate_kiz_codes(cls, value: list[str]) -> list[str]:
        if any(code != code.strip() for code in value):
            raise ValueError("kiz_codes не должны содержать пробелы по краям")
        if len(value) != len(set(value)):
            raise ValueError("kiz_codes не должны содержать дубликаты")
        return value

    @field_validator("location_code", "user_name", "idempotency_key")
    @classmethod
    def validate_trimmed_non_blank(cls, value: str) -> str:
        if value != value.strip() or not value.strip():
            raise ValueError("значение должно быть непустым и без пробелов по краям")
        return value


class KizActivationItem(BaseModel):
    kiz_code: str = Field(description="Код активированного или уже активного КИЗ.")
    product_id: str = Field(description="Идентификатор товара, которому принадлежит КИЗ.")
    lifecycle_status: str = Field(
        description="Состояние КИЗ в результате операции: active — физически идентифицирован."
    )
    location_code: str = Field(description="Код локации хранения КИЗ в результате операции.")


class KizActivationResponse(BaseModel):
    operation_id: int = Field(
        description="Идентификатор операции активации; сохраняется при повторе с тем же ключом."
    )
    location_code: str = Field(description="Код локации, указанной при активации.")
    activated_count: int = Field(
        description="Количество КИЗ, переведённых из registered в active этой операцией."
    )
    already_active_count: int = Field(
        description="Количество КИЗ, уже активных в этой локации; новые события для них не создаются."
    )
    items: list[KizActivationItem] = Field(
        description="Результаты по всем переданным КИЗ. При повторе с тем же ключом возвращается сохранённое состояние на момент операции."
    )
