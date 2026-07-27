"""Read-only история документа поступления."""

from datetime import date

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import get_receipt_history_service
from app.core.schemas.receipt_history import ReceiptHistoryListResponse, ReceiptHistoryResponse
from app.core.services.receipt_history_service import ReceiptHistoryService
from app.api.v1.openapi_history import RECEIPT_EXAMPLES, RECEIPT_LIST_EXAMPLES, error_response

router = APIRouter(prefix="/receipts", tags=["История WMS"])


@router.get(
    "/history",
    response_model=ReceiptHistoryListResponse,
    summary="Получить список документов и ревизий поступлений",
    operation_id="list_receipt_history",
    response_description="Страница legacy revisions и WMS-only snapshots.",
    description="""Одна строка — одна точная legacy revision либо один WMS-only snapshot.
Один GUID может повторяться. Период применяется к revision_at по Europe/Moscow;
undated legacy rows доступны только с include_undated=true. is_current=true включает
текущие revisions/documents. Клик использует item.guid для GET /api/receipts/{guid}/history;
row_id разбирать не нужно. Receipt movements не связываются эвристически.
Пагинация выполняется после группировки и всех фильтров.""",
    responses={
        200: {
            "description": "Список сформирован, включая пустой.",
            "content": {"application/json": {"examples": RECEIPT_LIST_EXAMPLES}},
        },
        400: error_response(
            "Неверный период или source_type.",
            {
                "detail": "date_from не может быть позже date_to",
                "error_code": "RECEIPT_HISTORY_VALIDATION_ERROR",
            },
        ),
        422: error_response(
            "Query parameter не прошёл validation.",
            {
                "detail": [
                    {
                        "type": "date_from_datetime_parsing",
                        "loc": ["query", "date_from"],
                        "msg": "Input should be a valid date",
                        "input": "bad",
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
async def list_receipt_history(
    date_from: date = Query(
        ..., description="Первый включённый день Europe/Moscow.", examples=["2026-07-01"]
    ),
    date_to: date = Query(
        ..., description="Последний включённый день; период до 366 дней.", examples=["2026-07-31"]
    ),
    source_type: str
    | None = Query(
        None, description="legacy_revision или wms_snapshot_only.", examples=["legacy_revision"]
    ),
    guid: str
    | None = Query(None, description="Точное сравнение GUID.", examples=["document-guid"]),
    document_number: str
    | None = Query(None, description="Точный номер документа.", examples=["ПТУ-123"]),
    supplier_name: str
    | None = Query(None, description="Точное имя поставщика.", examples=["Поставщик"]),
    supplier_code: str
    | None = Query(None, description="Точный код поставщика.", examples=["SUP-1"]),
    event_status: str
    | None = Query(None, description="Точный event_status.", examples=["Проведён"]),
    author: str
    | None = Query(None, description="Точное author_of_the_change.", examples=["Иванов"]),
    order_guid: str | None = Query(None, description="Точный order_guid.", examples=["order-guid"]),
    product_id: str
    | None = Query(
        None,
        description="Включает целую revision/snapshot при наличии SKU; totals полные.",
        examples=["wild1825"],
    ),
    is_current: bool
    | None = Query(
        None, description="Legacy bool_or(is_valid); WMS-only всегда true.", examples=[True]
    ),
    include_undated: bool = Query(
        False, description="Включить undated legacy независимо от периода.", examples=[False]
    ),
    limit: int = Query(50, ge=1, le=100, description="Итоговых строк на странице.", examples=[50]),
    offset: int = Query(0, ge=0, description="Смещение по итоговым строкам.", examples=[0]),
    service: ReceiptHistoryService = Depends(get_receipt_history_service),
) -> ReceiptHistoryListResponse:
    return await service.list_history(
        date_from,
        date_to,
        source_type,
        guid,
        document_number,
        supplier_name,
        supplier_code,
        event_status,
        author,
        order_guid,
        product_id,
        is_current,
        include_undated,
        limit,
        offset,
    )


@router.get(
    "/{guid}/history",
    response_model=ReceiptHistoryResponse,
    summary="Получить историю документа поступления",
    operation_id="get_receipt_history",
    response_description="Legacy revisions и отдельный текущий WMS snapshot документа.",
    description="""Объединяет два независимых источника: legacy revisions из
`public.supply_to_sellers_warehouse` и текущий snapshot из `wms.receipt_items`.
`current_snapshot` не является revision. GUID — точная строка, не обязательно UUID.
Receipt movements не связываются по reason, времени или автору.

Revision группируется по точному `COALESCE(update_document_datetime,
document_created_at, supply_date)` без округления; naive timestamps считаются
`Europe/Moscow`. Строка без всех дат получает `receipt_revision:legacy:<id>`, header
выбирается по максимальному legacy ID. `is_current=true`, если в revision есть строка
`is_valid=true`. Пагинация применяется по revisions, не по товарам.

History без snapshot и snapshot без history возвращают 200; отсутствие обоих — 404.
Endpoint read-only и не использует movements.""",
    responses={
        200: {
            "description": "История и/или snapshot найдены.",
            "content": {"application/json": {"examples": RECEIPT_EXAMPLES}},
        },
        400: error_response(
            "Пустой GUID или domain validation limit/offset.",
            {
                "detail": "guid не может быть пустым",
                "error_code": "RECEIPT_HISTORY_VALIDATION_ERROR",
            },
        ),
        404: error_response(
            "GUID отсутствует в обоих источниках.",
            {
                "detail": "Документ поступления missing не найден",
                "error_code": "RECEIPT_HISTORY_NOT_FOUND",
            },
        ),
        422: error_response(
            "Path/query parameter не прошёл FastAPI validation.",
            {
                "detail": [
                    {
                        "type": "greater_than_equal",
                        "loc": ["query", "limit"],
                        "msg": "Input should be greater than or equal to 1",
                        "input": "0",
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
async def get_receipt_history(
    guid: str = Path(
        ...,
        min_length=1,
        description="Обязательный точный ID документа из 1С/legacy; не обязан быть UUID и URL-encode выполняет клиент.",
        examples=["6b298b5e-85fa-11f1-8502-50ebf6b2ce7c"],
    ),
    limit: int = Query(
        50,
        ge=1,
        le=100,
        description="Необязательное число revisions на странице, 1..100.",
        examples=[50],
    ),
    offset: int = Query(
        0, ge=0, description="Необязательное смещение по revisions документа.", examples=[0]
    ),
    service: ReceiptHistoryService = Depends(get_receipt_history_service),
) -> ReceiptHistoryResponse:
    return await service.get_history(guid, limit, offset)
