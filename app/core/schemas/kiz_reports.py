"""Контракты аналитики реестра КИЗ без изменений физического остатка."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.json_validation import validate_jsonb_value
from app.core.schemas.kiz import Lifecycle

KizGroupDimension = Literal[
    "organization",
    "product_id",
    "lifecycle_status",
    "location_code",
    "container_id",
    "container_qr_code",
    "origin_type",
]


class KizSummaryFilters(BaseModel):
    organization: str | None = Field(
        None, min_length=1, description="Точное значение organization; регистр значим."
    )
    organization_missing: bool | None = Field(
        None,
        description="true — только organization=NULL; false — только заполненная организация; без параметра — все. Несовместим с organization при true.",
    )
    product_id: str | None = Field(None, min_length=1, description="Точный идентификатор товара.")
    lifecycle_status: Lifecycle | None = Field(
        None, description="Текущее состояние КИЗ; без параметра учитываются все состояния."
    )
    location_code: str | None = Field(
        None,
        min_length=1,
        description="Точный текущий адрес holder: прямая локация россыпи либо текущая локация контейнера. Без дочерних адресов и исторических мест хранения.",
    )
    container_id: int | None = Field(
        None, gt=0, description="Идентификатор текущего контейнера КИЗ."
    )
    container_qr_code: str | None = Field(
        None, min_length=1, description="Точный QR текущего контейнера КИЗ."
    )
    origin_type: Literal["warehouse_assignment", "receipt_import"] | None = Field(
        None, description="Способ создания identity: ручное назначение либо импорт поступления."
    )
    origin_reference: str | None = Field(
        None,
        min_length=1,
        description="Точное значение origin_reference; у receipt_import это order_guid поступления.",
    )

    @field_validator(
        "organization", "product_id", "location_code", "container_qr_code", "origin_reference"
    )
    @classmethod
    def valid_database_text(cls, value):
        return validate_jsonb_value(value)

    @model_validator(mode="after")
    def validate_organization_scope(self):
        if self.organization is not None and self.organization_missing is True:
            raise ValueError("organization и organization_missing=true несовместимы")
        return self


class KizSummaryQuery(KizSummaryFilters):
    model_config = ConfigDict(extra="forbid")

    group_by: list[KizGroupDimension] = Field(
        default_factory=lambda: ["organization", "product_id"],
        min_length=1,
        max_length=7,
        description="Измерения группировки; по умолчанию organization,product_id. Можно повторять параметр либо перечислить значения через запятую. Измерения не должны дублироваться.",
    )
    limit: int = Field(
        50, ge=1, le=200, description="Количество групп на странице: 1–200, по умолчанию 50."
    )
    offset: int = Field(0, ge=0, description="Количество пропускаемых групп; по умолчанию 0.")

    @field_validator("group_by", mode="before")
    @classmethod
    def split_dimensions(cls, value):
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return [dimension.strip() for item in value for dimension in item.split(",")]
        return value

    @field_validator("group_by")
    @classmethod
    def unique_dimensions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("group_by содержит повторные измерения")
        return value


class KizSummaryCounts(BaseModel):
    total_count: int = Field(
        ge=0, description="Все identity реестра после фильтрации, включая закрытые и отгруженные."
    )
    registered_count: int = Field(
        ge=0,
        description="Зарегистрированные, но не активированные КИЗ; физическое место не подтверждено, остаток не резервируется.",
    )
    active_count: int = Field(
        ge=0,
        description="Активные идентифицированные единицы организации; не общий количественный остаток inventory.",
    )
    active_loose_count: int = Field(
        ge=0, description="Active КИЗ с прямой локацией и без контейнера."
    )
    active_container_count: int = Field(
        ge=0, description="Active КИЗ с контейнерным holder и без прямой локации."
    )
    shipped_count: int = Field(ge=0, description="Отгруженные КИЗ; не входят в active.")
    error_count: int = Field(ge=0, description="КИЗ с закрытой ошибочной идентификацией.")
    deactivated_count: int = Field(ge=0, description="Деактивированные КИЗ; не входят в active.")


class KizSummaryGroup(BaseModel):
    organization: str | None = Field(
        None,
        description="Организация; null — не указана. Поле возвращается только при соответствующей группировке.",
    )
    product_id: str | None = Field(
        None, description="Товар группы; поле возвращается только при группировке по товару."
    )
    lifecycle_status: Lifecycle | None = Field(
        None,
        description="Lifecycle группы; поле возвращается только при соответствующей группировке.",
    )
    location_code: str | None = Field(
        None,
        description="Текущий holder location_code, включая адрес контейнера; null — адрес отсутствует. Только при соответствующей группировке.",
    )
    container_id: int | None = Field(
        None,
        description="Текущий контейнер; null — нет контейнера. Только при соответствующей группировке.",
    )
    container_qr_code: str | None = Field(
        None,
        description="QR текущего контейнера; null — нет контейнера. Только при соответствующей группировке.",
    )
    origin_type: Literal["warehouse_assignment", "receipt_import"] | None = Field(
        None, description="Происхождение группы; только при соответствующей группировке."
    )


class KizSummaryItem(BaseModel):
    group: KizSummaryGroup = Field(
        description="Значения только запрошенных измерений. Выбранные измерения с NULL сохраняются в ответе."
    )
    counts: KizSummaryCounts = Field(
        description="Счётчики identity текущей группы после фильтрации."
    )


class KizSummaryResponse(BaseModel):
    group_by: list[KizGroupDimension] = Field(
        description="Применённые измерения в порядке группировки и сортировки."
    )
    filters: KizSummaryFilters = Field(
        description="Применённые фильтры; null означает отсутствие фильтра."
    )
    total_groups: int = Field(
        ge=0, description="Количество всех групп после фильтрации, независимо от limit/offset."
    )
    limit: int = Field(description="Применённый размер страницы групп.")
    offset: int = Field(description="Применённое смещение групп.")
    totals: KizSummaryCounts = Field(
        description="Итоги по всему отфильтрованному реестру, а не только странице."
    )
    items: list[KizSummaryItem] = Field(
        description="Страница групп; сортировка по group_by по возрастанию, NULL перед заполненными значениями."
    )
