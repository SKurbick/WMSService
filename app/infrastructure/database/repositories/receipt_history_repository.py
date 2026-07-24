"""Read-only repository истории поступления."""

from asyncpg import Pool

from app.infrastructure.database.queries import receipt_history as queries


class ReceiptHistoryRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    async def get_history(self, guid: str, limit: int, offset: int):
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                snapshot = await connection.fetch(queries.GET_CURRENT_SNAPSHOT, guid)
                total = await connection.fetchval(queries.COUNT_REVISIONS, guid)
                headers = await connection.fetch(queries.GET_REVISION_HEADERS, guid, limit, offset)
                timestamp_keys = [
                    row["revision_key_at"] for row in headers if row["revision_key_at"] is not None
                ]
                fallback_ids = [
                    row["fallback_id"] for row in headers if row["fallback_id"] is not None
                ]
                items = (
                    await connection.fetch(
                        queries.GET_REVISION_ITEMS, guid, timestamp_keys, fallback_ids
                    )
                    if headers
                    else []
                )
                return snapshot, total, headers, items
