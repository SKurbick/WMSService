"""KIZ v1: exact locations, no physical movement API."""
from fastapi import APIRouter, Depends, Query, Path, Body
from app.api.v1.dependencies import get_kiz_service
from app.core.schemas.kiz import (
    KizAssignment,
    KizAssignmentResult,
    KizState,
    KizStockSummary,
    KizPage,
    KizEventPage,
    KizTerminalRequest,
    Lifecycle,
)
from app.core.services.kiz_service import KizService
from app.api.v1.openapi_kiz import OPERATIONS, ASSIGNMENT, TERMINAL

router = APIRouter(
    prefix="/kiz",
    tags=["КИЗ"],
    responses={
        404: {"description": "КИЗ, товар или локация не найдены."},
        409: {
            "description": "Конфликт КИЗ или конкурентной записи; error_code и detail содержат причину."
        },
    },
)


@router.post("/assign", response_model=KizAssignmentResult, status_code=201, **OPERATIONS["assign"])
async def assign(
    data: KizAssignment = Body(
        ...,
        openapi_examples={
            "assignment": {"summary": "Назначение одной единице", "value": ASSIGNMENT}
        },
    ),
    service: KizService = Depends(get_kiz_service),
):
    return await service.assign(data)


@router.get("/stock-summary", response_model=KizStockSummary, **OPERATIONS["summary"])
async def stock_summary(
    product_id: str = Query(
        ...,
        min_length=1,
        max_length=50,
        description="Идентификатор существующего товара (SKU).",
        examples=["wild1825"],
    ),
    location_code: str | None = Query(
        None,
        min_length=1,
        description="Код точной локации, без дочерних адресов.",
        examples=["STORAGE-A-01"],
    ),
    container_id: int | None = Query(None, gt=0, description="ID контейнера — прямого holder КИЗ."),
    container_qr_code: str | None = Query(None, description="QR контейнера — прямого holder КИЗ."),
    service: KizService = Depends(get_kiz_service),
):
    return await service.summary(product_id, location_code, container_id, container_qr_code)


@router.get("", response_model=KizPage, **OPERATIONS["list"])
async def list_kiz(
    product_id: str
    | None = Query(None, description="Фильтр по идентификатору товара.", examples=["wild1825"]),
    location_code: str
    | None = Query(None, description="Точное совпадение кода локации.", examples=["STORAGE-A-01"]),
    lifecycle_status: Lifecycle
    | None = Query(
        None,
        description="active — действующие, error — ошибочные, deactivated — деактивированные, shipped — покинувшие склад; без фильтра — все.",
    ),
    container_id: int | None = Query(None, gt=0, description="Фильтр по контейнеру — прямому holder КИЗ."),
    container_qr_code: str | None = Query(None, description="Фильтр по QR контейнера — прямого holder КИЗ."),
    limit: int = Query(50, ge=1, le=200, description="Размер страницы от 1 до 200 записей."),
    offset: int = Query(0, ge=0, description="Число пропускаемых записей; первая страница — 0."),
    service: KizService = Depends(get_kiz_service),
):
    return await service.list(
        product_id, location_code, lifecycle_status, container_id, container_qr_code,
        limit, offset,
    )


@router.get("/{kiz_code:path}/events", response_model=KizEventPage, **OPERATIONS["events"])
async def events(
    kiz_code: str = Path(
        ...,
        description="Полный код КИЗ с percent-encoding в URL; регистр значим.",
        examples=["KIZ-001"],
    ),
    limit: int = Query(50, ge=1, le=200, description="Размер страницы от 1 до 200 записей."),
    offset: int = Query(0, ge=0, description="Число пропускаемых записей; первая страница — 0."),
    service: KizService = Depends(get_kiz_service),
):
    return await service.events(kiz_code, limit, offset)


@router.post("/{kiz_code:path}/mark-error", response_model=KizState, **OPERATIONS["error"])
async def mark_error(
    kiz_code: str = Path(..., description="Полный код КИЗ; регистр значим.", examples=["KIZ-001"]),
    data: KizTerminalRequest = Body(
        ..., openapi_examples={"close": {"summary": "Закрытие с причиной", "value": TERMINAL}}
    ),
    service: KizService = Depends(get_kiz_service),
):
    return await service.terminate(kiz_code, "error", data)


@router.post("/{kiz_code:path}/deactivate", response_model=KizState, **OPERATIONS["deactivate"])
async def deactivate(
    kiz_code: str = Path(..., description="Полный код КИЗ; регистр значим.", examples=["KIZ-001"]),
    data: KizTerminalRequest = Body(
        ..., openapi_examples={"close": {"summary": "Закрытие с причиной", "value": TERMINAL}}
    ),
    service: KizService = Depends(get_kiz_service),
):
    return await service.terminate(kiz_code, "deactivated", data)


@router.get("/{kiz_code:path}", response_model=KizState, **OPERATIONS["get"])
async def get_kiz(
    kiz_code: str = Path(
        ...,
        description="Полный код КИЗ с percent-encoding в URL; регистр значим.",
        examples=["KIZ-001"],
    ),
    service: KizService = Depends(get_kiz_service),
):
    return await service.get(kiz_code)
