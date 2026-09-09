"""KIZ v1 contracts. Codes are opaque and case-sensitive."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Lifecycle = Literal["active", "error", "deactivated"]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


def _validate_metadata(value):
    """Проверить строки, включая ключи и вложенные значения, до записи jsonb."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item or any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ValueError("metadata не должна содержать NUL или некорректные Unicode-символы")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


class KizAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kiz_code: NonEmpty = Field(
        description="Полный код КИЗ. Регистр значим; код не переиспользуется после закрытия. При назначении запрещены пустая строка, NUL, пробелы по краям, код stock-summary и окончание /events."
    )
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

    @field_validator("kiz_code", "product_id", "location_code", "author")
    @classmethod
    def validate_text(cls, value):
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("Поле не должно быть пустым, содержать NUL или окружающие пробелы")
        return value


    @field_validator("kiz_code")
    @classmethod
    def validate_route_code(cls, value):
        if value == "stock-summary" or value.endswith("/events"):
            raise ValueError("Код КИЗ конфликтует с маршрутом API: stock-summary и окончание /events запрещены")
        if "\n" in value:
            raise ValueError("Код КИЗ не должен содержать перевод строки")
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
    kiz_code: str = Field(
        description="Полный код КИЗ. Регистр значим; код не переиспользуется после закрытия. При назначении запрещены пустая строка, NUL и пробелы по краям."
    )
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int = Field(
        description="Внутренний идентификатор точной локации; дочерние адреса не учитываются."
    )
    location_code: str = Field(
        description="Код существующей точной локации. Дочерние адреса не учитываются."
    )
    lifecycle_status: Lifecycle = Field(
        description="Состояние: active — действующий; error — ошибочное назначение; deactivated — деактивирован. Закрытые состояния окончательные."
    )
    origin_type: Literal["warehouse_assignment"] = Field(
        description="Источник появления КИЗ. В первой версии всегда warehouse_assignment — назначение на существующий остаток."
    )
    origin_reference: str | None = Field(
        description="Дополнительная ссылка на источник. При назначении через API первой версии равна null."
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
    location_id: int = Field(
        description="Внутренний идентификатор точной локации; дочерние адреса не учитываются."
    )
    location_code: str = Field(
        description="Код существующей точной локации. Дочерние адреса не учитываются."
    )
    physical_quantity: Decimal = Field(
        description="Физическое количество: только available, без партии и контейнера на точном адресе. В JSON — десятичная строка."
    )
    identified_quantity: int = Field(
        description="Число активных КИЗ на этом адресе для товара. Один активный КИЗ соответствует одной единице."
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
    event_type: Literal["assigned", "marked_as_error", "deactivated"] = Field(
        description="Событие: assigned — назначен; marked_as_error — признан ошибочным; deactivated — деактивирован."
    )
    from_status: Lifecycle | None = Field(
        description="Состояние до события; null для первоначального назначения."
    )
    to_status: Lifecycle = Field(
        description="Состояние после события: active, error или deactivated."
    )
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int = Field(
        description="Внутренний идентификатор точной локации; дочерние адреса не учитываются."
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
    product_id: str = Field(
        description="Идентификатор существующего товара (SKU) из public.products.id."
    )
    location_id: int = Field(
        description="Внутренний идентификатор точной локации; дочерние адреса не учитываются."
    )
    location_code: str = Field(
        description="Код существующей точной локации. Дочерние адреса не учитываются."
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
