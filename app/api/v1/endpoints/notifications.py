"""API endpoints для уведомлений"""

from typing import List

from fastapi import APIRouter, Depends, Query

from app.core.schemas.notification import NotificationResponse, NotificationReadResponse
from app.core.services.notification_service import NotificationService
from app.api.v1.dependencies import get_notification_service

router = APIRouter(prefix="/notifications", tags=["Уведомления"])


@router.get("/unread", response_model=List[NotificationResponse])
async def get_unread_notifications(
    user_id: int = Query(..., description="ID пользователя"),
    service: NotificationService = Depends(get_notification_service),
):
    """Получить непрочитанные уведомления пользователя"""
    return await service.get_unread(user_id)


@router.put("/{notification_id}/read", response_model=NotificationReadResponse)
async def mark_notification_read(
    notification_id: int,
    service: NotificationService = Depends(get_notification_service),
):
    """Пометить уведомление как прочитанное"""
    return await service.mark_read(notification_id)
