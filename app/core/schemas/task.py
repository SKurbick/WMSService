"""Pydantic схемы для системы заявок"""

from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================
# Вспомогательные схемы
# ============================================================


class TaskItemCreate(BaseModel):
    """Позиция заявки при создании"""

    product_id: str = Field(..., description="ID товара")
    quantity_planned: int = Field(..., gt=0, description="Запланированное количество")
    batch_number: Optional[str] = Field(None, description="Номер партии")


class TaskItemComplete(BaseModel):
    """Позиция заявки при завершении (фактические данные)"""

    item_id: int = Field(..., description="ID позиции заявки")
    quantity_actual: int = Field(..., ge=0, description="Фактическое количество")
    from_location_code: str = Field(..., description="Код ячейки, откуда взяли товар")
    discrepancy_reason: Optional[str] = Field(None, description="Причина расхождения")


class TaskItemResponse(BaseModel):
    """Позиция заявки в ответе"""

    item_id: int
    product_id: str
    product_name: Optional[str] = None
    quantity_planned: float
    quantity_actual: Optional[float] = None
    from_location_id: Optional[int] = None
    from_location_code: Optional[str] = None
    batch_number: Optional[str] = None
    discrepancy_reason: Optional[str] = None
    has_discrepancy: Optional[bool] = None
    discrepancy_qty: Optional[float] = None
    discrepancy_percent: Optional[float] = None

    class Config:
        from_attributes = True


# ============================================================
# Схемы для заявок (CRUD)
# ============================================================


class TaskCreate(BaseModel):
    """Создание заявки"""

    task_type: str = Field(..., description="Тип заявки: replenishment, transfer, picking, putaway")
    priority: int = Field(5, ge=1, le=10, description="Приоритет: 1 (высший) – 10 (низший)")
    from_location_code: str = Field(..., description="Код зоны-источника")
    to_location_code: str = Field(..., description="Код зоны-назначения")
    due_date: Optional[datetime] = Field(None, description="Срок выполнения")
    reason: Optional[str] = Field(None, description="Причина создания заявки")
    notes: Optional[str] = Field(None, description="Дополнительные заметки")
    items: List[TaskItemCreate] = Field(..., min_length=1, description="Список товаров")
    created_by: int = Field(..., description="ID пользователя-создателя")


class TaskUpdate(BaseModel):
    """Обновление заявки (только незавершённые)"""

    priority: Optional[int] = Field(None, ge=1, le=10)
    due_date: Optional[datetime] = None
    notes: Optional[str] = None


class ChildTaskSummary(BaseModel):
    """Дочерняя заявка в списке родителя"""

    task_id: int
    task_type: str
    status: str
    priority: int
    from_location_code: Optional[str] = None
    to_location_code: Optional[str] = None
    assigned_to: Optional[int] = None
    created_by: int
    due_date: Optional[datetime] = None
    items_count: int = 0
    total_quantity_planned: float = 0
    total_quantity_actual: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """Заявка в списке (без позиций)"""

    task_id: int
    task_type: str
    status: str
    priority: int
    parent_task_id: Optional[int] = None
    from_location_code: Optional[str] = None
    from_zone_type: Optional[str] = None
    to_location_code: Optional[str] = None
    to_zone_type: Optional[str] = None
    assigned_to: Optional[int] = None
    created_by: int
    due_date: Optional[datetime] = None
    reason: Optional[str] = None
    items_count: int = 0
    total_quantity_planned: float = 0
    total_quantity_actual: Optional[float] = None
    created_at: datetime
    waiting_minutes: Optional[float] = None
    child_tasks: List[ChildTaskSummary] = []

    class Config:
        from_attributes = True


class TaskDetailResponse(BaseModel):
    """Детальная карточка заявки (с позициями)"""

    task_id: int
    task_type: str
    status: str
    priority: int
    from_location_id: Optional[int] = None
    from_location_code: Optional[str] = None
    to_location_id: Optional[int] = None
    to_location_code: Optional[str] = None
    assigned_to: Optional[int] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    parent_task_id: Optional[int] = None
    created_by: int
    metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    items: List[TaskItemResponse] = []

    class Config:
        from_attributes = True


class TaskCreateResponse(BaseModel):
    """Ответ при создании заявки"""

    task_id: int
    task_type: str
    status: str
    items_count: int
    created_at: datetime
    warnings: Optional[List[dict]] = None

    class Config:
        from_attributes = True


