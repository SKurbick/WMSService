"""asyncpg boundary for inventory stage 1; no physical mutation API exists here."""

import json

from app.infrastructure.database.queries import inventory_documents as q


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


class InventoryDocumentRepository:
    def __init__(self, pool):
        self.pool = pool

    async def document(self, conn, document_id):
        row = await conn.fetchrow(q.GET_DOCUMENT, document_id)
        if row is None:
            return None
        result = dict(row)
        result["locations"] = decode(result["locations"])
        result["product_ids"] = list(result["product_ids"])
        return result

    async def capture(self, conn, location_ids, product_ids):
        result = {"observed_at": await conn.fetchval("SELECT clock_timestamp()")}
        for name, sql in (
            ("stocks", q.GET_STOCK),
            ("kiz", q.GET_KIZ),
            ("movements", q.GET_MOVEMENTS),
            ("kiz_events", q.GET_EVENTS),
        ):
            result[name] = [dict(row) for row in await conn.fetch(sql, location_ids, product_ids)]
            if len(result[name]) > 100000:
                from app.core.inventory_document_errors import InventoryDocumentError

                raise InventoryDocumentError(
                    "Evidence limit exceeded; select a smaller scope",
                    code="INVENTORY_EVIDENCE_LIMIT",
                )
        return result

    async def revision(self, conn, document_id, number):
        row = await conn.fetchrow(q.GET_REVISION, document_id, number)
        return decode(row["evidence"]) if row else None
