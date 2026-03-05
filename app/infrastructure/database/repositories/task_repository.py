"""Репозиторий для работы с заявками"""

import json
from typing import List, Optional
from datetime import date
from asyncpg import Pool, Record

from app.infrastructure.database.queries import tasks as queries


class TaskRepository:
    """Репозиторий для таблиц wms.tasks и wms.task_items"""

    def __init__(self, pool: Pool):
        self.pool = pool

    # ============================================================
    # Список / Деталь
    # ============================================================

    async def get_tasks(
            self,
            status: Optional[str] = None,
            task_type: Optional[str] = None,
            assigned_to: Optional[int] = None,
            from_location_id: Optional[int] = None,
            to_location_id: Optional[int] = None,
            from_date: Optional[date] = None,
            to_date: Optional[date] = None,
            limit: int = 100,
            offset: int = 0,
    ) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                queries.GET_TASKS,
                status,
                task_type,
                assigned_to,
                from_location_id,
                to_location_id,
                from_date,
                to_date,
                limit,
                offset,
            )

    async def get_by_id(self, task_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.GET_TASK_BY_ID, task_id)

    async def get_task_items(self, task_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_TASK_ITEMS, task_id)

    async def get_task_item_detail(self, item_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.GET_TASK_ITEM_DETAIL, item_id)

    # ============================================================
    # Создание
    # ============================================================

    async def create(
            self,
            task_type: str,
            priority: int,
            from_location_code: str,
            to_location_code: str,
            due_date,
            reason: Optional[str],
            notes: Optional[str],
            created_by: int,
            items: list,
    ) -> Record:
        """Создать заявку вместе с позициями в одной транзакции"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    queries.CREATE_TASK,
                    task_type,
                    priority,
                    from_location_code,
                    to_location_code,
                    due_date,
                    reason,
                    notes,
                    created_by,
                )
                task_id = task["task_id"]

                for item in items:
                    await conn.execute(
                        queries.INSERT_TASK_ITEM,
                        task_id,
                        item["product_id"],
                        item["quantity_planned"],
                        item.get("batch_number"),
                    )

                count_row = await conn.fetchrow(queries.COUNT_TASK_ITEMS, task_id)
                items_count = count_row["items_count"] if count_row else 0

        return task, items_count

    # ============================================================
    # Обновление
    # ============================================================

    async def update(self, task_id: int, priority, due_date, notes) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.UPDATE_TASK, task_id, priority, due_date, notes)

    async def cancel(self, task_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.CANCEL_TASK, task_id)

    async def set_status(self, task_id: int, status: str) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.SET_TASK_STATUS, task_id, status)

    # ============================================================
    # ТСД-операции
    # ============================================================

    async def assign(self, task_id: int, user_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.ASSIGN_TASK, task_id, user_id)

    async def start(self, task_id: int, user_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.START_TASK, task_id, user_id)

    async def update_task_item_actual(
            self,
            item_id: int,
            task_id: int,
            quantity_actual: int,
            from_location_code: str,
            discrepancy_reason: Optional[str],
    ) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                queries.UPDATE_TASK_ITEM_ACTUAL,
                item_id,
                quantity_actual,
                task_id,
                from_location_code,
                discrepancy_reason,
            )

    async def validate_items_belong_to_task(
            self, task_id: int, item_ids: List[int]
    ) -> List[int]:
        """Проверить что все item_ids принадлежат task_id"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                queries.VALIDATE_ITEM_IDS_BELONG_TO_TASK,
                task_id,
                item_ids,
            )
            return [row["item_id"] for row in rows]


    async def check_discrepancies(self, task_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.CHECK_DISCREPANCIES, task_id)

    async def complete_task(self, task_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.COMPLETE_TASK, task_id)

    async def get_availability_warnings(self, task_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.CHECK_AVAILABILITY_ON_START, task_id)

    # ============================================================
    # Подсказки (suggestions)
    # ============================================================

    async def get_suggestions_raw(self, task_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_SUGGESTIONS, task_id)

    async def get_product_qty_in_zone(
            self, product_id: str, zone_code: str
    ) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(queries.GET_PRODUCT_QTY_IN_ZONE, product_id, zone_code)
            return float(row["available"]) if row else 0.0

    # ============================================================
    # Дочерние заявки
    # ============================================================

    async def create_child_task(
            self,
            parent_task_id: int,
            task_type: str,
            assigned_to: Optional[int],
            metadata: dict,
            created_by: int,
    ) -> Record:
        """
        Создать дочернюю заявку (discrepancy_approval или recount).

        Args:
            parent_task_id: ID родительской заявки
            task_type: Тип дочерней заявки ('discrepancy_approval' или 'recount')
            assigned_to: ID пользователя на которого назначить (может быть None)
            metadata: Метаданные дочерней заявки (JSON)
            created_by: ID пользователя-создателя

        Returns:
            Record с данными созданной заявки
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                queries.CREATE_CHILD_TASK,
                parent_task_id,
                task_type,
                assigned_to,
                json.dumps(metadata, ensure_ascii=False),
                created_by,
            )

    # ============================================================
    # Подтверждение расхождений
    # ============================================================

    async def check_user_can_approve(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(queries.CHECK_USER_CAN_APPROVE, user_id)
            return bool(row["can_approve"]) if row else False

    async def get_approvers(self) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_APPROVERS)

    async def update_approved_qty(self, item_id: int, quantity: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.UPDATE_TASK_ITEM_APPROVED_QTY, item_id, quantity)

    async def get_inventory_qty_in_location(
            self, product_id: str, location_id: int
    ) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                queries.GET_INVENTORY_QTY_IN_LOCATION, product_id, location_id
            )
            return float(row["available"]) if row else 0.0

    async def get_items_for_movements(self, task_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_TASK_ITEMS_FOR_MOVEMENTS, task_id)

    # ============================================================
    # Пересчёт
    # ============================================================

    async def save_recount_results(
            self, task_id: int, results: dict, user_id: int
    ) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                queries.SAVE_RECOUNT_RESULTS,
                task_id,
                json.dumps(results, ensure_ascii=False),
                user_id,
            )

    async def get_inventory_qty_by_location_code(
            self, product_id: str, location_code: str
    ) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                queries.GET_INVENTORY_QTY_BY_LOCATION_CODE, product_id, location_code
            )
            return float(row["current_qty"]) if row else 0.0

    # ============================================================
    # Активные заявки сотрудника (ТСД)
    # ============================================================

    async def get_my_tasks(self, user_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT *
                FROM wms.v_tasks_with_users
                WHERE assigned_to = $1
                  AND status IN ('assigned', 'in_progress')
                ORDER BY priority ASC, due_date ASC NULLS LAST;
                """,
                user_id,
            )

    async def get_available_tasks(self, limit: int = 20) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT *
                FROM wms.v_tasks_with_users
                WHERE status = 'pending'
                  AND assigned_to IS NULL
                ORDER BY priority ASC, created_at ASC
                LIMIT $1;
                """,
                limit,
            )
