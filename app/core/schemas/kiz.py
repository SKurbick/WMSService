"""KIZ v1 contracts. Codes are opaque and case-sensitive."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.json_validation import validate_jsonb_value
from app.core.kiz_code import KizCode, KIZ_CODE_DESCRIPTION

Lifecycle = Literal["registered", "active", "error", "deactivated", "shipped"]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


def _validate_metadata(value):
    try:
        return validate_jsonb_value(value)
    except ValueError as exc:
        raise ValueError(
            "metadata не должна содержать NUL или некорректные Unicode-символы"
        ) from exc


class KizAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kiz_code: KizCode = Field(description=KIZ_CODE_DESCRIPTION)
    product_id: Annotated[str, StringConstraints(min_length=1, max_length=50)] = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_code: NonEmpty = Field(
        description="Код существующей точной локации. Дочерние адреса не учитываются."
    )
    author: Annotated[str, StringConstraints(min_length=1, max_length=100)] = Field(
        description="Автор операции: логин оператора или имя вызывающей системы. Не подтверждает права доступа."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Дополнительные сведения в виде JSON-объекта. При закрытии сохраняются в событии; метаданные карточки не заменяются.",
    )

    @field_validator("product_id", "location_code", "author")
    @classmethod
    def validate_text(cls, value):
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("Поле не должно быть пустым, содержать NUL или окружающие пробелы")
        return value

    @field_validator("kiz_code")
    @classmethod
    def validate_route_code(cls, value):
        if value == "stock-summary" or value.endswith("/events"):
            raise ValueError(
                "Код КИЗ конфликтует с маршрутом API: stock-summary и окончание /events запрещены"
            )
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("Код КИЗ не должен содержать сегменты пути . или ..")
        return value

    _metadata_jsonb = field_validator("metadata")(_validate_metadata)


class KizTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: Annotated[str, StringConstraints(min_length=1, max_length=100)] = Field(
        description="Автор операции: логин оператора или имя вызывающей системы. Не подтверждает права доступа."
    )
    reason: NonEmpty = Field(
        description="Причина закрытия КИЗ; для error/deactivated обязательна и не может состоять из пробелов."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Дополнительные сведения в виде JSON-объекта. При закрытии сохраняются в событии; метаданные карточки не заменяются.",
    )

    @field_validator("author", "reason")
    @classmethod
    def validate_text(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Поле не должно быть пустым или содержать NUL")
        return value

    _metadata_jsonb = field_validator("metadata")(_validate_metadata)


class KizState(BaseModel):
    kiz_id: int = Field(description="Внутренний идентификатор записи КИЗ.")
    kiz_code: str = Field(description=KIZ_CODE_DESCRIPTION)
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int | None = Field(
        description="Прямая локация хранения россыпи; null для registered, КИЗ в контейнере и отгруженного КИЗ (shipped)."
    )
    location_code: str | None = Field(
        description="Код прямой локации хранения россыпи; null для registered, КИЗ в контейнере и отгруженного КИЗ."
    )
    container_id: int | None = Field(
        default=None,
        description="Идентификатор контейнера, в котором хранится КИЗ; null для registered, при хранении россыпью или после отгрузки.",
    )
    container_qr_code: str | None = Field(
        default=None, description="QR-код контейнера хранения КИЗ."
    )
    container_location_id: int | None = Field(
        default=None, description="Идентификатор текущей локации контейнера хранения КИЗ."
    )
    container_location_code: str | None = Field(
        default=None, description="Код текущей локации контейнера хранения КИЗ."
    )
    lifecycle_status: Lifecycle = Field(
        description="Состояние: registered — код зарегистрирован без физического места хранения; active — идентифицированная единица; error/deactivated — закрытая идентификация; shipped — единица покинула склад."
    )
    origin_type: Literal["warehouse_assignment", "receipt_import"] = Field(
        description="Источник КИЗ: warehouse_assignment — назначение на остаток, receipt_import — регистрация из сообщения поступления."
    )
    origin_reference: str | None = Field(
        description="GUID поступления для receipt_import; при ручном назначении равна null."
    )
    organization: str | None = Field(
        default=None,
        description="Организация из сообщения поступления; null, если не передана или КИЗ создан до поддержки этого поля.",
    )
    assigned_at: datetime = Field(description="Дата и время назначения КИЗ с часовым поясом.")
    closed_at: datetime | None = Field(description="Дата и время закрытия; null для активного КИЗ.")
    created_at: datetime = Field(description="Дата и время создания записи с часовым поясом.")
    updated_at: datetime = Field(
        description="Дата и время последнего изменения записи с часовым поясом."
    )
    created_by: str = Field(description="Автор первоначального назначения КИЗ.")
    metadata: dict[str, Any] = Field(
        description="Дополнительные сведения в виде JSON-объекта. При закрытии сохраняются в событии; метаданные карточки не заменяются."
    )

    @field_validator("metadata", mode="before")
    @classmethod
    def decode_metadata(cls, value):
        return json.loads(value) if isinstance(value, str) else value


class KizStockSummary(BaseModel):
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int | None = Field(
        description="Прямая локация россыпи; null при запросе остатка конкретного контейнера."
    )
    location_code: str | None = Field(
        description="Код прямой loose-локации; null для container scope."
    )
    container_id: int | None = Field(
        default=None, description="ID контейнера — прямого holder КИЗ."
    )
    container_qr_code: str | None = Field(
        default=None, description="QR контейнера — прямого holder КИЗ."
    )
    container_location_id: int | None = Field(
        default=None, description="ID текущей локации holder-контейнера."
    )
    container_location_code: str | None = Field(
        default=None, description="Код текущей локации holder-контейнера."
    )
    physical_quantity: Decimal = Field(
        description="Физический доступный остаток без партии: россыпь на точном адресе либо содержимое выбранного контейнера. В JSON — десятичная строка."
    )
    identified_quantity: int = Field(
        description="Число active КИЗ товара в выбранной локации россыпи либо контейнере. Один КИЗ соответствует одной единице."
    )
    unidentified_quantity: Decimal = Field(
        description="physical_quantity минус identified_quantity. В JSON — десятичная строка; при нарушении учёта может быть отрицательной."
    )
    integrity_ok: bool = Field(
        description="true, если физическое количество не меньше числа активных КИЗ."
    )


class KizAssignmentResult(KizStockSummary):
    kiz: KizState = Field(description="Карточка назначенного КИЗ после успешной операции.")


class KizEvent(BaseModel):
    kiz_event_id: int = Field(description="Внутренний идентификатор неизменяемого события КИЗ.")
    kiz_id: int = Field(description="Внутренний идентификатор записи КИЗ.")
    event_type: Literal[
        "registered", "activated", "assigned", "marked_as_error", "deactivated", "shipped"
    ] = Field(
        description="Событие: registered — регистрация; activated — подтверждение сканирования; assigned — ручное назначение; marked_as_error/deactivated — закрытие; shipped — отгрузка."
    )
    from_status: Lifecycle | None = Field(
        description="Состояние до события; null для первоначального назначения."
    )
    to_status: Lifecycle = Field(
        description="Состояние после события: active, error, deactivated или shipped."
    )
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int | None = Field(
        description="Прямая локация КИЗ на момент события; null для события КИЗ внутри контейнера."
    )
    container_id: int | None = Field(default=None, description="Снимок прямого holder-контейнера.")
    movement_ref: int | None = Field(
        default=None,
        description="Стабильная ссылка на физическое движение; обязательна для shipped, null для назначения и закрытия без движения.",
    )
    author: str = Field(
        description="Автор операции: логин оператора или имя вызывающей системы. Не подтверждает права доступа."
    )
    reason: str | None = Field(
        description="Причина закрытия КИЗ; для error/deactivated обязательна и не может состоять из пробелов."
    )
    metadata: dict[str, Any] = Field(
        description="Дополнительные сведения в виде JSON-объекта. При закрытии сохраняются в событии; метаданные карточки не заменяются."
    )
    occurred_at: datetime = Field(description="Дата и время события с часовым поясом.")

    @field_validator("metadata", mode="before")
    @classmethod
    def decode_metadata(cls, value):
        return json.loads(value) if isinstance(value, str) else value


class KizPage(BaseModel):
    items: list[KizState] = Field(
        description="Записи текущей страницы; пустой массив, если по фильтрам и смещению ничего не найдено."
    )
    total: int = Field(description="Общее число записей по фильтрам до применения пагинации.")
    limit: int = Field(
        description="Максимальное число записей на странице: от 1 до 200, по умолчанию 50."
    )
    offset: int = Field(description="Число пропущенных записей, начиная с нуля.")


class KizEventPage(BaseModel):
    items: list[KizEvent] = Field(
        description="Записи текущей страницы; пустой массив, если по фильтрам и смещению ничего не найдено."
    )
    total: int = Field(description="Общее число записей по фильтрам до применения пагинации.")
    limit: int = Field(
        description="Максимальное число записей на странице: от 1 до 200, по умолчанию 50."
    )
    offset: int = Field(description="Число пропущенных записей, начиная с нуля.")


class KizIntegrityViolation(BaseModel):
    violation_type: str = Field(
        default="loose_quantity", description="Тип нарушения holder/physical целостности КИЗ."
    )
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int | None = Field(description="Прямая loose-локация, если применимо.")
    location_code: str | None = Field(description="Код прямой loose-локации, если применимо.")
    container_id: int | None = Field(
        default=None, description="ID контейнера — прямого holder КИЗ."
    )
    container_qr_code: str | None = Field(
        default=None, description="QR контейнера — прямого holder КИЗ."
    )
    physical_quantity: Decimal = Field(
        description="Физическое количество: только available, без партии и контейнера на точном адресе. В JSON — десятичная строка."
    )
    identified_quantity: int = Field(
        description="Число активных КИЗ на этом адресе для товара. Один активный КИЗ соответствует одной единице."
    )
    difference: Decimal = Field(
        description="identified_quantity минус physical_quantity: величина нарушения в единицах, в JSON — десятичная строка."
    )
    inventory_missing: bool = Field(
        description="true, если строка available-остатка без партии и контейнера отсутствует."
    )
