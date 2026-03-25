"""Сервис для работы с заявками (бизнес-логика)"""

import json
import logging
from decimal import Decimal
from typing import List, Optional
from datetime import date

from fastapi import HTTPException

from app.core.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskListResponse,
    TaskDetailResponse,
    TaskCreateResponse,
    TaskUpdateResponse,
    TaskCancelResponse,
    TaskAssign,
    TaskAssignResponse,
    TaskStart,
    TaskStartResponse,
    TaskComplete,
    TaskCompleteResponse,
    TaskMyResponse,
    TaskSuggestionsResponse,
    ProductSuggestion,
    LocationSuggestion,
    ApproveDiscrepancy,
    ApproveDiscrepancyResponse,
    RejectDiscrepancyResponse,
    RecountResponse,
    CompleteRecount,
    CompleteRecountResponse,
    TaskItemResponse,
    ChildTaskSummary,
)
from app.infrastructure.database.repositories.task_repository import TaskRepository
from app.infrastructure.database.repositories.notification_repository import NotificationRepository
from app.core.services.movement_service import MovementService
from app.core.schemas.movement import MovementCreate
from app.core.services.notification_service import NotificationService
from app.core.exceptions import (
    TaskNotFoundError,
    TaskForbiddenError,
    TaskInvalidStatusError,
    TaskPermissionDeniedError,
    TaskItemNotFoundError,
    LocationNotFoundError,
)

logger = logging.getLogger(__name__)

