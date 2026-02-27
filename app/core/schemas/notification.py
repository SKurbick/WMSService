"""Pydantic схемы для уведомлений"""

from typing import Optional
from pydantic import BaseModel
from datetime import datetime


class NotificationResponse(BaseModel):
    """Уведомление в ответе API"""

    notification_id: int
    notification_type: str
    title: str
    message: str
    severity: str
    related_task_id: Optional[int] = None
    metadata: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationReadResponse(BaseModel):
    """Ответ при пометке уведомления прочитанным"""

    notification_id: int
    is_read: bool

    class Config:
        from_attributes = True
