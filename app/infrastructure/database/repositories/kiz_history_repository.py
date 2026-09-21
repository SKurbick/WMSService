"""Read-only persistence for one KIZ history."""

from app.infrastructure.database.queries import kiz_history as q


class KizHistoryRepository:
    def __init__(self, pool):
        self.pool = pool

    async def current_state(self, conn, kiz_code):
        return await conn.fetchrow(q.GET_CURRENT_STATE, kiz_code)

    async def timeline(self, conn, kiz_id):
        return await conn.fetch(q.GET_TIMELINE, kiz_id)
