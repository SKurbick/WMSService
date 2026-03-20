"""API endpoints для системы заявок"""

from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query, status

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
    ApproveDiscrepancy,
    ApproveDiscrepancyResponse,
    RejectDiscrepancyResponse,
    RecountResponse,
    CompleteRecount,
    CompleteRecountResponse,
)
from app.core.services.task_service import TaskService
from app.api.v1.dependencies import get_task_service

router = APIRouter(prefix="/tasks", tags=["Заявки"])


# ============================================================
# Список и детальная карточка
# ============================================================


@router.get("", response_model=List[TaskListResponse])
async def get_tasks(
    status_filter: Optional[str] = Query(None, alias="status", description="Фильтр по статусу"),
    task_type: Optional[str] = Query(None, description="Фильтр по типу заявки"),
    assigned_to: Optional[int] = Query(None, description="Фильтр по исполнителю"),
    from_location_id: Optional[int] = Query(None, description="Фильтр по зоне-источнику"),
    to_location_id: Optional[int] = Query(None, description="Фильтр по зоне-назначению"),
    from_date: Optional[date] = Query(None, description="Дата начала периода"),
    to_date: Optional[date] = Query(None, description="Дата окончания периода"),
    hide_child_tasks: bool = Query(True, description="Скрывать дочерние заявки из основного списка"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: TaskService = Depends(get_task_service),
):
    """Получить список заявок с фильтрами"""
    return await service.get_tasks(
        status=status_filter,
        task_type=task_type,
        assigned_to=assigned_to,
        from_location_id=from_location_id,
        to_location_id=to_location_id,
        from_date=from_date,
        to_date=to_date,
        hide_child_tasks=hide_child_tasks,
        limit=limit,
        offset=offset,
    )


@router.get("/my", response_model=List[TaskMyResponse])
async def get_my_tasks(
    user_id: int = Query(..., description="ID сотрудника"),
    service: TaskService = Depends(get_task_service),
):
    """Получить активные заявки сотрудника (для ТСД)"""
    return await service.get_my_tasks(user_id)


@router.get("/available", response_model=List[TaskListResponse])
async def get_available_tasks(
    limit: int = Query(20, ge=1, le=100),
    service: TaskService = Depends(get_task_service),
):
    """Получить свободные заявки (не назначенные, pending)"""
    return await service.get_available_tasks(limit)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
):
    """Получить детальную карточку заявки с позициями"""
    return await service.get_task_by_id(task_id)


# ============================================================
# CRUD
# ============================================================


@router.post("", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    service: TaskService = Depends(get_task_service),
):
    """Создать заявку"""
    return await service.create_task(data)


@router.put("/{task_id}", response_model=TaskUpdateResponse)
async def update_task(
    task_id: int,
    data: TaskUpdate,
    service: TaskService = Depends(get_task_service),
):
    """Обновить заявку (только pending/assigned)"""
    return await service.update_task(task_id, data)


@router.delete("/{task_id}", response_model=TaskCancelResponse)
async def cancel_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
):
    """Отменить заявку (только pending/assigned)"""
    return await service.cancel_task(task_id)


# ============================================================
# ТСД-операции
# ============================================================


@router.put("/{task_id}/assign", response_model=TaskAssignResponse)
async def assign_task(
    task_id: int,
    data: TaskAssign,
    service: TaskService = Depends(get_task_service),
):
    """Взять заявку в работу"""
    return await service.assign_task(task_id, data)


@router.put("/{task_id}/start", response_model=TaskStartResponse)
async def start_task(
    task_id: int,
    data: TaskStart,
    service: TaskService = Depends(get_task_service),
):
    """Начать выполнение заявки"""
    return await service.start_task(task_id, data)


@router.put("/{task_id}/complete", response_model=TaskCompleteResponse)
async def complete_task(
    task_id: int,
    data: TaskComplete,
    service: TaskService = Depends(get_task_service),
):
    """Завершить заявку с указанием фактических данных"""
    return await service.complete_task(task_id, data)


@router.get("/{task_id}/suggestions", response_model=TaskSuggestionsResponse)
async def get_task_suggestions(
    task_id: int,
    service: TaskService = Depends(get_task_service),
):
    """Получить подсказки по ячейкам для выполнения заявки (FIFO)"""
    return await service.get_suggestions(task_id)


# ============================================================
# Операции для администратора (расхождения)
# ============================================================


@router.put("/{task_id}/approve-discrepancy", response_model=ApproveDiscrepancyResponse)
async def approve_discrepancy(
    task_id: int,
    data: ApproveDiscrepancy,
    service: TaskService = Depends(get_task_service),
):
    """Подтвердить расхождение (только admin)"""
    return await service.approve_discrepancy(task_id, data)


@router.put("/{task_id}/reject-discrepancy", response_model=RejectDiscrepancyResponse)
async def reject_discrepancy(
    task_id: int,
    user_id: int = Query(..., description="ID администратора"),
    service: TaskService = Depends(get_task_service),
):
    """Отклонить расхождение — отменить заявку (только admin)"""
    return await service.reject_discrepancy(task_id, user_id)


@router.put("/{task_id}/recount", response_model=RecountResponse)
async def send_to_recount(
    task_id: int,
    user_id: int = Query(..., description="ID администратора"),
    service: TaskService = Depends(get_task_service),
):
    """Отправить на пересчёт (только admin)"""
    return await service.send_to_recount(task_id, user_id)


@router.put("/{task_id}/complete-recount", response_model=CompleteRecountResponse)
async def complete_recount(
    task_id: int,
    data: CompleteRecount,
    service: TaskService = Depends(get_task_service),
):
    """Завершить заявку на пересчёт"""
    return await service.complete_recount(task_id, data)
