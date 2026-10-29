"""Read-only API дневной истории остатков."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_inventory_history_service
from app.core.schemas.inventory_history import DailyBalancesResponse, TurnoverResponse
from app.core.services.inventory_history_service import InventoryHistoryService
from app.api.v1.openapi_history import (
    DAILY_EXAMPLE,
    TURNOVER_BAD_REQUEST_EXAMPLES,
    TURNOVER_EXAMPLES,
    TURNOVER_VALIDATION_EXAMPLES,
    HistoryErrorResponse,
    ValidationErrorResponse,
    error_response,
)

router = APIRouter(prefix="/inventory-history", tags=["История WMS"])


@router.get(
    "/daily-turnover",
    response_model=TurnoverResponse,
    summary="Получить поступления и отгрузки всего склада по дням или месяцам",
    operation_id="get_inventory_daily_turnover",
    response_description="Все подходящие товары с поступлениями, отгрузками и итогами за период.",
    description="""Поступления и отгрузки единственного склада **PUSHKINO** по всему WMS,
суммарно по всем зонам, локациям, партиям и контейнерам.

- `received_quantity` — сумма количества движений строго типа `receive`.
- `shipped_quantity` — сумма количества движений строго типа `ship`.
- Внутренние перемещения, корректировки, комплектация и пересорт в эти показатели не входят.
- Каждое движение учитывается один раз. Эти показатели не описывают все изменения остатка;
  для начального и конечного остатка используйте `GET /api/inventory-history/daily-balances`.

**Выбор периода и товаров**

`date_from` и `date_to` обязательны, формат `YYYY-MM-DD`. Обе даты включены,
дни считаются в `Europe/Moscow`. Период — не более **366 календарных дней**.
`product_id` — необязательный точный ID товара. Без него возвращаются **все товары**,
у которых за период есть хотя бы одно `receive` или `ship`. Пагинации и параметров
`limit`/`offset` нет. Товар без таких движений, в том числе неизвестный ID,
даёт HTTP 200 с `total_products=0` и `items=[]`.

**Группировка и структура ответа**

| group_by | Массив у товара | Дата периода |
|---|---|---|
| `day` (по умолчанию) | `days` | `date`: календарный день |
| `month` | `months` | `month`: первое число месяца |

Выбирайте структуру по `group_by` в ответе. Каждый товар содержит `product_id`,
`product_name` (может быть `null`), `totals` за весь период и массив выбранной группировки.
Количества — JSON-числа, возможны дробные значения. `totals` равен сумме периодов товара
и одинаков при `day` и `month`. `total_products` равен длине `items`.
Товары отсортированы по `product_id ASC`, периоды — по возрастанию даты.
Все дни или месяцы подходящего товара возвращаются, включая периоды с нулями.

В месячном режиме крайние месяцы включают **только даты запроса**: для диапазона
15 января — 10 апреля январь учитывает 15–31 января, апрель — 1–10 апреля.
Метка января остаётся `YYYY-01-01`. Календарный год даёт 12 месяцев;
скользящий диапазон до 366 дней может затронуть 13 месяцев.

Для годового просмотра используйте `group_by=month`: дневной ответ растёт как число
товаров × число дней. Время ответа зависит от объёма движений и размера результата.

**Примеры запросов**

- Один товар по дням: `/api/inventory-history/daily-turnover?date_from=2026-07-01&date_to=2026-07-03&product_id=wild1825`.
- Все товары за год по месяцам: `/api/inventory-history/daily-turnover?date_from=2026-01-01&date_to=2026-12-31&group_by=month`.

Ошибки: **400** — обратный диапазон дат или период более 366 дней;
**422** — отсутствующая/невалидная дата либо `group_by`, отличный от `day`/`month`;
**500** — внутренняя ошибка. В примерах HTTP 200 ниже доступны оба формата ответа
и пустые результаты.""",
    responses={
        200: {
            "description": "Дневной или месячный ответ; пустой результат также HTTP 200.",
            "content": {"application/json": {"examples": TURNOVER_EXAMPLES}},
        },
        400: {
            "model": HistoryErrorResponse,
            "description": "Некорректный порядок дат или более 366 дней включительно.",
            "content": {"application/json": {"examples": TURNOVER_BAD_REQUEST_EXAMPLES}},
        },
        422: {
            "model": ValidationErrorResponse,
            "description": "Обязательные даты, их формат или group_by не прошли валидацию.",
            "content": {"application/json": {"examples": TURNOVER_VALIDATION_EXAMPLES}},
        },
        500: error_response(
            "Необработанная ошибка сервиса или БД.",
            {"detail": "Внутренняя ошибка сервера", "error_code": "INTERNAL_ERROR"},
        ),
    },
)
async def get_daily_turnover(
    date_from: date = Query(
        ..., description="Первый включённый московский день, YYYY-MM-DD.", examples=["2026-01-01"]
    ),
    date_to: date = Query(
        ...,
        description="Последний включённый московский день; период до 366 дней включительно.",
        examples=["2026-12-31"],
    ),
    product_id: str
    | None = Query(
        None,
        description="Точный ID товара. Без фильтра — все товары с receive/ship за период; "
        "без таких движений — пустой HTTP 200.",
        examples=["wild1825"],
    ),
    group_by: Literal["day", "month"] = Query(
        "day",
        description="day — days/date (по умолчанию); month — months/month. "
        "Для годового просмотра используйте month. Итоги за период одинаковы.",
        examples=["day", "month"],
    ),
    service: InventoryHistoryService = Depends(get_inventory_history_service),
) -> TurnoverResponse:
    return await service.get_daily_turnover(date_from, date_to, product_id, group_by)


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
