"""API endpoints для журнала отгрузок ФБС"""

import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from asyncpg import Pool
import asyncpg.exceptions
from pydantic import ValidationError

from app.core.schemas.fbs_shipment import (
    FbsShipmentListResponse,
    FbsShipmentListItem,
    FbsShipmentDetailResponse,
    FbsShipmentItemResponse,
    FbsShipmentStatsResponse,
    RetryRequest,
    RetryResponse,
    RetryResultItem,
)
from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.handlers.write_off_fbs_handler import (
    _calc_next_retry_at,
    _process_shipment_group,
    handle_write_off_fbs,
)
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.core.services.movement_service import MovementService
from app.core.enums import FbsShipmentSource
from app.core.exceptions import AssemblyTasksAlreadyProcessedError
from app.infrastructure.database.connection import get_db_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["FBS Shipments"])

_VALID_STATUSES = {"processing", "completed", "partially_completed", "failed", "validation_failed"}


def _parse_retry_raw_message(raw_message: Any) -> List[dict]:
    """Normalize raw_message from asyncpg/jsonb into a list of object dicts."""
    raw = raw_message
    for _ in range(2):
        if not isinstance(raw, str):
            break
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"raw_message должен быть JSON array: invalid JSON ({e.msg})") from e

    if not isinstance(raw, list):
        raise ValueError("raw_message должен быть JSON array")

    if not all(isinstance(item, dict) for item in raw):
        raise ValueError("raw_message elements must be objects")

    return raw


def _parse_retry_items(raw_message: Any) -> tuple[List[dict], List[WriteOffAccordingToFBS]]:
    parsed_raw = _parse_retry_raw_message(raw_message)
    items = [WriteOffAccordingToFBS(**item) for item in parsed_raw]
    return parsed_raw, items


# ─────────────────────────────────────────────
#  GET /fbs-shipments/stats  (должен быть ДО /{shipment_id})
# ─────────────────────────────────────────────


@router.get(
    "/stats",
    response_model=FbsShipmentStatsResponse,
    summary="Статистика отгрузок по статусам",
    description="""
Возвращает сводную статистику по всем записям журнала отгрузок ФБС, сгруппированную по статусам.

**Возможные статусы:**
- `processing` — обработка в процессе, есть позиции ожидающие retry
- `completed` — все позиции успешно списаны
- `partially_completed` — часть позиций списана, часть завершилась ошибкой
- `failed` — все позиции завершились ошибкой
- `validation_failed` — сообщение не прошло валидацию Pydantic-схемы, позиции не создавались
""",
    response_description="Общее количество и разбивка по статусам",
)
async def get_shipments_stats(
    source: Optional[FbsShipmentSource] = Query(None, description="Фильтр по источнику"),
    pool: Pool = Depends(get_db_pool),
):
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        rows = await repo.get_shipments_stats(conn, source.value if source else None)

    by_status: dict[str, int] = {row["status"]: row["count"] for row in rows}
    total = sum(by_status.values())
    return FbsShipmentStatsResponse(total=total, by_status=by_status)


# ─────────────────────────────────────────────
#  GET /fbs-shipments  (должен быть ДО /{shipment_id})
# ─────────────────────────────────────────────


@router.get(
    "",
    response_model=FbsShipmentListResponse,
    summary="Список отгрузок ФБС",
    description="""
Возвращает список записей журнала отгрузок из RabbitMQ с пагинацией и фильтрацией.

Каждая запись соответствует одному сообщению из очереди `orders.delivered.fbs.first.aggregated`.
Сообщение может содержать несколько позиций (items) — товаров для списания.

**Фильтры:**
- `status` — фильтрация по статусу обработки
- `date_from` / `date_to` — фильтрация по дате получения сообщения

Поле `raw_message` (сырой JSON из RabbitMQ) не включается в список — доступно через GET по конкретному `shipment_id`.

Сортировка: по дате получения, новые первыми.
""",
    response_description="Список отгрузок с пагинацией",
)
async def list_shipments(
    status: Optional[str] = Query(
        None,
        description="Фильтр по статусу: processing, completed, partially_completed, failed, validation_failed",
    ),
    source: Optional[FbsShipmentSource] = Query(None, description="Фильтр по источнику"),
    limit: int = Query(50, ge=1, le=200, description="Количество записей на страницу"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    date_from: Optional[datetime] = Query(
        None, description="Начало периода (received_at >= date_from)"
    ),
    date_to: Optional[datetime] = Query(None, description="Конец периода (received_at <= date_to)"),
    pool: Pool = Depends(get_db_pool),
):
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Недопустимый статус. Допустимые: {sorted(_VALID_STATUSES)}",
        )

    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        rows, total = await repo.get_shipments(
            conn,
            status=status,
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            source=source.value if source else None,
        )

    items = [FbsShipmentListItem(**dict(r)) for r in rows]
    return FbsShipmentListResponse(total=total, limit=limit, offset=offset, items=items)


