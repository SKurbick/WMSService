"""API endpoints для контейнеров"""

from fastapi import APIRouter, Depends, status, Query, Path
from typing import List, Optional

from app.core.schemas.container import (
    ContainerRegister,
    ContainerRegisterResponse,
    ContainerResponse,
    ContainerLocationUpdate,
    ContainerLocationUpdateResponse,
    ContainerUnpack,
    ContainerUnpackResponse,
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
)
async def register_container(
    data: ContainerRegister,
    service: ContainerService = Depends(get_container_service),
):
    """
    Зарегистрировать контейнер

    Создаёт новый контейнер с содержимым. Автоматически создаёт события
    `receive` в movements для каждого товара.

    **Параметры:**
    - **qr_code**: QR-код контейнера
    - **container_type**: Тип контейнера (pallet, box, cage, trolley)
    - **location_code**: Код локации размещения
    - **contents**: Список товаров в контейнере

    **Возвращает:**
    - ID созданного контейнера и количество зарегистрированных позиций
    """
    return await service.register_container(data)


@router.get("/{qr_code}", response_model=ContainerResponse)
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


@router.put("/{container_id}/location", response_model=ContainerLocationUpdateResponse)
async def update_container_location(
    container_id: int = Path(..., description="ID контейнера"),
    data: ContainerLocationUpdate = ...,
    service: ContainerService = Depends(get_container_service),
):
    """
    Обновить локацию контейнера

    Перемещает контейнер в новую локацию. Триггер в БД создаёт
    события `transfer` в movements с batch_number.

    **Параметры:**
    - **container_id**: ID контейнера
    - **location_code**: Новый код локации

    **Возвращает:**
    - Обновлённую информацию о контейнере
    """
    return await service.update_container_location(container_id, data)


@router.post("/{container_id}/unpack", response_model=ContainerUnpackResponse)
async def unpack_container(
    container_id: int = Path(..., description="ID контейнера"),
    data: ContainerUnpack = ...,
    service: ContainerService = Depends(get_container_service),
):
    """
    Вскрыть контейнер

    Извлекает часть товара из контейнера в россыпь.
    Создаёт два положительных движения в movements.

    **Параметры:**
    - **container_id**: ID контейнера
    - **qr_code**: QR-код контейнера (для проверки)
    - **product_id**: ID товара для извлечения
    - **quantity**: Количество для извлечения

    **Возвращает:**
    - Информацию об оставшемся количестве в контейнере и россыпи
    """
    return await service.unpack_container(container_id, data)


@router.patch("/{container_id}/status", response_model=ContainerStatusUpdateResponse)
async def update_container_status(
    container_id: int = Path(..., description="ID контейнера"),
    data: ContainerStatusUpdate = ...,
    service: ContainerService = Depends(get_container_service),
):
    """
    Обновить статус контейнера

    Меняет статус контейнера (empty, sealed, open, in_transit, blocked).
    Заблокированный контейнер нельзя разблокировать через этот endpoint.

    **Параметры:**
    - **container_id**: ID контейнера
    - **status**: Новый статус

    **Возвращает:**
    - Обновлённый статус контейнера
    """
    return await service.update_container_status(container_id, data)


@router.get("/{qr_code}/history", response_model=List[ContainerHistoryItem])
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
