"""Read-only chronological history contract for one KIZ."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.schemas.kiz import KizState, Lifecycle


class KizTimelineEntry(BaseModel):
    event_type: str = Field(
        description=(
            "Тип складского события: lifecycle event либо тип связанного physical "
            "movement. Explicit shipped lifecycle event объединяется с ship movement."
        )
    )
    occurred_at: datetime = Field(
        description="Время lifecycle event либо создания physical movement."
    )
    location_code: str | None = Field(
        default=None,
        description="Локация lifecycle event; для physical movement обычно null.",
    )
    from_location_code: str | None = Field(
        default=None,
        description="Исходная локация physical movement; null для lifecycle event.",
    )
    to_location_code: str | None = Field(
        default=None,
        description="Целевая локация physical movement; null для shipment и lifecycle event.",
    )
    movement_refs: list[int] = Field(default_factory=list, description="Все physical legs одного logical события.")
    movement_ref: int | None = Field(
        default=None,
        description="Стабильная ссылка на physical movement; null для событий без движения.",
    )
    container_id: int | None = Field(default=None, description="Исторический ID контейнера операции.")
    container_qr_code: str | None = Field(default=None, description="Исторический QR контейнера операции.")
    container_operation_type: str | None = Field(default=None, description="Тип container operation для logical history item.")
    quantity: Decimal | None = Field(
        default=None,
        description=(
            "Полное количество physical movement, а не количество конкретного КИЗ. "
            "Один linked KIZ означает одну identified unit внутри этого movement."
        ),
    )
    kiz_event_id: int | None = Field(
        default=None,
        description=(
            "Стабильный ID lifecycle event; у объединённого shipment сохраняется вместе "
            "с movement_ref."
        ),
    )
    from_status: Lifecycle | None = Field(
        default=None, description="Lifecycle status до события, если применимо."
    )
    to_status: Lifecycle | None = Field(
        default=None, description="Lifecycle status после события, если применимо."
    )
    author: str | None = Field(
        default=None, description="Автор lifecycle event или physical movement."
    )
    reason: str | None = Field(
        default=None, description="Причина lifecycle event или physical movement."
    )


class KizHistory(BaseModel):
    kiz_code: str = Field(description="Полный регистрозависимый код КИЗ.")
    product_id: str = Field(description="Идентификатор товара (SKU), которому назначен КИЗ.")
    current_state: KizState = Field(
        description="Текущая KIZ projection из wms.kiz; location nullable для shipped."
    )
    timeline: list[KizTimelineEntry] = Field(
        description="Полная хронология lifecycle events и связанных physical movements."
    )