# ─────────────────────────────────────────────
#  POST /fbs-shipments/items/{item_id}/retry
# ─────────────────────────────────────────────


@router.post(
    "/items/{item_id}/retry",
    response_model=FbsShipmentDetailResponse,
    summary="Повторная обработка одной FBS-позиции",
)
async def retry_shipment_item(item_id: int, pool: Pool = Depends(get_db_pool)):
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        item = await repo.get_item_by_id(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="FBS item не найден")
    if item["status"] not in {"failed", "pending_retry", "retry_exhausted"}:
        raise HTTPException(
            status_code=409, detail="Item со статусом {} нельзя повторить".format(item["status"])
        )

    movement_service = MovementService(MovementRepository(pool), LocationRepository(pool))
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _process_shipment_group(
                    conn=conn,
                    product_id=item["product_id"],
                    total_quantity=item["quantity"],
                    all_assembly_tasks=list(item["assembly_tasks"]),
                    author=item["author"],
                    movement_service=movement_service,
                    shipment_repo=repo,
                    item_ids=[item_id],
                    retry_count=item["retry_count"] + 1,
                )
    except AssemblyTasksAlreadyProcessedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except asyncpg.exceptions.CheckViolationError as e:
        retry_count = item["retry_count"] + 1
        status = "retry_exhausted" if retry_count >= item["max_retries"] else "pending_retry"
        next_retry_at = None if status == "retry_exhausted" else _calc_next_retry_at(retry_count)
        async with pool.acquire() as conn:
            await repo.update_item_status(
                conn,
                item_id=item_id,
                status=status,
                error_message=str(e),
                retry_count=retry_count,
                next_retry_at=next_retry_at,
            )
    except Exception as e:
        logger.error(f"Ошибка ручного retry item_id={item_id}: {e}", exc_info=True)
        async with pool.acquire() as conn:
            await repo.update_item_status(
                conn, item_id=item_id, status="failed", error_message=str(e)
            )

    async with pool.acquire() as conn:
        await repo.update_shipment_status(conn, item["shipment_id"])
        shipment = await repo.get_shipment_by_id(conn, item["shipment_id"])
        item_rows = await repo.get_items_by_shipment_id(conn, item["shipment_id"])
    return FbsShipmentDetailResponse(
        **dict(shipment), items=[FbsShipmentItemResponse(**dict(row)) for row in item_rows]
    )


# ─────────────────────────────────────────────
#  GET /fbs-shipments/{shipment_id}
# ─────────────────────────────────────────────


