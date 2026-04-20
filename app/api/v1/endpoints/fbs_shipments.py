"""API endpoints для журнала отгрузок ФБС"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from asyncpg import Pool
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
from app.handlers.write_off_fbs_handler import handle_write_off_fbs
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.infrastructure.database.connection import get_db_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["FBS Shipments"])

_VALID_STATUSES = {"processing", "completed", "partially_completed", "failed", "validation_failed"}


# ─────────────────────────────────────────────
#  GET /fbs-shipments/stats  (должен быть ДО /{shipment_id})
# ─────────────────────────────────────────────

@router.get("/stats", response_model=FbsShipmentStatsResponse)
async def get_shipments_stats(
    pool: Pool = Depends(get_db_pool),
):
    """Сводная статистика по статусам — один GROUP BY запрос."""
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        rows = await repo.get_shipments_stats(conn)

    by_status: dict[str, int] = {row["status"]: row["count"] for row in rows}
    total = sum(by_status.values())
    return FbsShipmentStatsResponse(total=total, by_status=by_status)


# ─────────────────────────────────────────────
#  POST /fbs-shipments/retry  (должен быть ДО /{shipment_id})
# ─────────────────────────────────────────────

@router.post("/retry", response_model=RetryResponse)
async def retry_shipments(
    body: RetryRequest = RetryRequest(),
    pool: Pool = Depends(get_db_pool),
):
    """
    Массовая переобработка validation_failed записей.

    Если `shipment_ids` пуст — переобрабатывает **все** записи со статусом `validation_failed`.
    Один упавший shipment не блокирует остальные.
    """
    repo = FbsShipmentRepository()

    async with pool.acquire() as conn:
        if body.shipment_ids:
            # Загружаем только запрошенные, но только если они validation_failed
            rows = await repo.get_shipments_by_status(conn, "validation_failed")
            requested_ids = set(body.shipment_ids)
            rows = [r for r in rows if r["shipment_id"] in requested_ids]
        else:
            rows = await repo.get_shipments_by_status(conn, "validation_failed")

    results: List[RetryResultItem] = []
    processed = 0

    for row in rows:
        shipment_id: int = row["shipment_id"]
        raw = row["raw_message"]  # asyncpg возвращает JSONB как Python-объект

        # Парсинг через исправленный model_validator
        try:
            items: List[WriteOffAccordingToFBS] = [
                WriteOffAccordingToFBS(**i) for i in raw
            ]
        except (ValidationError, Exception) as e:
            error_str = str(e)
            logger.warning(f"Повторная ошибка валидации | shipment_id={shipment_id} | {error_str}")
            async with pool.acquire() as conn:
                await repo.update_shipment_error(conn, shipment_id, error_str)
            results.append(RetryResultItem(
                shipment_id=shipment_id,
                status="validation_failed",
                error=error_str,
            ))
            continue

        # Обработка: handler обновит статус shipment через update_shipment_status
        try:
            await handle_write_off_fbs(items, pool, raw_message=raw, shipment_id=shipment_id)
            processed += 1
            # Узнаём финальный статус из БД
            async with pool.acquire() as conn:
                record = await repo.get_shipment_by_id(conn, shipment_id)
            final_status = record["status"] if record else "unknown"
            results.append(RetryResultItem(shipment_id=shipment_id, status=final_status))
        except Exception as e:
            error_str = str(e)
            logger.error(f"Ошибка обработки | shipment_id={shipment_id} | {error_str}", exc_info=True)
            results.append(RetryResultItem(
                shipment_id=shipment_id,
                status="failed",
                error=error_str,
            ))

    still_failed = sum(1 for r in results if r.status == "validation_failed")

    return RetryResponse(
        total_requested=len(rows),
        processed=processed,
        still_failed=still_failed,
        results=results,
    )


# ─────────────────────────────────────────────
#  GET /fbs-shipments
# ─────────────────────────────────────────────

@router.get("", response_model=FbsShipmentListResponse)
async def list_shipments(
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pool: Pool = Depends(get_db_pool),
):
    """
    Список shipments с пагинацией и фильтрацией.

    Не возвращает `raw_message` и вложенные `items` — для деталей используйте
    `GET /fbs-shipments/{shipment_id}`.
    """
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
        )

    items = [FbsShipmentListItem(**dict(r)) for r in rows]
    return FbsShipmentListResponse(total=total, limit=limit, offset=offset, items=items)


# ─────────────────────────────────────────────
#  GET /fbs-shipments/{shipment_id}
# ─────────────────────────────────────────────

@router.get("/{shipment_id}", response_model=FbsShipmentDetailResponse)
async def get_shipment(
    shipment_id: int,
    pool: Pool = Depends(get_db_pool),
):
    """
    Детали одного shipment: raw_message + все items.

    Для `validation_failed` items будут пустые — они не создавались.
    """
    repo = FbsShipmentRepository()
    async with pool.acquire() as conn:
        row = await repo.get_shipment_by_id(conn, shipment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Shipment не найден")
        item_rows = await repo.get_items_by_shipment_id(conn, shipment_id)

    items = [FbsShipmentItemResponse(**dict(r)) for r in item_rows]
    return FbsShipmentDetailResponse(**dict(row), items=items)


# ─────────────────────────────────────────────
#  POST /fbs-shipments/{shipment_id}/retry
# ─────────────────────────────────────────────

@router.post("/{shipment_id}/retry", response_model=RetryResultItem)
async def retry_shipment(
    shipment_id: int,
    pool: Pool = Depends(get_db_pool),
):
    """Переобработка одного конкретного shipment со статусом `validation_failed`."""
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
        items: List[WriteOffAccordingToFBS] = [
            WriteOffAccordingToFBS(**i) for i in raw
        ]
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
        await handle_write_off_fbs(items, pool, raw_message=raw, shipment_id=shipment_id)
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
