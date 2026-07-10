"""API endpoints для операций комплектации и разукомплектации."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.v1.dependencies import get_kit_operation_service
from app.core.enums import KitOperationStatus, KitOperationType
from app.core.schemas.kit_operations import (
    KitOperationCreate,
    KitOperationLocationCreate,
    KitOperationLocationDeactivate,
    KitOperationLocationListResponse,
    KitOperationLocationResponse,
    KitOperationResponse,
    KitOperationSummaryResponse,
)
from app.core.services.kit_operation_service import KitOperationService

router = APIRouter(prefix="/kit-operations", tags=["Операции комплектов"])


@router.get(
    "/locations",
    response_model=KitOperationLocationListResponse,
    summary="Получить разрешённые локации комплектации",
    description=(
        "Возвращает allow-list WMS-локаций, где разрешены kit operations. "
        "В MVP поддерживается только `scope='direct'`: используются остатки прямо на этой location_id."
    ),
)
async def list_kit_operation_locations(
    is_active: Optional[bool] = Query(
        None,
        description="Фильтр активности разрешения. true - только активные, false - только деактивированные.",
    ),
    limit: int = Query(50, ge=1, le=1000, description="Размер страницы."),
    offset: int = Query(0, ge=0, description="Смещение страницы."),
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.list_operation_locations(
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/locations",
    response_model=KitOperationLocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить разрешённую локацию комплектации",
    description=(
        "Добавляет или реактивирует active allow-list запись для `kit_operations/direct`. "
        "Локация должна существовать в `wms.locations` и быть активной. Проверка `level=5` не применяется."
    ),
    responses={
        201: {"description": "Разрешённая локация создана или реактивирована."},
        404: {"description": "location_code не найден в wms.locations."},
        409: {"description": "Локация неактивна."},
    },
)
async def create_kit_operation_location(
    data: KitOperationLocationCreate,
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.create_operation_location(data)


@router.patch(
    "/locations/{operation_location_id}/deactivate",
    response_model=KitOperationLocationResponse,
    summary="Деактивировать разрешённую локацию комплектации",
    description=(
        "Переводит allow-list запись kit operations в `is_active=false`. "
        "Если запись уже неактивна, возвращает её текущее состояние."
    ),
    responses={
        200: {"description": "Разрешённая локация деактивирована или уже была неактивна."},
        404: {"description": "operation_location_id не найден."},
    },
)
async def deactivate_kit_operation_location(
    data: KitOperationLocationDeactivate,
    operation_location_id: int = Path(
        ...,
        ge=1,
        description="ID разрешённой локации из wms.operation_locations.",
        examples=[1],
    ),
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.deactivate_operation_location(operation_location_id, data)


@router.post(
    "",
    response_model=KitOperationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Выполнить комплектацию или разукомплектацию",
    description=(
        "Создаёт операцию комплекта в одной DB transaction. В `operation_type` передавайте "
        "`assembly`, чтобы собрать комплект из компонентов, или `disassembly`, чтобы разобрать "
        "комплект обратно на компоненты. `location_code` должен быть "
        "активной WMS-локацией, заранее разрешённой в `wms.operation_locations` для "
        "`operation_code='kit_operations'` и `scope='direct'`. Остатки меняются только "
        "через `INSERT INTO wms.movements`; дочерние адреса не учитываются."
    ),
    responses={
        201: {"description": "Операция выполнена и завершена со статусом completed."},
        400: {"description": "Невалидный тип операции, quantity или kit_components."},
        404: {"description": "Не найдена локация, комплект или компонент."},
        409: {"description": "Локация неактивна/не разрешена, товар неактивен или остатка недостаточно."},
    },
)
async def create_kit_operation(
    data: KitOperationCreate,
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.create_operation(data)


@router.get(
    "",
    response_model=List[KitOperationSummaryResponse],
    summary="Получить список операций комплектов",
    description="Возвращает журнал операций комплектации/разукомплектации с фильтрами.",
)
async def list_kit_operations(
    operation_type: Optional[KitOperationType] = Query(
        None,
        description="Фильтр по типу операции: assembly или disassembly.",
    ),
    kit_product_id: Optional[str] = Query(
        None,
        description="Фильтр по ID товара-комплекта, например metawild_test.",
        examples=["metawild_test"],
    ),
    status: Optional[KitOperationStatus] = Query(
        None,
        description="Фильтр по статусу операции.",
    ),
    location_code: Optional[str] = Query(
        None,
        description="Фильтр по коду WMS-локации операции.",
        examples=["PUSHKINO-КОМПЛЕКТАЦИЯ"],
    ),
    date_from: Optional[datetime] = Query(
        None,
        description="Начало периода по created_at, ISO datetime.",
    ),
    date_to: Optional[datetime] = Query(
        None,
        description="Конец периода по created_at, ISO datetime.",
    ),
    limit: int = Query(100, ge=1, le=1000, description="Размер страницы."),
    offset: int = Query(0, ge=0, description="Смещение страницы."),
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.list_operations(
        operation_type=operation_type,
        kit_product_id=kit_product_id,
        status=status,
        location_code=location_code,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{operation_id}",
    response_model=KitOperationResponse,
    summary="Получить операцию комплекта по ID",
    description=(
        "Возвращает детальную карточку операции, включая строки и `movement_id` по каждой строке. "
        "Поле `items[].role` показывает назначение строки: `component_consumption` - компонент "
        "списан при сборке; `kit_result` - готовый комплект получен при сборке; "
        "`kit_consumption` - готовый комплект списан при разукомплектации; "
        "`component_result` - компонент получен после разукомплектации. Для комплекта "
        "metawild_test = testwild x 2 + testwild2 x 1 при quantity=3 сборка вернёт две "
        "строки `component_consumption` и одну `kit_result`, а разукомплектация - одну "
        "`kit_consumption` и две `component_result`."
    ),
    responses={404: {"description": "operation_id не найден."}},
)
async def get_kit_operation(
    operation_id: int = Path(
        ...,
        ge=1,
        description="ID операции комплекта из wms.kit_operations.",
        examples=[123],
    ),
    service: KitOperationService = Depends(get_kit_operation_service),
):
    return await service.get_operation(operation_id)
