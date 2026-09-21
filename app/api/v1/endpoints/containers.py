"""API endpoints для контейнеров"""

from fastapi import APIRouter, Depends, status, Query, Path
from typing import List, Optional

from app.core.schemas.container import (
    ContainerRegister,
    ContainerRegisterResponse,
    ContainerResponse,
    ContainerStatusUpdate,
    ContainerStatusUpdateResponse,
    ContainerHistoryItem,
    ContainerInLocation,
)
from app.core.services.container_service import ContainerService
from app.api.v1.dependencies import get_container_service

router = APIRouter(prefix="/containers", tags=["Контейнеры"])


@router.post(
    "/register",
    response_model=ContainerRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Зарегистрировать контейнер",
    description=(
        "Создаёт только пустой контейнер. contents должен быть пустым; товар добавляется "
        "через POST /api/container-operations/fill."
    ),
)
async def register_container(
    data: ContainerRegister,
    service: ContainerService = Depends(get_container_service),
):
    """
    Зарегистрировать контейнер

    Создаёт новый пустой контейнер.

    **Параметры:**
    - **qr_code**: QR-код контейнера
    - **container_type**: Тип контейнера (pallet, box, cage, trolley)
    - **location_code**: Код локации размещения
    - **contents**: Обязательный пустой список

    **Возвращает:**
    - ID созданного контейнера и количество зарегистрированных позиций
    """
    return await service.register_container(data)


@router.get(
    "/{qr_code}",
    response_model=ContainerResponse,
    summary="Получить контейнер по QR-коду",
    description="Возвращает карточку контейнера, его локацию, статус, вложенность и содержимое.",
)
async def get_container(
    qr_code: str = Path(..., description="QR-код контейнера"),
    service: ContainerService = Depends(get_container_service),
):
    """
    Получить контейнер по QR-коду

    Возвращает детальную информацию о контейнере: тип, статус, локацию,
    родительский контейнер и содержимое.

    **Параметры:**
    - **qr_code**: QR-код контейнера

    **Возвращает:**
    - Полную информацию о контейнере с содержимым
    """
    return await service.get_container_by_qr(qr_code)


@router.patch(
    "/{container_id}/status",
    response_model=ContainerStatusUpdateResponse,
    summary="Изменить статус контейнера",
    description="Меняет статус контейнера; заблокированный контейнер нельзя разблокировать этим endpoint.",
)
async def update_container_status(
    container_id: int = Path(..., description="ID контейнера"),
    data: ContainerStatusUpdate = ...,
    service: ContainerService = Depends(get_container_service),
):
    """
    Обновить статус контейнера

    Меняет статус контейнера (empty, open, sealed, blocked).
    Заблокированный контейнер нельзя разблокировать через этот endpoint.

    **Параметры:**
    - **container_id**: ID контейнера
    - **status**: Новый статус

    **Возвращает:**
    - Обновлённый статус контейнера
    """
    return await service.update_container_status(container_id, data)


@router.get(
    "/{qr_code}/history",
    response_model=List[ContainerHistoryItem],
    summary="Получить историю контейнера",
    description="Возвращает движения товаров, связанные с указанным QR-кодом контейнера.",
)
async def get_container_history(
    qr_code: str = Path(..., description="QR-код контейнера"),
    service: ContainerService = Depends(get_container_service),
):
    """
    Получить историю контейнера

    Возвращает все движения товаров связанные с контейнером.

    **Параметры:**
    - **qr_code**: QR-код контейнера

    **Возвращает:**
    - Список движений контейнера
    """
    return await service.get_container_history(qr_code)


# Этот endpoint логически относится к locations, но по ТЗ в модуле containers
@router.get(
    "/location/{location_id}",
    response_model=List[ContainerInLocation],
    summary="Получить контейнеры в локации",
    description="Возвращает контейнеры в указанной WMS-локации с фильтрами по статусу и типу.",
)
async def get_containers_in_location(
    location_id: int = Path(..., description="ID локации"),
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    container_type: Optional[str] = Query(None, description="Фильтр по типу контейнера"),
    service: ContainerService = Depends(get_container_service),
):
    """
    Получить контейнеры в локации

    Возвращает все контейнеры в указанной локации с возможностью фильтрации.

    **Параметры:**
    - **location_id**: ID локации
    - **status**: Фильтр по статусу (опционально)
    - **container_type**: Фильтр по типу контейнера (опционально)

    **Возвращает:**
    - Список контейнеров в локации
    """
    return await service.get_containers_in_location(location_id, status, container_type)
