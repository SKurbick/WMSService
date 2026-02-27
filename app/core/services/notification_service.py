"""Сервис для работы с уведомлениями"""

from typing import List
from app.core.schemas.notification import NotificationResponse, NotificationReadResponse
from app.infrastructure.database.repositories.notification_repository import NotificationRepository
from app.core.exceptions import NotificationNotFoundError


class NotificationService:
    """Сервис уведомлений"""

    def __init__(self, repository: NotificationRepository):
        self.repo = repository

    async def get_unread(self, user_id: int) -> List[NotificationResponse]:
        """Получить непрочитанные уведомления пользователя"""
        records = await self.repo.get_unread(user_id)
        return [NotificationResponse.model_validate(dict(r)) for r in records]

    async def mark_read(self, notification_id: int) -> NotificationReadResponse:
        """Пометить уведомление как прочитанное"""
        record = await self.repo.mark_read(notification_id)
        if not record:
            raise NotificationNotFoundError(
                f"Уведомление {notification_id} не найдено"
            )
        return NotificationReadResponse.model_validate(dict(record))

    async def create_for_users(
        self,
        user_ids: List[int],
        notification_type: str,
        title: str,
        message: str,
        severity: str,
        related_task_id: int,
        metadata: dict,
    ) -> None:
        """Создать уведомление для списка пользователей"""
        for user_id in user_ids:
            await self.repo.create(
                user_id=user_id,
                notification_type=notification_type,
                title=title,
                message=message,
                severity=severity,
                related_task_id=related_task_id,
                metadata=metadata,
            )
