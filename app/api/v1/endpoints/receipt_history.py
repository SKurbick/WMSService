"""Read-only история документа поступления."""

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import get_receipt_history_service
from app.core.schemas.receipt_history import ReceiptHistoryResponse
from app.core.services.receipt_history_service import ReceiptHistoryService
from app.api.v1.openapi_history import RECEIPT_EXAMPLES, error_response

router = APIRouter(prefix="/receipts", tags=["История WMS"])


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
