"""Read-only chronological history contract for one KIZ."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.schemas.kiz import KizState, Lifecycle


class KizTimelineEntry(BaseModel):
    event_type: str = Field(
        description=(
            "Тип события жизненного цикла либо связанного физического движения. "
            "Событие shipped объединяется со связанным движением ship."
        )
    )
    occurred_at: datetime = Field(
        description="Время события жизненного цикла либо создания физического движения с часовым поясом."
    )
    location_code: str | None = Field(
        default=None,
        description="Локация события жизненного цикла; для физического движения обычно null.",
    )
    from_location_code: str | None = Field(
        default=None,
        description="Исходная локация физического движения; null для события без движения.",
    )
    to_location_code: str | None = Field(
        default=None,
        description="Целевая локация физического движения; null для отгрузки и события без движения.",
    )
    movement_refs: list[int] = Field(
        default_factory=list,
        description="Ссылки на все физические части одного события контейнерной операции.",
    )
    movement_ref: int | None = Field(
        default=None,
        description="Стабильная ссылка на физическое движение; null для событий без движения.",
    )
    container_id: int | None = Field(
        default=None, description="Исторический ID контейнера операции."
    )
    container_qr_code: str | None = Field(
        default=None, description="Исторический QR контейнера операции."
    )
    container_operation_type: str | None = Field(
        default=None,
        description="Тип контейнерной операции, объединяющей связанные движения в одну запись истории.",
    )
    quantity: Decimal | None = Field(
        default=None,
        description=(
            "Полное количество физического движения. Конкретный связанный КИЗ "
            "представляет одну идентифицированную единицу внутри этого количества."
        ),
    )
    kiz_event_id: int | None = Field(
        default=None,
        description=(
            "Идентификатор события жизненного цикла; у объединённой записи отгрузки "
            "сохраняется вместе с movement_ref."
        ),
    )
    from_status: Lifecycle | None = Field(
        default=None, description="Состояние жизненного цикла до события, если применимо."
    )
    to_status: Lifecycle | None = Field(
        default=None, description="Состояние жизненного цикла после события, если применимо."
    )
    author: str | None = Field(
        default=None, description="Автор события жизненного цикла или физического движения."
    )
    reason: str | None = Field(
        default=None, description="Причина события жизненного цикла или физического движения."
    )


class KizHistory(BaseModel):
    kiz_code: str = Field(description="Полный регистрозависимый код КИЗ.")
    product_id: str = Field(description="Идентификатор товара (SKU), которому назначен КИЗ.")
    current_state: KizState = Field(
        description="Текущее состояние КИЗ; прямое место хранения отсутствует после отгрузки."
    )
    timeline: list[KizTimelineEntry] = Field(
        description="Полная хронология событий жизненного цикла и связанных физических движений без пагинации."
    )
