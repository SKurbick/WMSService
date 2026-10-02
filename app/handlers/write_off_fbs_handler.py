"""Handler для обработки списания из ФБС зоны"""

import json
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


async def _process_shipment_group_legacy(
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


def normalize_stored_assembly_tasks(value) -> list[str]:
    """Decode assembly_tasks read from jsonb and validate task identifiers."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("assembly_tasks должен быть JSON-массивом")
    result = []
    for task_id in value:
        int(task_id)
        result.append(str(task_id))
    return result


async def _classify_task_occurrences(
    conn: Connection,
    *,
    product_id: str,
    locked_items: Sequence,
    shipment_repo: FbsShipmentRepository,
) -> tuple[list[dict], list[str]]:
    """Classify every payload occurrence while assembly-task rows are locked."""
    occurrences: list[dict] = []
    all_tasks: list[str] = []
    shipment_id = locked_items[0]["shipment_id"]

    for row in sorted(locked_items, key=lambda item: item["item_id"]):
        if row["product_id"] != product_id:
            raise FbsShipmentItemsUpdateError(
                f"Item {row['item_id']} имеет product_id={row['product_id']}, "
                f"ожидался {product_id}"
            )
        tasks = normalize_stored_assembly_tasks(row["assembly_tasks"])
        for occurrence_index, task_id in enumerate(tasks):
            occurrences.append(
                {
                    "shipment_id": shipment_id,
                    "item_id": row["item_id"],
                    "occurrence_index": occurrence_index,
                    "task_id": task_id,
                    "product_id": product_id,
                    "effect_quantity": 0,
                    "details": {},
                }
            )
            all_tasks.append(task_id)

    states = await shipment_repo.lock_assembly_tasks(conn, assembly_tasks=all_tasks)
    confirmed_links = await shipment_repo.get_confirmed_task_links(
        conn, assembly_tasks=all_tasks, product_id=product_id
    )
    written_occurrences = await shipment_repo.get_written_task_occurrences(
        conn, item_ids=[row["item_id"] for row in locked_items]
    )

    owners: dict[str, dict] = {}
    new_tasks: list[str] = []
    for result in occurrences:
        task_id = result["task_id"]
        result["is_shipped_before"] = states.get(task_id)
        occurrence_key = (result["item_id"], result["occurrence_index"])
        if occurrence_key in written_occurrences:
            previous = written_occurrences[occurrence_key]
            owners.setdefault(task_id, result)
            result.update(
                outcome="written_off",
                effect_quantity=1,
                movement_id=previous["movement_id"],
                movement_created_at=previous["movement_created_at"],
                is_shipped_before=previous["is_shipped_before"],
                reason=previous["reason"],
                details={"preserved_from_previous_attempt": True},
            )
            continue
        if task_id in owners:
            owner = owners[task_id]
            result.update(
                outcome="duplicate_in_payload",
                reason="Повтор СЗ внутри текущей товарной группы; физический эффект учтён один раз",
                details={
                    "owner_item_id": owner["item_id"],
                    "owner_occurrence_index": owner["occurrence_index"],
                },
            )
            continue

        owners[task_id] = result
        if task_id not in states:
            result.update(outcome="not_found", reason="СЗ не найдено в public.assembly_task")
        elif not states[task_id]:
            result.update(outcome="new", reason="Новое СЗ подготовлено к списанию")
            new_tasks.append(task_id)
        elif task_id in confirmed_links:
            link = confirmed_links[task_id]
            result.update(
                outcome="duplicate_skipped",
                reason="СЗ ранее подтверждённо списано; повторный эффект пропущен",
                existing_success_item_id=link["existing_success_item_id"],
                existing_movement_id=link["existing_movement_id"],
                existing_movement_created_at=link["existing_movement_created_at"],
            )
        else:
            result.update(
                outcome="inconsistent",
                reason=(
                    "СЗ отмечено отгруженным, но подтверждённая связь с FBS movement не найдена"
                ),
            )

    return occurrences, new_tasks


def _item_task_state(results: Sequence[dict]) -> tuple[str, str, Optional[str]]:
    outcomes = {result["outcome"] for result in results}
    has_written = "written_off" in outcomes
    has_duplicate = bool(outcomes & {"duplicate_skipped", "duplicate_in_payload"})
    anomalies = outcomes & {"inconsistent", "not_found", "failed"}

    if "pending_retry" in outcomes:
        return "pending_retry", "pending_retry", "Новые СЗ ожидают повторного списания"
    if has_written and anomalies:
        return (
            "failed",
            "partially_completed",
            "Новые СЗ списаны, но часть СЗ требует сверки: " + ", ".join(sorted(anomalies)),
        )
    if has_written:
        resolution = "completed_with_duplicates" if has_duplicate else "completed"
        return "success", resolution, None
    if outcomes and outcomes <= {"duplicate_skipped", "duplicate_in_payload"}:
        return (
            "failed",
            "duplicate_only",
            "Нового списания нет: все СЗ являются подтверждёнными или внутривходными дублями",
        )
    return "failed", "failed", "СЗ не списаны: " + ", ".join(sorted(outcomes))


async def _save_task_level_item_states(
    conn: Connection,
    *,
    locked_items: Sequence,
    results: Sequence[dict],
    shipment_repo: FbsShipmentRepository,
    movement_id: Optional[int],
    retry_count: Optional[int],
    next_retry_at: Optional[datetime] = None,
    item_status_override: Optional[str] = None,
) -> None:
    by_item: dict[int, list[dict]] = {}
    for result in results:
        by_item.setdefault(result["item_id"], []).append(result)

    for row in locked_items:
        item_results = by_item.get(row["item_id"], [])
        status, resolution, error = _item_task_state(item_results)
        if item_status_override is not None:
            status = item_status_override
        # Preserve the legacy contract: movement_id is exposed only by a
        # successful item. Partial physical effects remain fully linked in the
        # task-result rows through (movement_id, movement_created_at).
        item_movement_id = None
        if status == "success":
            item_movement_id = movement_id
            if item_movement_id is None:
                written_results = [
                    result
                    for result in item_results
                    if result["outcome"] == "written_off" and result.get("movement_id")
                ]
                if written_results:
                    latest_written = max(
                        written_results,
                        key=lambda result: result.get("movement_created_at")
                        or datetime.min.replace(tzinfo=timezone.utc),
                    )
                    item_movement_id = latest_written["movement_id"]
        updated = await shipment_repo.update_item_task_result(
            conn,
            item_id=row["item_id"],
            status=status,
            task_resolution_status=resolution,
            error_message=error,
            movement_id=item_movement_id,
            retry_count=retry_count,
            next_retry_at=next_retry_at if status == "pending_retry" else None,
        )
        if not updated:
            raise FbsShipmentItemsUpdateError(
                f"Не удалось обновить task-level итог item_id={row['item_id']}"
            )


async def _process_shipment_group_task_level(
    conn: Connection,
    *,
    product_id: str,
    total_quantity: int,
    author: str,
    movement_service: MovementService,
    shipment_repo: FbsShipmentRepository,
    item_ids: Sequence[int],
    retry_count: Optional[int],
    expected_statuses: Optional[set[str]],
) -> Optional[int]:
    locked_items = await shipment_repo.lock_items_for_processing(conn, item_ids=item_ids)
    if {row["item_id"] for row in locked_items} != set(item_ids):
        raise FbsShipmentItemsUpdateError(
            f"Не удалось заблокировать все FBS items: expected={sorted(item_ids)}"
        )
    shipment_ids = {row["shipment_id"] for row in locked_items}
    if len(shipment_ids) != 1:
        raise FbsShipmentItemsUpdateError("FBS items принадлежат разным shipments")
    if expected_statuses is not None and any(
        row["status"] not in expected_statuses for row in locked_items
    ):
        logger.info(
            "Task-level FBS attempt skipped after row lock: item status changed | "
            "item_ids=%s | expected_statuses=%s | actual_statuses=%s",
            sorted(item_ids),
            sorted(expected_statuses),
            sorted({row["status"] for row in locked_items}),
        )
        movement_ids = {row["movement_id"] for row in locked_items if row["movement_id"]}
        return movement_ids.pop() if len(movement_ids) == 1 else None

    results, new_tasks = await _classify_task_occurrences(
        conn,
        product_id=product_id,
        locked_items=locked_items,
        shipment_repo=shipment_repo,
    )
    claimed = (
        await shipment_repo.mark_assembly_tasks_shipped(conn, assembly_tasks=new_tasks)
        if new_tasks
        else set()
    )
    if claimed != set(new_tasks):
        raise AssemblyTasksAlreadyProcessedError(
            f"Не удалось атомарно захватить новые СЗ: expected={sorted(new_tasks)}, "
            f"updated={sorted(claimed)}"
        )

    movement_id: Optional[int] = None
    movement_created_at: Optional[datetime] = None
    if new_tasks:
        movement = MovementCreate(
            movement_type=MovementType.SHIP,
            product_id=product_id,
            quantity=len(new_tasks),
            user_name=author,
            from_location_code=settings.FBS_LOCATION_CODE,
            reason=f"Списание из ФБС зоны. Новые сборочные задания: {new_tasks}",
        )
        created = await movement_service.create_movement_in_transaction(conn, [movement])
        movement_id = created[0].movement_id
        movement_created_at = created[0].created_at

    for result in results:
        if result["outcome"] == "new":
            result.update(
                outcome="written_off",
                effect_quantity=1,
                movement_id=movement_id,
                movement_created_at=movement_created_at,
                reason="СЗ списано текущим movement",
            )

    await shipment_repo.upsert_task_results(conn, results=results)
    await _save_task_level_item_states(
        conn,
        locked_items=locked_items,
        results=results,
        shipment_repo=shipment_repo,
        movement_id=movement_id,
        retry_count=retry_count,
    )
    shipment_id = shipment_ids.pop()
    await shipment_repo.update_shipment_status(conn, shipment_id)
    logger.info(
        "Task-level FBS group processed | shipment_id=%s | product_id=%s | "
        "incoming_tasks=%s | new_tasks=%s | duplicate_tasks=%s | "
        "inconsistent_tasks=%s | not_found_tasks=%s | movement_id=%s | "
        "declared_quantity=%s",
        shipment_id,
        product_id,
        len(results),
        len(new_tasks),
        sum(r["outcome"] in {"duplicate_skipped", "duplicate_in_payload"} for r in results),
        sum(r["outcome"] == "inconsistent" for r in results),
        sum(r["outcome"] == "not_found" for r in results),
        movement_id,
        total_quantity,
    )
    return movement_id


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
    expected_statuses: Optional[set[str]] = None,
) -> Optional[int]:
    if settings.FBS_TASK_PROCESSING_MODE == "task_level" and settings.FBS_VALIDATE_ASSEMBLY_TASKS:
        return await _process_shipment_group_task_level(
            conn,
            product_id=product_id,
            total_quantity=total_quantity,
            author=author,
            movement_service=movement_service,
            shipment_repo=shipment_repo,
            item_ids=item_ids,
            retry_count=retry_count,
            expected_statuses=expected_statuses,
        )
    return await _process_shipment_group_legacy(
        conn=conn,
        product_id=product_id,
        total_quantity=total_quantity,
        all_assembly_tasks=all_assembly_tasks,
        author=author,
        movement_service=movement_service,
        shipment_repo=shipment_repo,
        item_ids=item_ids,
        retry_count=retry_count,
    )


async def record_task_level_attempt_failure(
    conn: Connection,
    *,
    product_id: str,
    item_ids: Sequence[int],
    shipment_repo: FbsShipmentRepository,
    outcome: str,
    error_message: str,
    retry_count: Optional[int] = None,
    next_retry_at: Optional[datetime] = None,
    item_status_override: Optional[str] = None,
) -> None:
    """Persist a rolled-back task-level attempt in a fresh short transaction."""
    if outcome not in {"pending_retry", "failed"}:
        raise ValueError(f"Недопустимый outcome ошибки: {outcome}")
    locked_items = await shipment_repo.lock_items_for_processing(conn, item_ids=item_ids)
    if {row["item_id"] for row in locked_items} != set(item_ids):
        raise FbsShipmentItemsUpdateError("Не удалось заблокировать items для записи ошибки")
    results, _ = await _classify_task_occurrences(
        conn,
        product_id=product_id,
        locked_items=locked_items,
        shipment_repo=shipment_repo,
    )
    for result in results:
        if result["outcome"] == "new":
            result.update(
                outcome=outcome,
                reason=(
                    "Списание отложено до retry"
                    if outcome == "pending_retry"
                    else "Списание завершилось ошибкой"
                ),
                last_error=error_message,
            )
    await shipment_repo.upsert_task_results(conn, results=results)
    await _save_task_level_item_states(
        conn,
        locked_items=locked_items,
        results=results,
        shipment_repo=shipment_repo,
        movement_id=None,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        item_status_override=item_status_override,
    )
    await shipment_repo.update_shipment_status(conn, locked_items[0]["shipment_id"])


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
                if settings.FBS_TASK_PROCESSING_MODE == "task_level":
                    async with conn.transaction():
                        await record_task_level_attempt_failure(
                            conn,
                            product_id=group.product_id,
                            item_ids=related_item_ids,
                            shipment_repo=shipment_repo,
                            outcome="pending_retry",
                            error_message=str(e),
                            retry_count=retry_count,
                            next_retry_at=next_retry_at,
                        )
                else:
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
                if settings.FBS_TASK_PROCESSING_MODE == "task_level":
                    async with conn.transaction():
                        await record_task_level_attempt_failure(
                            conn,
                            product_id=group.product_id,
                            item_ids=related_item_ids,
                            shipment_repo=shipment_repo,
                            outcome="failed",
                            error_message=str(e),
                        )
                else:
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
