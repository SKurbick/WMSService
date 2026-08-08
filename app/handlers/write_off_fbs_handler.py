"""Handler для обработки списания из ФБС зоны"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Sequence

import asyncpg.exceptions
from asyncpg import Connection, Pool

from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.core.schemas.movement import MovementCreate
from app.core.enums import FbsShipmentSource, MovementType
from app.core.services.movement_service import MovementService
from app.core.exceptions import (
    AssemblyTasksAlreadyProcessedError,
    FbsShipmentItemsUpdateError,
    InconsistentFbsShipmentError,
)
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
FOR UPDATE
"""

MARK_ASSEMBLY_TASKS_SHIPPED = """
UPDATE public.assembly_task
SET is_shipped = TRUE
WHERE task_id = ANY($1::bigint[]) AND is_shipped = FALSE
RETURNING task_id
"""


class AssemblyTaskValidationError(Exception):
    pass


def _calc_next_retry_at(retry_count: int) -> datetime:
    """Экспоненциальный backoff, максимум 30 минут."""
    minutes = min(_RETRY_BASE_MINUTES * (2**retry_count), _RETRY_MAX_MINUTES)
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

    if len(task_ids) != len(set(task_ids)):
        raise AssemblyTaskValidationError("assembly_tasks содержит дубли")

    rows = await conn.fetch(VALIDATE_ASSEMBLY_TASKS, task_ids)

    found_ids = {row["task_id"] for row in rows}
    non_existing_ids = set(task_ids) - found_ids
    already_shipped = {row["task_id"] for row in rows if row["is_shipped"]}

    if non_existing_ids:
        logger.error(f"Сборочные задания не найдены в БД: {sorted(non_existing_ids)}")
        raise AssemblyTaskValidationError(f"Задания не найдены: {sorted(non_existing_ids)}")

    if already_shipped:
        logger.error(f"Сборочные задания уже отгружены: {sorted(already_shipped)}")
        raise AssemblyTasksAlreadyProcessedError(
            f"Задания уже отгружены: {sorted(already_shipped)}"
        )

    updated_rows = await conn.fetch(MARK_ASSEMBLY_TASKS_SHIPPED, task_ids)
    updated_ids = {row["task_id"] for row in updated_rows}
    if updated_ids != set(task_ids):
        raise AssemblyTasksAlreadyProcessedError(
            f"Не удалось захватить все сборочные задания: expected={sorted(task_ids)}, updated={sorted(updated_ids)}"
        )
    logger.info(f"Сборочные задания помечены как отгруженные: {sorted(task_ids)}")


