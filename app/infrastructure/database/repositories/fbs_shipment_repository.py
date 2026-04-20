"""Репозиторий для работы с журналом отгрузок из ФБС зоны"""

import json
from datetime import datetime
from typing import List, Optional

from asyncpg import Connection


CREATE_SHIPMENT = """
INSERT INTO wms.fbs_shipments (raw_message, total_items, status)
VALUES ($1::jsonb, $2, $3)
RETURNING shipment_id
"""

CREATE_SHIPMENT_ITEM = """
INSERT INTO wms.fbs_shipment_items (
    shipment_id, product_id, quantity, author, supply_id, account,
    assembly_tasks, warehouse_id, delivery_type, wb_warehouse, shipment_date
)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
RETURNING item_id
"""

UPDATE_ITEM_STATUS = """
UPDATE wms.fbs_shipment_items
SET
    status          = $2,
    error_message   = COALESCE($3, error_message),
    movement_id     = COALESCE($4, movement_id),
    retry_count     = COALESCE($5, retry_count),
    next_retry_at   = COALESCE($6, next_retry_at)
WHERE item_id = $1
"""

GET_SHIPMENTS = """
SELECT shipment_id, received_at, total_items, status, error_message, completed_at
FROM wms.fbs_shipments
WHERE ($1::text IS NULL OR status = $1)
  AND ($2::timestamptz IS NULL OR received_at >= $2)
  AND ($3::timestamptz IS NULL OR received_at <= $3)
ORDER BY received_at DESC
LIMIT $4 OFFSET $5
"""

COUNT_SHIPMENTS = """
SELECT count(*)::int
FROM wms.fbs_shipments
WHERE ($1::text IS NULL OR status = $1)
  AND ($2::timestamptz IS NULL OR received_at >= $2)
  AND ($3::timestamptz IS NULL OR received_at <= $3)
"""

GET_SHIPMENT_BY_ID = """
SELECT shipment_id, received_at, raw_message, total_items, status, error_message, completed_at
FROM wms.fbs_shipments
WHERE shipment_id = $1
"""

GET_ITEMS_BY_SHIPMENT_ID = """
SELECT item_id, product_id, quantity, author, supply_id, account, assembly_tasks,
       status, error_message, retry_count, movement_id, created_at, updated_at
FROM wms.fbs_shipment_items
WHERE shipment_id = $1
ORDER BY item_id
"""

GET_SHIPMENTS_STATS = """
SELECT status, count(*)::int AS count
FROM wms.fbs_shipments
GROUP BY status
"""

GET_SHIPMENTS_BY_STATUS = """
SELECT shipment_id, raw_message
FROM wms.fbs_shipments
WHERE status = $1
ORDER BY shipment_id
"""

UPDATE_SHIPMENT_ERROR = """
UPDATE wms.fbs_shipments
SET error_message = $2
WHERE shipment_id = $1
"""

UPDATE_SHIPMENT_STATUS = """
UPDATE wms.fbs_shipments
SET
    status       = CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status NOT IN ('success')
        ) THEN 'completed'

        WHEN NOT EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status NOT IN ('failed', 'retry_exhausted')
        ) THEN 'failed'

        WHEN EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status = 'pending_retry'
        ) THEN 'processing'

        ELSE 'partially_completed'
    END,
    completed_at = CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status NOT IN ('success')
        ) THEN now()
        WHEN NOT EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status NOT IN ('failed', 'retry_exhausted')
        ) THEN now()
        WHEN NOT EXISTS (
            SELECT 1 FROM wms.fbs_shipment_items
            WHERE shipment_id = $1 AND status IN ('pending_retry', 'new', 'success')
        ) THEN now()
        ELSE NULL
    END
WHERE shipment_id = $1
"""