class TaskUpdateResponse(BaseModel):
    """Ответ при обновлении заявки"""

    task_id: int
    status: str
    priority: int
    due_date: Optional[datetime] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class TaskCancelResponse(BaseModel):
    """Ответ при отмене заявки"""

    task_id: int
    status: str

    class Config:
        from_attributes = True


# ============================================================
# Схемы для ТСД-операций
# ============================================================


class TaskAssign(BaseModel):
    """Взять заявку в работу"""

    user_id: int = Field(..., description="ID сотрудника")


class TaskAssignResponse(BaseModel):
    """Ответ при назначении заявки"""

    task_id: int
    status: str
    assigned_to: int
    assigned_at: datetime

    class Config:
        from_attributes = True


class TaskStart(BaseModel):
    """Начать выполнение заявки"""

    user_id: int = Field(..., description="ID сотрудника")


class TaskStartResponse(BaseModel):
    """Ответ при старте заявки"""

    task_id: int
    status: str
    started_at: datetime
    warnings: Optional[List[dict]] = None

    class Config:
        from_attributes = True


class TaskComplete(BaseModel):
    """Завершение заявки с фактическими данными"""

    user_id: int = Field(..., description="ID сотрудника")
    items: List[TaskItemComplete] = Field(..., min_length=1)
    to_location_code: str = Field(..., description="Код ячейки куда положили товар")


class TaskCompleteResponse(BaseModel):
    """Ответ при завершении заявки"""

    task_id: int
    status: str
    message: Optional[str] = None
    child_task_id: Optional[int] = None

    class Config:
        from_attributes = True


class TaskMyResponse(BaseModel):
    """Активная заявка сотрудника (для ТСД)"""

    task_id: int
    task_type: str
    priority: int
    status: str
    from_location_code: Optional[str] = None
    to_location_code: Optional[str] = None
    items_count: int = 0
    total_quantity_planned: float = 0
    due_date: Optional[datetime] = None
    started_at: Optional[datetime] = None
    execution_minutes: Optional[float] = None

    class Config:
        from_attributes = True


# ============================================================
# Схемы для подсказок (suggestions)
# ============================================================


class LocationSuggestion(BaseModel):
    """Рекомендуемая ячейка для забора товара"""

    location_id: int
    location_code: str
    quantity: float
    batch_number: Optional[str] = None
    created_at: Optional[datetime] = None
    recommended: bool

    class Config:
        from_attributes = True


class ProductSuggestion(BaseModel):
    """Подсказки для одного товара"""

    product_id: str
    quantity_needed: float
    available_total: float
    locations: List[LocationSuggestion]


class TaskSuggestionsResponse(BaseModel):
    """Подсказки по ячейкам для заявки"""

    task_id: int
    from_zone: Optional[str] = None
    suggestions: List[ProductSuggestion]
    warnings: Optional[List[dict]] = None


# ============================================================
# Схемы для подтверждения расхождений (admin)
# ============================================================


class ApproveDiscrepancy(BaseModel):
    """Подтверждение расхождений"""

    user_id: int = Field(..., description="ID администратора")
    approved_quantities: dict = Field(
        ..., description="Словарь {item_id: quantity}: подтверждённые количества"
    )


class ApproveDiscrepancyResponse(BaseModel):
    """Ответ при подтверждении расхождений"""

    parent_task_id: int
    status: str
    warnings: Optional[List[dict]] = None


class RejectDiscrepancyResponse(BaseModel):
    """Ответ при отклонении расхождения"""

    status: str


class RecountResponse(BaseModel):
    """Ответ при отправке на пересчёт"""

    recount_task_id: int


# ============================================================
# Схемы для заявки на пересчёт
# ============================================================


class RecountItem(BaseModel):
    """Позиция при выполнении пересчёта"""

    product_id: str
    quantity_counted: int = Field(..., ge=0)
    location_code: str
    notes: Optional[str] = None


class CompleteRecount(BaseModel):
    """Завершение заявки на пересчёт"""

    user_id: int
    items: List[RecountItem] = Field(..., min_length=1)


class CompleteRecountResponse(BaseModel):
    """Ответ при завершении пересчёта"""

    task_id: int
    status: str
    parent_task_id: Optional[int] = None

    class Config:
        from_attributes = True