async def _process_shipment_group(
    conn: Connection,
    product_id: str,
    total_quantity: int,
    all_assembly_tasks: List[str],
    author: str,
    movement_service: MovementService,
    shipment_repo: FbsShipmentRepository,
    item_ids: Sequence[int],
    retry_count: Optional[int] = None,
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
    locked_items = await shipment_repo.lock_items_for_processing(conn, item_ids=item_ids)
    locked_item_ids = {row["item_id"] for row in locked_items}
    if locked_item_ids != set(item_ids):
        raise FbsShipmentItemsUpdateError(
            f"Не удалось заблокировать все FBS items: expected={sorted(item_ids)}, "
            f"locked={sorted(locked_item_ids)}"
        )
    shipment_ids = {row["shipment_id"] for row in locked_items}
    if len(shipment_ids) != 1:
        raise FbsShipmentItemsUpdateError(
            f"FBS items product group принадлежат разным shipments: {sorted(shipment_ids)}"
        )

    if settings.FBS_VALIDATE_ASSEMBLY_TASKS:
        try:
            await validate_assembly_tasks(all_assembly_tasks, conn)
        except AssemblyTasksAlreadyProcessedError as exc:
            linked_tasks = await shipment_repo.get_success_linked_assembly_tasks(
                conn, assembly_tasks=all_assembly_tasks
            )
            expected_tasks = {str(task_id) for task_id in all_assembly_tasks}
            if not expected_tasks.issubset(linked_tasks):
                missing_links = sorted(expected_tasks - linked_tasks)
                raise InconsistentFbsShipmentError(
                    "Обнаружено неконсистентное FBS-списание: сборочные задания "
                    f"уже отгружены, но отсутствует success item с movement_id: {missing_links}"
                ) from exc
            raise
    else:
        logger.warning(
            "Проверка assembly_tasks отключена настройкой "
            "FBS_VALIDATE_ASSEMBLY_TASKS=false; public.assembly_task не используется"
        )

    movement = MovementCreate(
        movement_type=MovementType.SHIP,
        product_id=product_id,
        quantity=total_quantity,
        user_name=author,
        from_location_code=settings.FBS_LOCATION_CODE,
        reason=f"Списание из ФБС зоны. Сборочные задания: {all_assembly_tasks}",
    )
    created = await movement_service.create_movement_in_transaction(conn, [movement])
    movement_id = created[0].movement_id
    updated_item_ids = set(
        await shipment_repo.mark_items_success_in_transaction(
            conn, item_ids=item_ids, movement_id=movement_id, retry_count=retry_count
        )
    )
    if updated_item_ids != set(item_ids):
        raise FbsShipmentItemsUpdateError(
            f"Не удалось обновить все FBS items: expected={sorted(item_ids)}, updated={sorted(updated_item_ids)}"
        )
    await shipment_repo.update_shipment_status(conn, shipment_ids.pop())
    return movement_id


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
            grouped[item.product_id] = acc.model_copy(
                update={
                    "quantity": acc.quantity + item.quantity,
                    "assembly_tasks": acc.assembly_tasks + item.assembly_tasks,
                }
            )
    return list(grouped.values())


def _items_to_dicts(items: List[WriteOffAccordingToFBS]) -> List[dict]:
    """Конвертирует список схем в список dict для репозитория."""
    return [item.model_dump() for item in items]


async def handle_write_off_fbs(
    items: List[WriteOffAccordingToFBS],
    pool: Pool,
    raw_message: Optional[dict] = None,
    shipment_id: Optional[int] = None,
    source: FbsShipmentSource = FbsShipmentSource.STANDARD,
) -> int:
    """
    Обрабатывает список объектов списания из ФБС зоны.

    Если shipment_id передан (consumer уже создал запись до валидации) —
    использует его. Иначе создаёт shipment сам (для прямых вызовов).

    Шаги:
    1. Создать fbs_shipment_items для уже существующего shipment
    2. Для каждой группы product_id — попытка списания (отдельная транзакция):
       - Успех          → status=success, movement_id заполнен
       - CheckViolation → status=pending_retry, next_retry_at заполнен
       - Другая ошибка  → status=failed, error_message заполнен
    3. Пересчитать статус shipment

    Returns:
        shipment_id
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

    # --- Создаём shipment_items (shipment уже создан consumer'ом ДО валидации) ---
    async with pool.acquire() as conn:
        async with conn.transaction():
            if shipment_id is None:
                # Прямой вызов без consumer'а — создаём shipment здесь
                shipment_id = await shipment_repo.create_shipment(
                    conn,
                    raw_message=raw_message if raw_message is not None else [],
                    total_items=len(items),
                    source=source.value,
                )
            item_ids = await shipment_repo.create_shipment_items(
                conn,
                shipment_id=shipment_id,
                items=_items_to_dicts(items),
            )

    logger.info(f"Shipment items созданы | shipment_id={shipment_id} | items={len(item_ids)}")

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
                        shipment_repo=shipment_repo,
                        item_ids=related_item_ids,
                    )

            logger.info(
                f"Списание выполнено | product_id={group.product_id} | "
                f"qty={group.quantity} | movement_id={movement_id}"
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
