"""API endpoints для движений товаров"""

from fastapi import APIRouter, Depends, status, Query, Path, Body
from typing import List, Optional
from datetime import date

from app.core.schemas.movement import (
    MovementCreate,
    MovementBulkCreateResponse,
    MovementResponse,
)
from app.core.services.movement_service import MovementService
from app.api.v1.dependencies import get_movement_service

router = APIRouter(prefix="/movements", tags=["Движения"])


@router.post(
    "",
    response_model=MovementBulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать движения товаров",
    description="Атомарно создаёт batch из 1-500 movements. Остатки обновляются триггером БД через wms.movements.",
)
async def create_movement(
    data: List[MovementCreate] = Body(
        ...,
        min_length=1,
        max_length=500,
        description="Список movements для создания (1-500 элементов)",
    ),
    service: MovementService = Depends(get_movement_service),
):
    """
    Создать movements (батч операция)

    Регистрирует один или несколько movements атомарно.
    Все movements создаются в одной транзакции (всё или ничего).
    Триггер в БД автоматически обновляет inventory.

    **Параметры:**
    - **data**: Массив movements (минимум 1, максимум 500)

    Каждый movement содержит:
    - **movement_type**: Тип движения
    - **product_id**: ID товара
    - **from_location_code**: Код локации-источника (опционально)
    - **to_location_code**: Код локации-назначения (опционально)
    - **quantity**: Количество
    - **batch_number**: Номер партии (опционально)
    - **container_code**: Код контейнера (опционально)
    - **user_name**: Имя пользователя (опционально)
    - **reason**: Причина/комментарий (опционально)

    **Возвращает:**
    - **created**: Список созданных movements
    - **total**: Количество созданных movements

    **Атомарность:**
    - Если хотя бы один movement не прошёл валидацию → ошибка, ничего не создано
    - Если ошибка при создании любого movement → откат всех
    """
    return await service.create_movement(data)


@router.get(
    "",
    response_model=List[MovementResponse],
    summary="Получить историю движений",
    description="Возвращает журнал движений товаров с фильтрами по товару, контейнеру, типу движения и датам.",
)
async def get_movements(
    product_id: Optional[str] = Query(None, description="Фильтр по ID товара"),
    container_code: Optional[str] = Query(None, description="Фильтр по коду контейнера"),
    movement_type: Optional[str] = Query(None, description="Фильтр по типу движения"),
    from_date: Optional[date] = Query(None, description="Дата начала периода"),
    to_date: Optional[date] = Query(None, description="Дата окончания периода"),
    limit: int = Query(100, ge=1, le=1000, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    service: MovementService = Depends(get_movement_service),
):
    """
    Получить историю движений

    Возвращает историю движений с возможностью фильтрации.
    Включает batch_number для FIFO/FEFO.

    **Параметры:**
    - **product_id**: Фильтр по ID товара (опционально)
    - **container_code**: Фильтр по коду контейнера (опционально)
    - **movement_type**: Фильтр по типу движения (опционально)
    - **from_date**: Дата начала периода (опционально)
    - **to_date**: Дата окончания периода (опционально)
    - **limit**: Лимит записей (по умолчанию 100, максимум 1000)
    - **offset**: Смещение для пагинации

    **Возвращает:**
    - Список движений
    """
    return await service.get_movements(
        product_id=product_id,
        container_code=container_code,
        movement_type=movement_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/product/{product_id}",
    response_model=List[MovementResponse],
    summary="Получить движения товара",
    description="Возвращает последние движения по конкретному product_id.",
)
async def get_movements_by_product(
    product_id: str = Path(..., description="ID товара"),
    limit: int = Query(100, ge=1, le=1000, description="Лимит записей"),
    service: MovementService = Depends(get_movement_service),
):
    """
    Получить движения по товару

    Возвращает полную историю движений конкретного товара.

    **Параметры:**
    - **product_id**: ID товара
    - **limit**: Лимит записей (по умолчанию 100)

    **Возвращает:**
    - История движений товара
    """
    return await service.get_movements_by_product(product_id, limit)