def convert_decimals_to_float(obj):
    """
    Рекурсивно конвертирует все Decimal в dict/list в float.
    Необходимо для JSON сериализации данных из PostgreSQL.
    """
    if isinstance(obj, dict):
        return {k: convert_decimals_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals_to_float(item) for item in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj

class TaskService:
    """Сервис заявок"""

    def __init__(
        self,
        task_repo: TaskRepository,
        notification_repo: NotificationRepository,
        movement_service: MovementService,
        notification_service: NotificationService,
    ):
        self.task_repo = task_repo
        self.notification_repo = notification_repo
        self.movement_service = movement_service
        self.notification_svc = notification_service

    # ============================================================
    # Получение заявок
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
        hide_child_tasks: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TaskListResponse]:
        records = await self.task_repo.get_tasks(
            status=status,
            task_type=task_type,
            assigned_to=assigned_to,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )

        tasks = [TaskListResponse.model_validate(dict(r)) for r in records]

        if hide_child_tasks:
            tasks = [t for t in tasks if t.parent_task_id is None]

        # Батч-загрузка дочерних заявок для всех родителей в одном запросе
        parent_ids = [t.task_id for t in tasks if t.parent_task_id is None]
        if parent_ids:
            child_records = await self.task_repo.get_child_tasks_for_parents(parent_ids)
            children_by_parent: dict[int, list[ChildTaskSummary]] = {}
            for cr in child_records:
                child = ChildTaskSummary.model_validate(dict(cr))
                children_by_parent.setdefault(cr["parent_task_id"], []).append(child)

            for task in tasks:
                task.child_tasks = children_by_parent.get(task.task_id, [])

        return tasks

    async def get_task_by_id(self, task_id: int) -> TaskDetailResponse:
        """
        Получить детальную информацию о заявке с позициями.

        Для обычных заявок (replenishment, transfer, picking, putaway) позиции берутся из task_items.
        Для дочерних заявок (discrepancy_approval, recount) позиции берутся из metadata.discrepancies.

        Args:
            task_id: ID заявки

        Returns:
            TaskDetailResponse с полной информацией о заявке и позициями

        Raises:
            TaskNotFoundError: Заявка не найдена
        """
        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        task_dict = dict(task)
        if task_dict.get("metadata"):
            if isinstance(task_dict["metadata"], str):
                task_dict["metadata"] = json.loads(task_dict["metadata"])

        # Для дочерних заявок (discrepancy_approval, recount) берём items из metadata
        if task["task_type"] in ("discrepancy_approval", "recount"):
            # Парсим metadata (может быть строка или dict)
            metadata_raw = task["metadata"]
            if isinstance(metadata_raw, str):
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            elif isinstance(metadata_raw, dict):
                metadata = metadata_raw
            else:
                metadata = {}

            # Извлекаем discrepancies и преобразуем в items
            items_from_metadata = []
            for disc in metadata.get("discrepancies", []):
                items_from_metadata.append(TaskItemResponse(
                    item_id=disc.get("item_id"),
                    product_id=disc.get("product_id"),
                    product_name=None,  # В metadata нет имени товара
                    quantity_planned=float(disc.get("quantity_planned", 0)),
                    quantity_actual=float(disc.get("quantity_actual", 0)) if disc.get("quantity_actual") is not None else None,
                    from_location_id=None,  # В metadata нет location_id
                    from_location_code=disc.get("from_location_code"),
                    batch_number=None,  # В metadata нет batch
                    discrepancy_reason=disc.get("reason"),
                    has_discrepancy=disc.get("has_discrepancy"),
                    discrepancy_qty=float(disc.get("discrepancy_qty", 0)) if disc.get("discrepancy_qty") is not None else None,
                    discrepancy_percent=float(disc.get("discrepancy_percent", 0)) if disc.get("discrepancy_percent") is not None else None,
                ))

            task_dict["items"] = items_from_metadata
        else:
            # Для обычных заявок берём items из task_items (как раньше)
            items_records = await self.task_repo.get_task_items(task_id)
            task_dict["items"] = [TaskItemResponse.model_validate(dict(r)) for r in items_records]

        return TaskDetailResponse.model_validate(task_dict)

    async def get_my_tasks(self, user_id: int) -> List[TaskMyResponse]:
        records = await self.task_repo.get_my_tasks(user_id)
        return [TaskMyResponse.model_validate(dict(r)) for r in records]

    async def get_available_tasks(self, limit: int = 20) -> List[TaskListResponse]:
        records = await self.task_repo.get_available_tasks(limit)
        return [TaskListResponse.model_validate(dict(r)) for r in records]

    # ============================================================
    # Создание заявки
    # ============================================================

    async def create_task(self, data: TaskCreate) -> TaskCreateResponse:
        """
        Создать заявку с позициями.

        Валидирует локации и товары, допускает quantity > остатков
        (только предупреждение).
        """
        # Проверяем что items не пустые и quantity > 0 — уже в схеме (min_length=1, gt=0)

        items_data = [item.model_dump() for item in data.items]

        # Создаём заявку и позиции (транзакция в репозитории)
        task_record, items_count = await self.task_repo.create(
            task_type=data.task_type,
            priority=data.priority,
            from_location_code=data.from_location_code,
            to_location_code=data.to_location_code,
            due_date=data.due_date,
            reason=data.reason,
            notes=data.notes,
            created_by=data.created_by,
            items=items_data,
        )

        if not task_record:
            raise LocationNotFoundError(
                f"Одна из локаций не найдена: '{data.from_location_code}' "
                f"или '{data.to_location_code}'"
            )

        task_id = task_record["task_id"]

        # Предупреждения об остатках (не блокируют создание)
        warnings = []
        for item in data.items:
            available = await self.task_repo.get_product_qty_in_zone(
                item.product_id, data.from_location_code
            )
            if item.quantity_planned > available:
                warnings.append({
                    "product_id": item.product_id,
                    "planned": item.quantity_planned,
                    "available": available,
                    "message": (
                        f"В зоне доступно только {available:.0f} шт, "
                        f"а запланировано {item.quantity_planned} шт"
                    ),
                })

        return TaskCreateResponse(
            task_id=task_id,
            task_type=task_record["task_type"],
            status=task_record["status"],
            items_count=items_count,
            created_at=task_record["created_at"],
            warnings=warnings or None,
        )

    # ============================================================
    # Обновление / Отмена
    # ============================================================

    async def update_task(self, task_id: int, data: TaskUpdate) -> TaskUpdateResponse:
        record = await self.task_repo.update(task_id, data.priority, data.due_date, data.notes)
        if not record:
            raise TaskInvalidStatusError(
                f"Заявка #{task_id} не найдена или уже завершена/отменена"
            )
        return TaskUpdateResponse.model_validate(dict(record))

    async def cancel_task(self, task_id: int) -> TaskCancelResponse:
        record = await self.task_repo.cancel(task_id)
        if not record:
            raise TaskInvalidStatusError(
                f"Заявка #{task_id} не найдена или не может быть отменена"
            )
        return TaskCancelResponse.model_validate(dict(record))

    # ============================================================
    # ТСД: Назначение, Старт, Завершение
    # ============================================================

    async def assign_task(self, task_id: int, data: TaskAssign) -> TaskAssignResponse:
        record = await self.task_repo.assign(task_id, data.user_id)
        if not record:
            raise TaskInvalidStatusError(
                f"Заявка #{task_id} не найдена, уже назначена или не в статусе pending"
            )
        return TaskAssignResponse.model_validate(dict(record))

    async def start_task(self, task_id: int, data: TaskStart) -> TaskStartResponse:
        record = await self.task_repo.start(task_id, data.user_id)
        if not record:
            raise TaskInvalidStatusError(
                f"Заявка #{task_id} не найдена, не назначена на вас или не в статусе assigned"
            )

        # Предупреждения о доступности товаров
        warnings = []
        avail_records = await self.task_repo.get_availability_warnings(task_id)
        for row in avail_records:
            warnings.append({
                "product_id": row["product_id"],
                "quantity_planned": float(row["quantity_planned"]),
                "quantity_available": float(row["quantity_available"]),
                "message": (
                    f"В зоне доступно только {row['quantity_available']:.0f} шт "
                    f"из {row['quantity_planned']:.0f} запланированных. "
                    f"Возможна нехватка товара."
                ),
            })

        return TaskStartResponse(
            task_id=record["task_id"],
            status=record["status"],
            started_at=record["started_at"],
            warnings=warnings or None,
        )

    async def complete_task(self, task_id: int, data: TaskComplete) -> TaskCompleteResponse:
        """
        Завершить заявку с фактическими данными.

        Алгоритм:
        1. Проверить что заявка существует, назначена на пользователя и в статусе in_progress
        2. Валидировать что переданы ВСЕ позиции заявки (защита от частичного завершения)
        3. Валидировать что все item_id принадлежат данной заявке (защита от подмены)
        4. Обновить фактические данные (quantity_actual, from_location_code) для каждой позиции
        5. Проверить расхождения между planned и actual
        6. Если есть расхождение и у пользователя нет права approve_discrepancies:
           - Создать дочернюю заявку типа discrepancy_approval для админа
           - Перевести родительскую заявку в статус pending_approval
           - Отправить уведомления всем пользователям с правом approve_discrepancies
           - НЕ создавать movements (ждём подтверждения админа)
        7. Если нет расхождений ИЛИ пользователь имеет право approve:
           - Создать movements для всех позиций
           - Завершить заявку (статус: completed или completed_with_discrepancy)

        Args:
            task_id: ID заявки
            data: Данные завершения (фактические количества, локации)

        Returns:
            TaskCompleteResponse с итоговым статусом

        Raises:
            TaskNotFoundError: Заявка не найдена
            TaskForbiddenError: Заявка назначена на другого пользователя
            TaskInvalidStatusError: Заявка не в статусе in_progress или неполные данные
            TaskItemNotFoundError: Позиция заявки не найдена
        """
        # 1. Проверить существование и права
        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        if task["assigned_to"] != data.user_id:
            raise TaskForbiddenError("Эта заявка не назначена на вас")

        if task["status"] != "in_progress":
            raise TaskInvalidStatusError(
                f"Заявка должна быть в статусе in_progress, текущий: {task['status']}"
            )

        # 2. Валидация: проверить что передали данные для ВСЕХ позиций заявки
        task_items = await self.task_repo.get_task_items(task_id)
        expected_item_ids = {item["item_id"] for item in task_items}
        received_item_ids = {item.item_id for item in data.items}

        # 2.1. Проверить что не пропущены позиции
        missing_items = expected_item_ids - received_item_ids
        if missing_items:
            raise TaskInvalidStatusError(
                f"Не переданы данные для позиций: {list(missing_items)}. "
                f"Необходимо передать данные для ВСЕХ позиций заявки."
            )

        # 2.2. Проверить что нет лишних/чужих позиций
        extra_items = received_item_ids - expected_item_ids
        if extra_items:
            raise TaskInvalidStatusError(
                f"Переданы неверные item_id: {list(extra_items)}. "
                f"Эти позиции не принадлежат заявке #{task_id}."
            )

        # 3. Обновить фактические данные для каждой позиции
        for item in data.items:
            result = await self.task_repo.update_task_item_actual(
                item_id=item.item_id,
                task_id=task_id,
                quantity_actual=item.quantity_actual,
                from_location_code=item.from_location_code,
                discrepancy_reason=item.discrepancy_reason,
            )
            if not result:
                raise TaskItemNotFoundError(f"Позиция #{item.item_id} не найдена")

        # 4. Проверить расхождения (сравнить planned vs actual для всех позиций)
        discrepancy_records = await self.task_repo.check_discrepancies(task_id)
        discrepancies = [dict(r) for r in discrepancy_records]

        # Конвертировать Decimal → float для JSON сериализации
        discrepancies = convert_decimals_to_float(discrepancies)

        has_discrepancy = any(d["has_discrepancy"] for d in discrepancies)

        # 5. Проверить права пользователя на самостоятельное подтверждение расхождений
        user_can_approve = await self.task_repo.check_user_can_approve(data.user_id)

        # 6. Если есть расхождение и пользователь НЕ может самостоятельно подтвердить
        if has_discrepancy and not user_can_approve:
            # 6.1. Создать метаданные для дочерней заявки
            child_metadata = {
                "parent_task_info": {
                    "task_id": task_id,
                    "task_type": task["task_type"],
                    "from_location_code": task["from_location_code"],
                    "to_location_code": task["to_location_code"],
                },
                "discrepancies": discrepancies,
                "to_location_code": data.to_location_code,
            }

            # 6.2. Назначить дочернюю заявку на создателя (если у него есть право approve)
            assignee = None
            if await self.task_repo.check_user_can_approve(task["created_by"]):
                assignee = task["created_by"]

            # 6.3. Создать дочернюю заявку типа discrepancy_approval
            child = await self.task_repo.create_child_task(
                parent_task_id=task_id,
                task_type="discrepancy_approval",
                assigned_to=assignee,
                metadata=child_metadata,
                created_by=data.user_id
            )

            # 6.4. Перевести родительскую заявку в статус ожидания подтверждения
            await self.task_repo.set_status(task_id, "pending_approval")

            # 6.5. Отправить уведомления всем пользователям с правом approve_discrepancies
            approver_rows = await self.task_repo.get_approvers()
            approver_ids = [r["user_id"] for r in approver_rows]

            # 6.6. Подготовить данные для уведомления
            total_planned = sum(d["quantity_planned"] for d in discrepancies)
            total_actual = sum(d["quantity_actual"] for d in discrepancies)
            percent = (
                abs((total_actual - total_planned) / total_planned * 100)
                if total_planned else 0
            )
            severity = "critical" if percent >= 20 else "warning"

            # 6.7. Создать уведомления
            await self.notification_svc.create_for_users(
                user_ids=approver_ids,
                notification_type="task_discrepancy",
                title=f"Расхождение в заявке #{task_id}",
                message=(
                    f"Запланировано {total_planned}, доставлено {total_actual} "
                    f"({percent:.1f}%)"
                ),
                severity=severity,
                related_task_id=child["task_id"],
                metadata={"discrepancies": discrepancies, "parent_task_id": task_id},
            )

            # 6.8. Вернуть ответ (movements НЕ создаются, ждём подтверждения админа)
            return TaskCompleteResponse(
                task_id=task_id,
                status="pending_approval",
                message="Расхождение отправлено на подтверждение",
                child_task_id=child["task_id"],
            )

        # 7. Нет расхождений ИЛИ пользователь имеет право самостоятельно подтвердить
        # 7.1. Создать movements для всех позиций (с фактическими количествами)
        await self._create_movements_for_task(task_id, data.to_location_code, data.user_id)

        # 7.2. Завершить заявку (статус зависит от наличия расхождений)
        completed = await self.task_repo.complete_task(task_id)

        # 7.3. Вернуть результат
        return TaskCompleteResponse(
            task_id=task_id,
            status=completed["status"],  # completed или completed_with_discrepancy
        )

    # ============================================================
    # Подсказки (suggestions)
    # ============================================================

    async def get_suggestions(self, task_id: int) -> TaskSuggestionsResponse:
        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        rows = await self.task_repo.get_suggestions_raw(task_id)

        # Группируем по product_id
        products: dict = {}
        for row in rows:
            pid = row["product_id"]
            if pid not in products:
                products[pid] = {
                    "product_id": pid,
                    "quantity_needed": float(row["quantity_needed"]),
                    "locations": [],
                    "running_total": 0.0,
                }
            products[pid]["locations"].append(row)

        suggestions = []
        warnings = []
        for pid, pdata in products.items():
            locations = []
            running = 0.0
            needed = pdata["quantity_needed"]
            for loc in pdata["locations"]:
                qty = float(loc["quantity"])
                recommended = running < needed
                if recommended:
                    running += qty
                locations.append(
                    LocationSuggestion(
                        location_id=loc["location_id"],
                        location_code=loc["location_code"],
                        quantity=qty,
                        batch_number=loc["batch_number"],
                        created_at=loc["created_at"],
                        recommended=recommended,
                    )
                )

            available_total = sum(float(l["quantity"]) for l in pdata["locations"])

            suggestions.append(
                ProductSuggestion(
                    product_id=pid,
                    quantity_needed=needed,
                    available_total=available_total,
                    locations=locations,
                )
            )

            if available_total < needed:
                warnings.append({
                    "product_id": pid,
                    "message": (
                        f"В зоне доступно {available_total:.0f} шт, "
                        f"запланировано {needed:.0f} шт"
                    ),
                })

        return TaskSuggestionsResponse(
            task_id=task_id,
            from_zone=task["from_location_code"],
            suggestions=suggestions,
            warnings=warnings or None,
        )

    # ============================================================
    # Подтверждение расхождений (admin)
    # ============================================================

    async def approve_discrepancy(
        self, task_id: int, data: ApproveDiscrepancy
    ) -> ApproveDiscrepancyResponse:
        # Проверяем права
        if not await self.task_repo.check_user_can_approve(data.user_id):
            raise TaskPermissionDeniedError("Нет прав на подтверждение расхождений")

        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        if task["task_type"] != "discrepancy_approval":
            raise TaskInvalidStatusError("Это не заявка на подтверждение расхождений")

        parent_task_id = task["parent_task_id"]

        # Валидируем и обновляем количества
        warnings = []
        for item_id_str, quantity in data.approved_quantities.items():
            item_id = int(item_id_str)
            item = await self.task_repo.get_task_item_detail(item_id)
            if not item:
                raise TaskItemNotFoundError(f"Позиция #{item_id} не найдена")

            # Предупреждение если больше чем planned
            if quantity > item["quantity_planned"]:
                warnings.append({
                    "item_id": item_id,
                    "product_id": item["product_id"],
                    "quantity_approved": quantity,
                    "quantity_planned": float(item["quantity_planned"]),
                    "message": (
                        f"Вы вводите больше запланированного "
                        f"({quantity} > {item['quantity_planned']}). "
                        f"Это означает ошибку учёта."
                    ),
                })

            # Нельзя подтвердить больше чем есть в ячейке
            if item["from_location_id"]:
                available = await self.task_repo.get_inventory_qty_in_location(
                    item["product_id"], item["from_location_id"]
                )
                if quantity > available:
                    raise TaskInvalidStatusError(
                        f"Нельзя подтвердить {quantity} шт для товара {item['product_id']}. "
                        f"В ячейке {item['from_location_code']} доступно только {available:.0f} шт."
                    )

            await self.task_repo.update_approved_qty(item_id, quantity)

        # Получаем metadata задачи
        task_metadata_raw = task["metadata"]
        if isinstance(task_metadata_raw, str):
            # PostgreSQL вернул JSON как строку - парсим
            task_metadata = json.loads(task_metadata_raw) if task_metadata_raw else {}
        elif isinstance(task_metadata_raw, dict):
            # Уже dict - используем как есть
            task_metadata = task_metadata_raw
        else:
            # None или другой тип - пустой dict
            task_metadata = {}

        to_location_code = task_metadata.get("to_location_code", "")

        # Создаём movements для родительской заявки
        items = await self.task_repo.get_items_for_movements(parent_task_id)
        for item in items:
            await self.movement_service.create_movement([
                MovementCreate(
                    movement_type="transfer",
                    product_id=item["product_id"],
                    from_location_code=item["from_location_code"],
                    to_location_code=to_location_code,
                    quantity=int(item["quantity_actual"]),
                    batch_number=item["batch_number"],
                    container_code=None,
                    user_name=f"user_{data.user_id}",
                    reason=f"Task #{parent_task_id} (approved)",
                )
            ])

            # Adjustment если approved > planned
            orig_item = await self.task_repo.get_task_item_detail(item["item_id"])
            if orig_item and item["quantity_actual"] > orig_item["quantity_planned"]:
                diff = int(item["quantity_actual"] - orig_item["quantity_planned"])
                await self.movement_service.create_movement([
                    MovementCreate(
                        movement_type="adjust",
                        product_id=item["product_id"],
                        from_location_code=None,
                        to_location_code=item["from_location_code"],
                        quantity=diff,
                        batch_number=item["batch_number"],
                        container_code=None,
                        user_name=f"user_{data.user_id}",
                        reason=(
                            f"Task #{parent_task_id}: adjustment "
                            f"(approved {item['quantity_actual']} instead of "
                            f"{orig_item['quantity_planned']})"
                        ),
                    )
                ])

        # Обновляем статусы
        await self.task_repo.set_status(parent_task_id, "completed_with_discrepancy")
        await self.task_repo.set_status(task_id, "completed")

        return ApproveDiscrepancyResponse(
            parent_task_id=parent_task_id,
            status="approved",
            warnings=warnings or None,
        )

    async def reject_discrepancy(
        self, task_id: int, user_id: int
    ) -> RejectDiscrepancyResponse:
        """Отклонить расхождение — отменить и дочернюю и родительскую заявки"""
        if not await self.task_repo.check_user_can_approve(user_id):
            raise TaskPermissionDeniedError("Нет прав на отклонение расхождений")

        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        parent_task_id = task["parent_task_id"]
        await self.task_repo.set_status(task_id, "cancelled")
        if parent_task_id:
            await self.task_repo.set_status(parent_task_id, "cancelled")

        return RejectDiscrepancyResponse(status="rejected")

    async def send_to_recount(self, task_id: int, user_id: int) -> RecountResponse:
        """Отправить заявку на пересчёт"""
        if not await self.task_repo.check_user_can_approve(user_id):
            raise TaskPermissionDeniedError("Нет прав для отправки на пересчёт")

        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        parent_task_id = task["parent_task_id"]
        parent_task = await self.task_repo.get_by_id(parent_task_id) if parent_task_id else None

        recount_metadata = {
            "parent_task_id": parent_task_id,
            "notes": f"Пересчёт для заявки #{parent_task_id}",
        }

        recount = await self.task_repo.create_child_task(
            parent_task_id=parent_task_id or task_id,
            task_type="recount",
            assigned_to=parent_task["assigned_to"] if parent_task else None,
            metadata=recount_metadata,
            created_by= user_id
        )

        await self.task_repo.set_status(parent_task_id or task_id, "pending_recount")
        await self.task_repo.set_status(task_id, "waiting_recount")

        return RecountResponse(recount_task_id=recount["task_id"])

    # ============================================================
    # Пересчёт (recount)
    # ============================================================

    async def complete_recount(
        self, task_id: int, data: CompleteRecount
    ) -> CompleteRecountResponse:
        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Заявка #{task_id} не найдена")

        if task["task_type"] != "recount":
            raise TaskInvalidStatusError("Это не заявка на пересчёт")

        if task["assigned_to"] != data.user_id:
            raise TaskForbiddenError("Эта заявка не назначена на вас")

        # Сохраняем результаты пересчёта
        recount_results = []
        for item in data.items:
            current_qty = await self.task_repo.get_inventory_qty_by_location_code(
                item.product_id, item.location_code
            )
            recount_results.append({
                "product_id": item.product_id,
                "location_code": item.location_code,
                "quantity_in_system": current_qty,
                "quantity_counted": item.quantity_counted,
                "difference": item.quantity_counted - current_qty,
                "notes": item.notes,
            })

        record = await self.task_repo.save_recount_results(
            task_id=task_id,
            results={"items": recount_results},
            user_id=data.user_id,
        )
        if not record:
            raise TaskInvalidStatusError("Не удалось завершить пересчёт")

        parent_task_id = record["parent_task_id"]

        # Если есть родительская заявка — уведомляем approvers
        if parent_task_id:
            await self.task_repo.set_status(parent_task_id, "pending_approval")

            approver_rows = await self.task_repo.get_approvers()
            approver_ids = [r["user_id"] for r in approver_rows]

            await self.notification_svc.create_for_users(
                user_ids=approver_ids,
                notification_type="recount_completed",
                title=f"Пересчёт завершён для заявки #{parent_task_id}",
                message=f"Сотрудник завершил пересчёт. Требуется подтверждение.",
                severity="warning",
                related_task_id=task_id,
                metadata={"recount_results": recount_results, "parent_task_id": parent_task_id},
            )

        return CompleteRecountResponse(
            task_id=task_id,
            status=record["status"],
            parent_task_id=parent_task_id,
        )

    # ============================================================
    # Внутренние методы
    # ============================================================

    async def _create_movements_for_task(
        self, task_id: int, to_location_code: str, user_id: int
    ) -> None:
        """Создать movements для каждой позиции заявки"""
        items = await self.task_repo.get_items_for_movements(task_id)
        movements = [
            MovementCreate(
                movement_type="transfer",
                product_id=item["product_id"],
                from_location_code=item["from_location_code"],
                to_location_code=to_location_code,
                quantity=int(item["quantity_actual"]),
                batch_number=item["batch_number"],
                container_code=None,
                user_name=f"user_{user_id}",
                reason=f"Task #{task_id}",
            )
            for item in items
        ]
        if movements:
            await self.movement_service.create_movement(movements)
