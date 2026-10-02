"""Schemas for explicit registered KIZ activation."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonBlankString = Annotated[str, StringConstraints(min_length=1)]


class KizActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kiz_codes: list[NonBlankString] = Field(..., min_length=1)
    location_code: NonBlankString
    user_name: NonBlankString
    idempotency_key: NonBlankString

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
    kiz_code: str
    product_id: str
    lifecycle_status: str
    location_code: str


class KizActivationResponse(BaseModel):
    operation_id: int
    location_code: str
    activated_count: int
    already_active_count: int
    items: list[KizActivationItem]
