"""Фоновый воркер для повторной обработки pending_retry позиций"""

import asyncio
import logging
from collections import defaultdict
from typing import List

import asyncpg.exceptions

from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.core.services.movement_service import MovementService
from app.handlers.write_off_fbs_handler import (
    _calc_next_retry_at,
    _process_shipment_group,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 300  # 5 минут


async def process_pending_retries(pool) -> None:
    """
    Подбирает все pending_retry записи с истёкшим next_retry_at
    и пытается выполнить списание повторно.

    Группирует записи по shipment_id, внутри — по product_id.
    После обработки каждого shipment пересчитывает его статус.
    """
    shipment_repo = FbsShipmentRepository()
    movement_repo = MovementRepository(pool)
    location_repo = LocationRepository(pool)
    movement_service = MovementService(movement_repo, location_repo)

    async with pool.acquire() as conn:
        rows = await shipment_repo.get_pending_retry_items(conn)

    if not rows:
        logger.debug("Нет pending_retry записей для обработки")
        return

    logger.info(f"Retry worker: найдено {len(rows)} pending_retry записей")

    # Группируем по shipment_id → product_id → list[row]
    by_shipment: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_shipment[row["shipment_id"]][row["product_id"]].append(row)

    for shipment_id, by_product in by_shipment.items():
        logger.info(f"Retry | shipment_id={shipment_id} | " f"групп product_id={len(by_product)}")

        for product_id, group_rows in by_product.items():
            # Берём данные из первой записи группы
            first = group_rows[0]
            retry_count: int = first["retry_count"]
            max_retries: int = first["max_retries"]
            item_ids: List[int] = [r["item_id"] for r in group_rows]

            # Собираем суммарное количество и все assembly_tasks группы
            total_quantity: int = sum(r["quantity"] for r in group_rows)
            all_assembly_tasks: List[str] = []
            for r in group_rows:
                all_assembly_tasks.extend(r["assembly_tasks"])

            new_retry_count = retry_count + 1

            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        # validate_assembly_tasks + create_movement — одна транзакция.
                        # CheckViolationError откатит оба, is_shipped вернётся в false.
                        movement_id = await _process_shipment_group(
                            conn=conn,
                            product_id=product_id,
                            total_quantity=total_quantity,
                            all_assembly_tasks=all_assembly_tasks,
                            author=first["author"],
                            movement_service=movement_service,
                            shipment_repo=shipment_repo,
                            item_ids=item_ids,
                            retry_count=new_retry_count,
                        )

                logger.info(
                    f"Retry успех | shipment_id={shipment_id} | product_id={product_id} | "
                    f"movement_id={movement_id} | попытка={new_retry_count}"
                )

            except asyncpg.exceptions.CheckViolationError as e:
                if new_retry_count >= max_retries:
                    logger.error(
                        f"Retry исчерпаны | shipment_id={shipment_id} | "
                        f"product_id={product_id} | попытка={new_retry_count}/{max_retries}"
                    )
                    async with pool.acquire() as conn:
                        for item_id in item_ids:
                            await shipment_repo.update_item_status(
                                conn,
                                item_id=item_id,
                                status="retry_exhausted",
                                error_message="Исчерпаны попытки. Недостаточно остатка.",
                                retry_count=new_retry_count,
                            )
                else:
                    next_retry_at = _calc_next_retry_at(new_retry_count)
                    logger.warning(
                        f"Retry отложен | shipment_id={shipment_id} | "
                        f"product_id={product_id} | попытка={new_retry_count}/{max_retries} | "
                        f"next_retry_at={next_retry_at.isoformat()} | error={e}"
                    )
                    async with pool.acquire() as conn:
                        for item_id in item_ids:
                            await shipment_repo.update_item_status(
                                conn,
                                item_id=item_id,
                                status="pending_retry",
                                error_message=str(e),
                                retry_count=new_retry_count,
                                next_retry_at=next_retry_at,
                            )

            except Exception as e:
                logger.error(
                    f"Retry ошибка | shipment_id={shipment_id} | "
                    f"product_id={product_id} | error={e}",
                    exc_info=True,
                )
                async with pool.acquire() as conn:
                    for item_id in item_ids:
                        await shipment_repo.update_item_status(
                            conn,
                            item_id=item_id,
                            status="failed",
                            error_message=str(e),
                            retry_count=new_retry_count,
                        )

        # Пересчитать статус shipment после обработки всех его групп
        async with pool.acquire() as conn:
            await shipment_repo.update_shipment_status(conn, shipment_id)

        logger.info(f"Retry | shipment_id={shipment_id} | статус обновлён")


async def start_retry_worker() -> None:
    """
    Фоновая задача: каждые 5 минут проверяет pending_retry записи.
    Не падает при ошибках — логирует и продолжает работу.
    """
    logger.info("Retry worker запущен, интервал опроса: 5 минут")
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            pool = await get_db_pool()
            await process_pending_retries(pool)
        except Exception as e:
            logger.error(f"Retry worker ошибка: {e}", exc_info=True)