class FbsShipmentRepository:
    """Репозиторий для журнала отгрузок ФБС.

    Все методы принимают conn (asyncpg Connection), а не pool —
    чтобы можно было использовать внутри внешней транзакции.
    """

    async def create_shipment(
        self,
        conn: Connection,
        raw_message: dict,
        total_items: int,
        status: str = "processing",
    ) -> int:
        """INSERT в fbs_shipments. Возвращает shipment_id."""
        row = await conn.fetchrow(
            CREATE_SHIPMENT,
            json.dumps(raw_message, ensure_ascii=False),
            total_items,
            status,
        )
        return row["shipment_id"]

    async def create_shipment_items(
        self,
        conn: Connection,
        shipment_id: int,
        items: List[dict],
    ) -> List[int]:
        """Batch INSERT в fbs_shipment_items. Возвращает список item_id."""
        item_ids = []
        for item in items:
            row = await conn.fetchrow(
                CREATE_SHIPMENT_ITEM,
                shipment_id,
                item["product_id"],
                item["quantity"],
                item["author"],
                item["supply_id"],
                item["account"],
                json.dumps(item["assembly_tasks"], ensure_ascii=False),
                item["warehouse_id"],
                item["delivery_type"],
                item.get("wb_warehouse"),
                item.get("shipment_date"),
            )
            item_ids.append(row["item_id"])
        return item_ids

    async def update_item_status(
        self,
        conn: Connection,
        item_id: int,
        status: str,
        error_message: Optional[str] = None,
        movement_id: Optional[int] = None,
        retry_count: Optional[int] = None,
        next_retry_at: Optional[datetime] = None,
    ) -> None:
        """UPDATE одной позиции — статус и связанные поля."""
        await conn.execute(
            UPDATE_ITEM_STATUS,
            item_id,
            status,
            error_message,
            movement_id,
            retry_count,
            next_retry_at,
        )

    async def update_shipment_status(
        self,
        conn: Connection,
        shipment_id: int,
    ) -> None:
        """Пересчитывает статус shipment на основе статусов всех items.

        - Все success              → completed   (completed_at = now())
        - Все failed/retry_exh.   → failed       (completed_at = now())
        - Есть pending_retry       → processing   (completed_at = NULL)
        - Микс success+failed/exh. → partially_completed (completed_at = now())
        """
        await conn.execute(UPDATE_SHIPMENT_STATUS, shipment_id)

    async def get_shipments(
        self,
        conn: Connection,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> tuple:
        """Список shipments с фильтрацией. Возвращает (записи, total_count)."""
        rows = await conn.fetch(GET_SHIPMENTS, status, date_from, date_to, limit, offset)
        total = await conn.fetchval(COUNT_SHIPMENTS, status, date_from, date_to)
        return rows, total

    async def get_shipment_by_id(self, conn: Connection, shipment_id: int):
        """Один shipment по ID."""
        return await conn.fetchrow(GET_SHIPMENT_BY_ID, shipment_id)

    async def get_items_by_shipment_id(self, conn: Connection, shipment_id: int) -> list:
        """Все items конкретного shipment."""
        return await conn.fetch(GET_ITEMS_BY_SHIPMENT_ID, shipment_id)

    async def get_shipments_stats(self, conn: Connection) -> list:
        """GROUP BY status — один запрос."""
        return await conn.fetch(GET_SHIPMENTS_STATS)

    async def get_shipments_by_status(self, conn: Connection, status: str) -> list:
        """Все shipments с указанным статусом (shipment_id + raw_message)."""
        return await conn.fetch(GET_SHIPMENTS_BY_STATUS, status)

    async def update_shipment_error(
        self,
        conn: Connection,
        shipment_id: int,
        error_message: str,
    ) -> None:
        """Обновляет error_message (статус не трогает — остаётся validation_failed)."""
        await conn.execute(UPDATE_SHIPMENT_ERROR, shipment_id, error_message)

    async def get_pending_retry_items(self, conn: Connection) -> list:
        """Возвращает все items со статусом pending_retry у которых next_retry_at <= now()."""
        return await conn.fetch("""
            SELECT item_id, shipment_id, product_id, quantity, author,
                   supply_id, account, assembly_tasks, warehouse_id,
                   delivery_type, wb_warehouse, shipment_date,
                   retry_count, max_retries
            FROM wms.fbs_shipment_items
            WHERE status = 'pending_retry'
              AND next_retry_at <= now()
            ORDER BY next_retry_at ASC
        """)
