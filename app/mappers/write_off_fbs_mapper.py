"""Маппер: WriteOffAccordingToFBS → MovementCreate"""

from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.core.schemas.movement import MovementCreate
from app.core.enums import MovementType
from app.shared.config import settings


def map_to_movement(item: WriteOffAccordingToFBS) -> MovementCreate:
    """
    Преобразует схему списания из ФБС в схему создания движения.

    Фиксированные значения:
    - movement_type = "ship" (отгрузка)
    - from_location_code = "FBS" (зона FBS, россыпь)
    """
    return MovementCreate(
        movement_type=MovementType.SHIP,
        product_id=item.product_id,
        quantity=item.quantity,
        user_name=item.author,
        from_location_code=settings.FBS_LOCATION_CODE,
        reason=f"Списание из ФБС зоны. Сборочные задания: {item.assembly_tasks}",
    )
