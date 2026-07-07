"""API endpoints для уведомлений"""

from typing import List

from fastapi import APIRouter, Depends, Query

from app.core.schemas.notification import NotificationResponse, NotificationReadResponse
from app.core.services.notification_service import NotificationService
from app.api.v1.dependencies import get_notification_service

router = APIRouter(prefix="/notifications", tags=["Уведомления"])


@router.get(
    "/unread",
    response_model=List[NotificationResponse],
    summary="Получить непрочитанные уведомления",
    description="Возвращает список непрочитанных уведомлений для указанного пользователя.",
)
async def get_unread_notifications(
    user_id: int = Query(..., description="ID пользователя"),
    service: NotificationService = Depends(get_notification_service),
):
    """Получить непрочитанные уведомления пользователя"""
    return await service.get_unread(user_id)


@router.put(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
    summary="Пометить уведомление прочитанным",
    description="Устанавливает флаг прочтения для уведомления.",
)
async def mark_notification_read(
    notification_id: int,
    service: NotificationService = Depends(get_notification_service),
):
    """Пометить уведомление как прочитанное"""
    return await service.mark_read(notification_id)
