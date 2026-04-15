"""Handler для обработки списания из ФБС зоны"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import asyncpg.exceptions
from asyncpg import Connection, Pool

from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.core.schemas.movement import MovementCreate
from app.core.enums import MovementType
from app.core.services.movement_service import MovementService
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.shared.config import settings

logger = logging.getLogger(__name__)

# Экспоненциальный backoff для retry: базовый шаг 5 минут, максимум 30 минут
_RETRY_BASE_MINUTES = 5
_RETRY_MAX_MINUTES = 30

VALIDATE_ASSEMBLY_TASKS = """
SELECT task_id, is_shipped
FROM public.assembly_task
WHERE task_id = ANY($1::bigint[])
"""

MARK_ASSEMBLY_TASKS_SHIPPED = """
UPDATE public.assembly_task
SET is_shipped = TRUE
WHERE task_id = ANY($1::bigint[]) AND is_shipped = FALSE
"""


class AssemblyTaskValidationError(Exception):
    pass


def _calc_next_retry_at(retry_count: int) -> datetime:
    """Экспоненциальный backoff, максимум 30 минут."""
    minutes = min(_RETRY_BASE_MINUTES * (2 ** retry_count), _RETRY_MAX_MINUTES)
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def validate_assembly_tasks(
    assembly_tasks: List[str],
    conn: Connection,
) -> None:
    """
    Проверяет сборочные задания и помечает их как отгруженные.

    Выполняется внутри транзакции вместе с основной операцией списания.
    При откате транзакции is_shipped возвращается в false — атомарность гарантирована.
    Бросает AssemblyTaskValidationError если:
    - часть task_id не существует в БД
    - часть task_id уже имеет is_shipped = TRUE
    """
    try:
        task_ids = [int(t) for t in assembly_tasks]
    except ValueError as e:
        raise ValueError(f"assembly_tasks содержит нечисловые значения: {e}")

    rows = await conn.fetch(VALIDATE_ASSEMBLY_TASKS, task_ids)

    found_ids = {row["task_id"] for row in rows}
    non_existing_ids = set(task_ids) - found_ids
    already_shipped = {row["task_id"] for row in rows if row["is_shipped"]}

    if non_existing_ids:
        logger.error(f"Сборочные задания не найдены в БД: {sorted(non_existing_ids)}")
        raise AssemblyTaskValidationError(
            f"Задания не найдены: {sorted(non_existing_ids)}"
        )

    if already_shipped:
        logger.error(f"Сборочные задания уже отгружены: {sorted(already_shipped)}")
        raise AssemblyTaskValidationError(
            f"Задания уже отгружены: {sorted(already_shipped)}"
        )

    await conn.execute(MARK_ASSEMBLY_TASKS_SHIPPED, task_ids)
    logger.info(f"Сборочные задания помечены как отгруженные: {sorted(task_ids)}")


async def _process_shipment_group(
    conn: Connection,
    product_id: str,
    total_quantity: int,
    all_assembly_tasks: List[str],
    author: str,
    movement_service: MovementService,
) -> int:
    """
    Выполняет списание для одной группы (по product_id).

    Должна вызываться внутри открытой транзакции — тогда при
    CheckViolationError откатываются и validate_assembly_tasks,
    и create_movement атомарно (is_shipped вернётся в false).

    Returns:
        movement_id созданного движения.

    Raises:
        asyncpg.exceptions.CheckViolationError: нехватка остатка.
        AssemblyTaskValidationError: задания не найдены или уже отгружены.
    """
    await validate_assembly_tasks(all_assembly_tasks, conn)

    movement = MovementCreate(
        movement_type=MovementType.SHIP,
        product_id=product_id,
        quantity=total_quantity,
        user_name=author,
        from_location_code=settings.FBS_LOCATION_CODE,
        reason=f"Списание из ФБС зоны. Сборочные задания: {all_assembly_tasks}",
    )
    created = await movement_service.create_movement_in_transaction(conn, [movement])
    return created[0].movement_id


def _group_items(
    items: List[WriteOffAccordingToFBS],
) -> List[WriteOffAccordingToFBS]:
    """
    Группирует items по product_id: суммирует quantity, объединяет assembly_tasks.
    Author берётся от первого встреченного объекта с данным product_id.
    """
    grouped: dict[str, WriteOffAccordingToFBS] = {}
    for item in items:
        if item.product_id not in grouped:
            grouped[item.product_id] = item.model_copy(deep=True)
        else:
            acc = grouped[item.product_id]
            grouped[item.product_id] = acc.model_copy(update={
                "quantity": acc.quantity + item.quantity,
                "assembly_tasks": acc.assembly_tasks + item.assembly_tasks,
            })
    return list(grouped.values())


def _items_to_dicts(items: List[WriteOffAccordingToFBS]) -> List[dict]:
    """Конвертирует список схем в список dict для репозитория."""
    result = []
    for item in items:
        d = item.model_dump()
        # shipment_date в схеме — date, в БД — TIMESTAMPTZ
        if d.get("shipment_date") is not None:
            d["shipment_date"] = datetime.combine(
                d["shipment_date"],
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        result.append(d)
    return result


async def handle_write_off_fbs(
    items: List[WriteOffAccordingToFBS],
    pool: Pool,
    raw_message: Optional[dict] = None,
) -> int:
    """
    Обрабатывает список объектов списания из ФБС зоны.

    Шаги:
    1. Сохранить shipment + items в БД (транзакция 1) — данные зафиксированы, ACK отправляется
    2. Для каждой группы product_id — попытка списания (отдельная транзакция):
       - Успех          → status=success, movement_id заполнен
       - CheckViolation → status=pending_retry, next_retry_at заполнен
       - Другая ошибка  → status=failed, error_message заполнен
    3. Пересчитать статус shipment

    Returns:
        shipment_id созданного журнала
    """
    shipment_repo = FbsShipmentRepository()
    movement_repo = MovementRepository(pool)
    location_repo = LocationRepository(pool)
    movement_service = MovementService(movement_repo, location_repo)

    grouped_items = _group_items(items)

    if len(grouped_items) < len(items):
        logger.info(
            f"Группировка: {len(items)} позиций → {len(grouped_items)} уникальных product_id"
        )

    # --- Транзакция 1: сохраняем журнал ---
    async with pool.acquire() as conn:
        async with conn.transaction():
            shipment_id = await shipment_repo.create_shipment(
                conn,
                raw_message=raw_message if raw_message is not None else [],
                total_items=len(items),
            )
            item_ids = await shipment_repo.create_shipment_items(
                conn,
                shipment_id=shipment_id,
                items=_items_to_dicts(items),
            )

    logger.info(f"Shipment сохранён | shipment_id={shipment_id} | items={len(item_ids)}")

    # Маппинг product_id → list[item_id] для обновления статусов
    product_to_item_ids: dict[str, List[int]] = {}
    for item, item_id in zip(items, item_ids):
        product_to_item_ids.setdefault(item.product_id, []).append(item_id)

    # --- Транзакции 2..N: списание каждой группы ---
    for group in grouped_items:
        related_item_ids = product_to_item_ids.get(group.product_id, [])
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # validate_assembly_tasks + create_movement — одна транзакция.
                    # CheckViolationError откатит оба, is_shipped вернётся в false.
                    movement_id = await _process_shipment_group(
                        conn=conn,
                        product_id=group.product_id,
                        total_quantity=group.quantity,
                        all_assembly_tasks=group.assembly_tasks,
                        author=group.author,
                        movement_service=movement_service,
                    )

            logger.info(
                f"Списание выполнено | product_id={group.product_id} | "
                f"qty={group.quantity} | movement_id={movement_id}"
            )

            async with pool.acquire() as conn:
                for item_id in related_item_ids:
                    await shipment_repo.update_item_status(
                        conn,
                        item_id=item_id,
                        status="success",
                        movement_id=movement_id,
                    )

        except asyncpg.exceptions.CheckViolationError as e:
            retry_count = 0  # первая попытка
            next_retry_at = _calc_next_retry_at(retry_count)
            logger.warning(
                f"Недостаток остатка | product_id={group.product_id} | "
                f"next_retry_at={next_retry_at.isoformat()} | error={e}"
            )
            async with pool.acquire() as conn:
                for item_id in related_item_ids:
                    await shipment_repo.update_item_status(
                        conn,
                        item_id=item_id,
                        status="pending_retry",
                        error_message=str(e),
                        retry_count=retry_count,
                        next_retry_at=next_retry_at,
                    )

        except Exception as e:
            logger.error(
                f"Ошибка списания | product_id={group.product_id} | error={e}",
                exc_info=True,
            )
            async with pool.acquire() as conn:
                for item_id in related_item_ids:
                    await shipment_repo.update_item_status(
                        conn,
                        item_id=item_id,
                        status="failed",
                        error_message=str(e),
                    )

    # --- Итог: пересчитать статус shipment ---
    async with pool.acquire() as conn:
        await shipment_repo.update_shipment_status(conn, shipment_id)

    logger.info(f"Обработка завершена | shipment_id={shipment_id}")
    return shipment_id
