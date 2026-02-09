"""API endpoints для системных операций"""

from fastapi import APIRouter, Depends, status
from typing import List

from app.core.schemas.system import (
    RecalculateInventoryRequest,
    RecalculateInventoryResponse,
    CreateSnapshotRequest,
    CreateSnapshotResponse,
    RefreshViewsResponse,
    IntegrityCheckResult,
)
from app.core.services.system_service import SystemService
from app.api.v1.dependencies import get_system_service

router = APIRouter(prefix="/system", tags=["Системные"])


@router.post("/validate-integrity", response_model=List[IntegrityCheckResult])
async def validate_integrity(
    service: SystemService = Depends(get_system_service),
):
    """
    Проверить целостность данных

    Сравнивает рассчитанные остатки из movements с текущими в inventory.
    Возвращает список расхождений. Пустой список = данные целостны.

    **Возвращает:**
    - Список расхождений между inventory и movements
    """
    return await service.validate_integrity()


@router.post(
    "/recalculate-inventory",
    response_model=RecalculateInventoryResponse,
    status_code=status.HTTP_200_OK,
)
async def recalculate_inventory(
    data: RecalculateInventoryRequest = RecalculateInventoryRequest(),
    service: SystemService = Depends(get_system_service),
):
    """
    Пересчитать остатки

    **ВНИМАНИЕ:** Эта операция удаляет текущие записи inventory
    и пересчитывает их заново из событий movements.

    Используйте для:
    - Исправления расхождений после сбоев
    - Восстановления данных из бэкапа
    - Проверки целостности Event Sourcing

    **Параметры:**
    - **product_id**: ID товара (опционально, если None - все товары)
    - **from_date**: Пересчитать с даты (опционально)

    **Возвращает:**
    - Статистику пересчёта
    """
    return await service.recalculate_inventory(data)


@router.post(
    "/create-snapshot",
    response_model=CreateSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_snapshot(
    data: CreateSnapshotRequest = CreateSnapshotRequest(),
    service: SystemService = Depends(get_system_service),
):
    """
    Создать снимок остатков

    Сохраняет текущее состояние inventory в таблицу snapshots.
    Обычно запускается по cron в конце дня для истории остатков.

    **Параметры:**
    - **snapshot_date**: Дата снимка (по умолчанию сегодня)

    **Возвращает:**
    - Статистику созданного снимка
    """
    return await service.create_snapshot(data)


@router.post("/refresh-materialized-views", response_model=RefreshViewsResponse)
async def refresh_materialized_views(
    service: SystemService = Depends(get_system_service),
):
    """
    Обновить материализованные представления

    Обновляет mv_product_stock CONCURRENTLY (без блокировки чтения).
    Рекомендуется запускать периодически для актуализации агрегатов.

    **Возвращает:**
    - Статистику обновлённого представления
    """
    return await service.refresh_materialized_views()