@router.get(
    "/{shipment_id}",
    response_model=FbsShipmentDetailResponse,
    summary="Детали отгрузки",
    description="""
Возвращает полную информацию об одной записи журнала отгрузок, включая:

- **raw_message** — исходный JSON из RabbitMQ (для отладки и анализа)
- **items** — список позиций с индивидуальными статусами обработки

Для записей со статусом `validation_failed` список items будет пустым — позиции не создавались из-за ошибки валидации. Исходные данные доступны в `raw_message`.

**Статусы позиций (items):**
- `new` — получена, ожидает обработки
- `success` — списание выполнено, `movement_id` заполнен
- `failed` — невосстановимая ошибка, описание в `error_message`
- `pending_retry` — ожидает повторной попытки (недостаток остатка на локации ФБС)
- `retry_exhausted` — все попытки исчерпаны (по умолчанию 5)
""",
    response_description="Полная информация об отгрузке с позициями",
    responses={
        404: {"description": "Отгрузка с указанным ID не найдена"},
    },
)
async def get_shipment(
    shipment_id: int,
    pool: Pool = Depends(get_db_pool),
):
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        row = await repo.get_shipment_by_id(conn, shipment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Shipment не найден")
        item_rows = await repo.get_items_by_shipment_id(conn, shipment_id)

    items = [FbsShipmentItemResponse(**dict(r)) for r in item_rows]
    return FbsShipmentDetailResponse(**dict(row), items=items)


# ─────────────────────────────────────────────
#  POST /fbs-shipments/retry  (должен быть ДО /{shipment_id})
# ─────────────────────────────────────────────


@router.post(
    "/retry",
    response_model=RetryResponse,
    summary="Массовая переобработка отгрузок",
    description="""
Повторно обрабатывает записи со статусом `validation_failed`.

**Сценарии использования:**
- После исправления бага в схеме валидации — переобработать все ранее отклонённые записи
- Выборочная переобработка конкретных shipment по ID

**Логика:**
1. Берёт `raw_message` из БД
2. Парсит через обновлённую Pydantic-схему `WriteOffAccordingToFBS`
3. При успешном парсинге — создаёт позиции (items) и пытается выполнить списание
4. При повторной ошибке парсинга — обновляет `error_message`, статус остаётся `validation_failed`

**Защита от дублей:** если shipment уже имеет статус отличный от `validation_failed` — он пропускается.

**Тело запроса:**
- `shipment_ids: [1, 2, 3]` — переобработать конкретные записи
- `shipment_ids: []` или не указан — переобработать ВСЕ записи со статусом `validation_failed`

⚠️ При большом количестве записей (600+) запрос может выполняться долго.
""",
    response_description="Результат переобработки: сколько успешно, сколько с ошибками",
)
async def retry_shipments(
    body: RetryRequest = RetryRequest(),
    pool: Pool = Depends(get_db_pool),
):
    repo = FbsShipmentRepository()

    async with pool.acquire() as conn:
        if body.shipment_ids:
            rows = await repo.get_shipments_by_status(conn, "validation_failed")
            requested_ids = set(body.shipment_ids)
            rows = [r for r in rows if r["shipment_id"] in requested_ids]
        else:
            rows = await repo.get_shipments_by_status(conn, "validation_failed")

    results: List[RetryResultItem] = []
    processed = 0

    for row in rows:
        shipment_id: int = row["shipment_id"]
        raw = row["raw_message"]

        try:
            parsed_raw, items = _parse_retry_items(raw)
        except (ValidationError, Exception) as e:
            error_str = str(e)
            logger.warning(f"Повторная ошибка валидации | shipment_id={shipment_id} | {error_str}")
            async with pool.acquire() as conn:
                await repo.update_shipment_error(conn, shipment_id, error_str)
            results.append(
                RetryResultItem(
                    shipment_id=shipment_id,
                    status="validation_failed",
                    error=error_str,
                )
            )
            continue

        try:
            await handle_write_off_fbs(
                items, pool, raw_message=parsed_raw, shipment_id=shipment_id
            )
            processed += 1
            async with pool.acquire() as conn:
                record = await repo.get_shipment_by_id(conn, shipment_id)
            final_status = record["status"] if record else "unknown"
            results.append(RetryResultItem(shipment_id=shipment_id, status=final_status))
        except Exception as e:
            error_str = str(e)
            logger.error(
                f"Ошибка обработки | shipment_id={shipment_id} | {error_str}", exc_info=True
            )
            results.append(
                RetryResultItem(
                    shipment_id=shipment_id,
                    status="failed",
                    error=error_str,
                )
            )

    still_failed = sum(1 for r in results if r.status == "validation_failed")

    return RetryResponse(
        total_requested=len(rows),
        processed=processed,
        still_failed=still_failed,
        results=results,
    )


# ─────────────────────────────────────────────
#  POST /fbs-shipments/{shipment_id}/retry
# ─────────────────────────────────────────────


@router.post(
    "/{shipment_id}/retry",
    response_model=RetryResultItem,
    summary="Переобработка одной отгрузки",
    description="""
Повторно обрабатывает одну конкретную запись журнала.

Работает для записей со статусом `validation_failed`:
1. Берёт `raw_message` из БД
2. Парсит через Pydantic-схему
3. Создаёт позиции и пытается выполнить списание

**Защита от дублей:** проверка `assembly_tasks` через таблицу `public.assembly_task` — если задания уже отмечены как отгруженные, повторное списание не произойдёт.
""",
    response_description="Результат переобработки одной записи",
    responses={
        404: {"description": "Отгрузка с указанным ID не найдена"},
        400: {"description": "Отгрузка не может быть переобработана (статус не validation_failed)"},
    },
)
async def retry_shipment(
    shipment_id: int,
    pool: Pool = Depends(get_db_pool),
):
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        row = await repo.get_shipment_by_id(conn, shipment_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Shipment не найден")

    if row["status"] != "validation_failed":
        return RetryResultItem(
            shipment_id=shipment_id,
            status=row["status"],
            error="Статус не validation_failed — переобработка не нужна",
        )

    raw = row["raw_message"]

    try:
        parsed_raw, items = _parse_retry_items(raw)
    except (ValidationError, Exception) as e:
        error_str = str(e)
        async with pool.acquire() as conn:
            await repo.update_shipment_error(conn, shipment_id, error_str)
        return RetryResultItem(
            shipment_id=shipment_id,
            status="validation_failed",
            error=error_str,
        )

    try:
        await handle_write_off_fbs(
            items, pool, raw_message=parsed_raw, shipment_id=shipment_id
        )
    except Exception as e:
        return RetryResultItem(
            shipment_id=shipment_id,
            status="failed",
            error=str(e),
        )

    async with pool.acquire() as conn:
        updated = await repo.get_shipment_by_id(conn, shipment_id)

    return RetryResultItem(
        shipment_id=shipment_id,
        status=updated["status"] if updated else "unknown",
    )
