"""Read-only API дневной истории остатков."""

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_inventory_history_service
from app.core.schemas.inventory_history import DailyBalancesResponse
from app.core.services.inventory_history_service import InventoryHistoryService
from app.api.v1.openapi_history import DAILY_EXAMPLE, error_response

router = APIRouter(prefix="/inventory-history", tags=["История WMS"])


@router.get(
    "/daily-balances",
    response_model=DailyBalancesResponse,
    summary="Получить дневную историю остатков",
    operation_id="get_inventory_daily_balances",
    response_description="Дневные остатки выбранных товаров за полный календарный период.",
    description="""Восстанавливает физический available-остаток только из `wms.movements`:
`to_location_id` — приход, `from_location_id` — расход. Даты включительны и считаются в
`Europe/Moscow`; дни без операций также возвращаются. Opening первого дня включает все
movements до периода. Партии, контейнеры и loose-остатки агрегируются до товара.

`closing_quantity = opening_quantity + incoming_quantity - outgoing_quantity`.

Пагинация применяется по товарам, для каждого товара возвращается весь диапазон дней.
Endpoint read-only и не использует snapshots или текущий inventory для расчёта.""",
    responses={
        200: {
            "description": "История успешно рассчитана.",
            "content": {
                "application/json": {
                    "examples": {
                        "chain": {
                            "summary": "Поступление, пустой день и отгрузка",
                            "value": DAILY_EXAMPLE,
                        }
                    }
                }
            },
        },
        400: error_response(
            "Некорректный диапазон дат или include_subtree без location_id.",
            {
                "detail": "date_from не может быть позже date_to",
                "message": "date_from не может быть позже date_to",
                "error_code": "INVENTORY_HISTORY_VALIDATION_ERROR",
            },
        ),
        404: error_response(
            "Указанная WMS-локация не найдена.",
            {"detail": "Локация с ID 999 не найдена", "error_code": "LOCATION_NOT_FOUND"},
        ),
        422: error_response(
            "Тип query parameter не прошёл FastAPI validation.",
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
async def get_daily_balances(
    date_from: date = Query(
        ...,
        description="Обязательный первый календарный день Europe/Moscow; включается в результат.",
        examples=["2026-07-01"],
    ),
    date_to: date = Query(
        ...,
        description="Обязательный последний календарный день Europe/Moscow; включается. Период не более 366 дней.",
        examples=["2026-07-03"],
    ),
    product_id: str
    | None = Query(None, description="Необязательный точный ID товара.", examples=["wild1825"]),
    location_id: int
    | None = Query(
        None,
        description="Необязательная WMS-локация; без subtree используется точное совпадение.",
        examples=[123],
    ),
    include_subtree: bool = Query(
        False,
        description="При true включает location и потомков; допустим только с location_id.",
        examples=[False],
    ),
    limit: int = Query(
        100,
        ge=1,
        le=500,
        description="Товаров на странице; для товара возвращаются все дни.",
        examples=[100],
    ),
    offset: int = Query(
        0, ge=0, description="Смещение по товарам, не по дневным строкам.", examples=[0]
    ),
    service: InventoryHistoryService = Depends(get_inventory_history_service),
) -> DailyBalancesResponse:
    return await service.get_daily_balances(
        date_from=date_from,
        date_to=date_to,
        product_id=product_id,
        location_id=location_id,
        include_subtree=include_subtree,
        limit=limit,
        offset=offset,
    )
