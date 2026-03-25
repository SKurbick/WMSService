"""Handler для обработки списания из ФБС зоны"""

import logging
from typing import List

from asyncpg import Connection, Pool

from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.mappers.write_off_fbs_mapper import map_to_movement
from app.core.services.movement_service import MovementService
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository

logger = logging.getLogger(__name__)

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


async def validate_assembly_tasks(
    assembly_tasks: List[int],
    conn: Connection,
) -> None:
    """
    Проверяет сборочные задания и помечает их как отгруженные.

    Выполняется внутри транзакции вместе с основной операцией списания.
    Бросает AssemblyTaskValidationError если:
    - часть task_id не существует в БД
    - часть task_id уже имеет is_shipped = TRUE
    """
    rows = await conn.fetch(VALIDATE_ASSEMBLY_TASKS, assembly_tasks)

    found_ids = {row["task_id"] for row in rows}
    non_existing_ids = set(assembly_tasks) - found_ids
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

    await conn.execute(MARK_ASSEMBLY_TASKS_SHIPPED, assembly_tasks)
    logger.info(f"Сборочные задания помечены как отгруженные: {sorted(assembly_tasks)}")


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


async def handle_write_off_fbs(
    items: List[WriteOffAccordingToFBS],
    pool: Pool,
    movement_service: MovementService = None,
) -> None:
    """
    Обрабатывает список объектов списания из ФБС зоны.

    Группирует items по product_id до старта транзакции.
    Всё выполняется в одной транзакции:
    1. Валидация и маркировка всех сборочных заданий
    2. Создание всех movements батчем через MovementService

    При любой ошибке транзакция откатывается целиком.
    """
    all_assembly_tasks = [tid for item in items for tid in item.assembly_tasks]
    grouped_items = _group_items(items)

    if len(grouped_items) < len(items):
        logger.info(
            f"Группировка: {len(items)} позиций → {len(grouped_items)} уникальных product_id"
        )

    if movement_service is None:
        movement_repo = MovementRepository(pool)
        location_repo = LocationRepository(pool)
        movement_service = MovementService(movement_repo, location_repo)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Валидация и маркировка assembly_tasks
            await validate_assembly_tasks(all_assembly_tasks, conn)

            # 2. Собрать все movements
            movements = []
            for item in grouped_items:
                logger.info(
                    f"Подготовка позиции | product_id={item.product_id} | "
                    f"qty={item.quantity} | tasks={item.assembly_tasks}"
                )
                movements.append(map_to_movement(item))

            # 3. Создать movements в той же транзакции
            created = await movement_service.create_movement_in_transaction(conn, movements)

            logger.info(
                f"Movements созданы атомарно | total={len(created)} | "
                f"movement_ids={[m.movement_id for m in created]}"
            )
