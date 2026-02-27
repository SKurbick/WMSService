"""Репозиторий для уведомлений"""

import json
from typing import List, Optional
from asyncpg import Pool, Record

from app.infrastructure.database.queries import notifications as queries


class NotificationRepository:
    """Репозиторий для таблицы wms.notifications"""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_unread(self, user_id: int) -> List[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_UNREAD_NOTIFICATIONS, user_id)

    async def mark_read(self, notification_id: int) -> Optional[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.MARK_NOTIFICATION_READ, notification_id)

    async def create(
        self,
        user_id: int,
        notification_type: str,
        title: str,
        message: str,
        severity: str,
        related_task_id: Optional[int],
        metadata: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                queries.CREATE_NOTIFICATION,
                user_id,
                notification_type,
                title,
                message,
                severity,
                related_task_id,
                json.dumps(metadata or {}, ensure_ascii=False),
            )
            return row["notification_id"]
