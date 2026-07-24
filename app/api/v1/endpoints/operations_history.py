"""Read-only endpoint единого списка бизнес-операций."""

from datetime import date

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import get_operations_history_service
from app.core.schemas.operations_history import OperationsHistoryResponse
from app.core.schemas.operations_history_detail import OperationDetailResponse
from app.core.services.operations_history_service import OperationsHistoryService
from app.api.v1.openapi_history import DETAIL_EXAMPLES, OPERATIONS_EXAMPLES, error_response

router = APIRouter(prefix="/operations-history", tags=["История WMS"])


@router.get(
    "/{event_id}",
    response_model=OperationDetailResponse,
    summary="Получить детали складской операции",
    operation_id="get_operation_history_detail",
    response_description="Typed header, профильные items, связанные movements и нефатальные warnings.",
    description="""Открывает `event_id`, полученный из списка операций. Prefix выбирает
один из typed adapters: kit, re-sorting, FBS или standalone movement. Ответ содержит
source-specific `header`, профильные `items`, структурно связанные `movements` и
нефатальные `warnings`. Missing/ambiguous movement link внутри существующего business
header возвращает HTTP 200; 404 означает отсутствие самого header/event.

Допустимые форматы: `kit_operation:<id>`, `re_sorting:<id>`, `fbs_shipment:<id>` и
`movement:<movement_id>:<created_at_epoch_us>`. Идентификаторы не являются UUID.""",
    responses={
        200: {
            "description": "Детальная карточка операции.",
            "content": {"application/json": {"examples": DETAIL_EXAMPLES}},
        },
        400: error_response(
            "Malformed event_id или неподдерживаемый prefix.",
            {
                "detail": "Неподдерживаемый prefix event ID: unknown",
                "error_code": "INVALID_OPERATION_EVENT_ID",
            },
        ),
        404: error_response(
            "Business header или точная movement-строка не найдены.",
            {"detail": "Kit operation 42 не найдена", "error_code": "OPERATION_HISTORY_NOT_FOUND"},
        ),
        422: error_response(
            "Path parameter не прошёл FastAPI validation.",
            {
                "detail": [
                    {
                        "type": "string_too_short",
                        "loc": ["path", "event_id"],
                        "msg": "String should have at least 1 character",
                        "input": "",
                    }
                ]
            },
            validation=True,
        ),
        500: error_response(
            "Необработанная ошибка сервиса или БД.",
            {
                "detail": "Внутренняя ошибка сервера",
                "message": "Внутренняя ошибка сервера",
                "error_code": "INTERNAL_ERROR",
            },
        ),
    },
)
async def get_operation_history_detail(
    event_id: str = Path(
        ...,
        min_length=1,
        description="Обязательный детерминированный event_id из GET /api/operations-history; не UUID.",
        examples=[
            "kit_operation:42",
            "re_sorting:7",
            "fbs_shipment:156",
            "movement:29530:1784198400451000",
        ],
    ),
    service: OperationsHistoryService = Depends(get_operations_history_service),
) -> OperationDetailResponse:
    return await service.get_operation_detail(event_id)


@router.get(
    "",
    response_model=OperationsHistoryResponse,
    summary="Получить единый журнал складских операций",
    operation_id="list_operations_history",
    response_description="Стабильно отсортированная страница нормализованных событий.",
    description="""Нормализованный журнал из четырёх источников: `kit_operation`,
`re_sorting_operation`, `fbs_shipment`, `movement`. Kit и re-sorting представлены одним
business header; FBS — одним shipment header, включая failed/validation_failed;
самостоятельные movements — отдельными событиями. Структурно связанные movements
исключаются из standalone branch. Эвристики по времени, reason, автору или номеру
документа не используются. Receipt, task и container headers в MVP не входят.

Гибридная модель нужна, поскольку надёжный header → items → movements есть не у всех
legacy flows. Сортировка стабильна: `created_at DESC, event_id DESC`.""",
    responses={
        200: {
            "description": "Страница единого журнала.",
            "content": {"application/json": {"examples": OPERATIONS_EXAMPLES}},
        },
        400: error_response(
            "Неверный период либо неизвестный source_type/operation_type.",
            {
                "detail": "Неизвестный source_type: unknown",
                "message": "Неизвестный source_type: unknown",
                "error_code": "OPERATIONS_HISTORY_VALIDATION_ERROR",
            },
        ),
        404: error_response(
            "Указанная точная location не найдена.",
            {"detail": "Локация с ID 999 не найдена", "error_code": "LOCATION_NOT_FOUND"},
        ),
        422: error_response(
            "Тип query parameter не прошёл FastAPI validation.",
            {
                "detail": [
                    {
                        "type": "int_parsing",
                        "loc": ["query", "location_id"],
                        "msg": "Input should be a valid integer",
                        "input": "x",
                    }
                ]
            },
            validation=True,
        ),
        500: error_response(
            "Необработанная ошибка сервиса или БД.",
            {
                "detail": "Внутренняя ошибка сервера",
                "message": "Внутренняя ошибка сервера",
                "error_code": "INTERNAL_ERROR",
            },
        ),
    },
)
async def get_operations_history(
    date_from: date = Query(
        ...,
        description="Обязательный первый включённый день Europe/Moscow.",
        examples=["2026-07-01"],
    ),
    date_to: date = Query(
        ...,
        description="Обязательный последний включённый день Europe/Moscow; период до 366 дней.",
        examples=["2026-07-31"],
    ),
    source_type: str
    | None = Query(
        None,
        description="Необязательный adapter: kit_operation, re_sorting_operation, fbs_shipment или movement.",
        examples=["kit_operation"],
    ),
    operation_type: str
    | None = Query(
        None,
        description="Необязательный тип: receive, putaway, transfer, pick, ship, unpack, adjust, kit_assembly, kit_disassembly, re_sorting, fbs_shipment. FBS-списание представлено ship; write_off не поддерживается.",
        examples=["transfer"],
    ),
    product_id: str
    | None = Query(
        None,
        description="Ищется в kit items, обеих SKU re-sorting, FBS items или напрямую movement.",
        examples=["wild1825"],
    ),
    location_id: int
    | None = Query(
        None,
        description="Точная location: операция kit/re-sort либо любая сторона movement. Subtree нет; FBS без relational location не возвращается.",
        examples=[123],
    ),
    author: str
    | None = Query(
        None,
        description="Точное, регистрозависимое сравнение автора в соответствующем источнике.",
        examples=["operator"],
    ),
    status: str
    | None = Query(
        None,
        description="Фактический status kit/re-sorting/FBS; standalone movements при фильтре исключаются.",
        examples=["completed"],
    ),
    limit: int = Query(
        100,
        ge=1,
        le=200,
        description="Событий после UNION всех источников, 1..200.",
        examples=[100],
    ),
    offset: int = Query(
        0, ge=0, description="Смещение после UNION и общей сортировки.", examples=[0]
    ),
    service: OperationsHistoryService = Depends(get_operations_history_service),
) -> OperationsHistoryResponse:
    return await service.get_operations(
        date_from=date_from,
        date_to=date_to,
        source_type=source_type,
        operation_type=operation_type,
        product_id=product_id,
        location_id=location_id,
        author=author,
        status=status,
        limit=limit,
        offset=offset,
    )
