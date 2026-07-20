"""HTTP API операций пересортицы товара."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Path, Query, status
from app.api.v1.dependencies import get_re_sorting_operation_service
from app.core.enums import ReSortingOperationStatus
from app.core.schemas.re_sorting_operations import (
    ReSortingOperationCreate,
    ReSortingOperationLocationCreate,
    ReSortingOperationLocationDeactivate,
    ReSortingOperationLocationListResponse,
    ReSortingOperationLocationResponse,
    ReSortingOperationResponse,
    ReSortingOperationSummaryResponse,
)
from app.core.services.re_sorting_operation_service import ReSortingOperationService

router = APIRouter(prefix="/re-sorting-operations", tags=["Операции пересортицы"])


@router.get(
    "/locations",
    response_model=ReSortingOperationLocationListResponse,
    summary="Получить разрешённые локации пересортицы",
    description="Возвращает allow-list WMS-локаций только для `operation_code='re_sorting_operations'`. В MVP используется `scope='direct'`: остатки дочерних адресов не включаются.",
    responses={200: {"description": "Страница разрешённых локаций пересортицы."}},
)
async def list_locations(
    is_active: Optional[bool] = Query(
        None,
        description="Фильтр активности: true — активные разрешения, false — деактивированные; без значения — все.",
    ),
    limit: int = Query(50, ge=1, le=1000, description="Размер страницы."),
    offset: int = Query(0, ge=0, description="Смещение от начала списка."),
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.list_operation_locations(is_active, limit, offset)


@router.post(
    "/locations",
    response_model=ReSortingOperationLocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить разрешённую локацию пересортицы",
    description="Создаёт или реактивирует разрешение `re_sorting_operations/direct`. Локация должна существовать и быть активной. Разрешения kit operations не изменяются.",
    responses={
        201: {"description": "Разрешение создано или реактивировано."},
        404: {"description": "Локация с указанным location_code не найдена."},
        409: {"description": "Найденная локация неактивна."},
        422: {"description": "Тело запроса не прошло валидацию."},
    },
)
async def create_location(
    data: ReSortingOperationLocationCreate,
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.create_operation_location(data)


@router.patch(
    "/locations/{operation_location_id}/deactivate",
    response_model=ReSortingOperationLocationResponse,
    summary="Деактивировать локацию пересортицы",
    description="Деактивирует только allow-list строку с `operation_code='re_sorting_operations'` и `scope='direct'`. Повторная деактивация возвращает текущее состояние.",
    responses={
        200: {"description": "Разрешение деактивировано или уже было неактивным."},
        404: {"description": "Разрешение пересортицы с таким ID не найдено."},
        422: {"description": "ID или тело запроса не прошли валидацию."},
    },
)
async def deactivate_location(
    data: ReSortingOperationLocationDeactivate,
    operation_location_id: int = Path(
        ..., ge=1, description="ID разрешения из wms.operation_locations.", examples=[17]
    ),
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.deactivate_operation_location(operation_location_id, data)


@router.post(
    "",
    response_model=ReSortingOperationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Выполнить пересортицу товара",
    description=(
        "Атомарно переидентифицирует physical loose-остаток: уменьшает `from_product_id` и увеличивает `to_product_id` на одинаковое целое количество. "
        "Создаются две строки операции и два movements типа `re_sorting`; inventory изменяется только DB-trigger. "
        "Учитывается только available loose-остаток точной direct-локации без batch/container. Мягкие резервы, состав комплектов, 1С и RabbitMQ не участвуют."
    ),
    responses={
        201: {"description": "Пересортица атомарно завершена со статусом completed."},
        400: {"description": "from_product_id совпадает с to_product_id."},
        404: {"description": "Не найдена локация, исходный или целевой товар."},
        409: {
            "description": "Локация неактивна/не разрешена, товар неактивен либо loose-остаток отсутствует или недостаточен."
        },
        422: {
            "description": "Некорректное тело: дробное/неположительное quantity или пустые обязательные строки."
        },
    },
)
async def create_operation(
    data: ReSortingOperationCreate,
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.create_operation(data)


@router.get(
    "",
    response_model=List[ReSortingOperationSummaryResponse],
    summary="Получить журнал пересортицы",
    description="Возвращает заголовки операций пересортицы от новых к старым. Фильтры можно комбинировать; период применяется к `created_at`.",
    responses={200: {"description": "Список операций текущей страницы."}},
)
async def list_operations(
    from_product_id: Optional[str] = Query(
        None, description="Фильтр по исходному SKU.", examples=["wild100"]
    ),
    to_product_id: Optional[str] = Query(
        None, description="Фильтр по целевому SKU.", examples=["wild101"]
    ),
    status: Optional[ReSortingOperationStatus] = Query(
        None, description="Фильтр по статусу: processing, completed или failed."
    ),
    location_code: Optional[str] = Query(
        None, description="Фильтр по коду direct-локации.", examples=["PUSHKINO-ПЕРЕСОРТИЦА"]
    ),
    date_from: Optional[datetime] = Query(
        None,
        description="Начало периода по created_at включительно, ISO 8601.",
        examples=["2026-07-16T00:00:00+03:00"],
    ),
    date_to: Optional[datetime] = Query(
        None,
        description="Конец периода по created_at включительно, ISO 8601.",
        examples=["2026-07-16T23:59:59+03:00"],
    ),
    limit: int = Query(100, ge=1, le=1000, description="Размер страницы."),
    offset: int = Query(0, ge=0, description="Смещение от начала журнала."),
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.list_operations(
        from_product_id=from_product_id,
        to_product_id=to_product_id,
        status=status,
        location_code=location_code,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{operation_id}",
    response_model=ReSortingOperationResponse,
    summary="Получить пересортицу по ID",
    description="Возвращает заголовок операции и две audit-строки. `source_outgoing` связан со списанием исходного SKU, `target_incoming` — с приходом целевого SKU; каждая строка содержит идентификатор movement.",
    responses={
        200: {"description": "Детальная карточка пересортицы."},
        404: {"description": "Операция пересортицы с таким ID не найдена."},
        422: {"description": "operation_id должен быть положительным целым числом."},
    },
)
async def get_operation(
    operation_id: int = Path(
        ..., ge=1, description="ID операции из wms.re_sorting_operations.", examples=[4812]
    ),
    service: ReSortingOperationService = Depends(get_re_sorting_operation_service),
):
    return await service.get_operation(operation_id)
