"""Репозиторий для работы с журналом отгрузок из ФБС зоны"""

import json
from datetime import datetime
from typing import List, Optional, Sequence

from asyncpg import Connection


CREATE_SHIPMENT = """
INSERT INTO wms.fbs_shipments (raw_message, total_items, status, source)
VALUES ($1::jsonb, $2, $3, $4)
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
    next_retry_at   = COALESCE($6, next_retry_at),
    updated_at      = now()
WHERE item_id = $1
"""

UPDATE_ITEM_TASK_RESULT = """
UPDATE wms.fbs_shipment_items
SET status = $2,
    task_resolution_status = $3,
    error_message = $4,
    movement_id = $5,
    retry_count = COALESCE($6, retry_count),
    next_retry_at = $7,
    updated_at = now()
WHERE item_id = $1
RETURNING item_id
"""


MARK_ITEMS_SUCCESS = """
UPDATE wms.fbs_shipment_items
SET status = 'success', movement_id = $1, error_message = NULL,
    next_retry_at = NULL, retry_count = COALESCE($3, retry_count), updated_at = now()
WHERE item_id = ANY($2::bigint[])
  AND status IN ('new', 'failed', 'pending_retry', 'retry_exhausted')
RETURNING item_id
"""

LOCK_ITEMS_FOR_PROCESSING = """
SELECT item_id, shipment_id, product_id, quantity, author, assembly_tasks,
       status, movement_id, retry_count, max_retries
FROM wms.fbs_shipment_items
WHERE item_id = ANY($1::bigint[])
FOR UPDATE
"""

GET_SUCCESS_LINKED_ASSEMBLY_TASKS = """
SELECT DISTINCT task.task_id
FROM wms.fbs_shipment_items AS item
CROSS JOIN LATERAL jsonb_array_elements_text(item.assembly_tasks) AS task(task_id)
WHERE task.task_id = ANY($1::text[])
  AND item.status = 'success'
  AND item.movement_id IS NOT NULL
"""

LOCK_ASSEMBLY_TASKS = """
SELECT task_id, is_shipped
FROM public.assembly_task
WHERE task_id = ANY($1::bigint[])
ORDER BY task_id
FOR UPDATE
"""

GET_CONFIRMED_TASK_LINKS = """
WITH requested(task_id) AS (
    SELECT unnest($1::text[])
), legacy_candidates AS (
    SELECT task.task_id,
           item.item_id AS existing_success_item_id,
           item.movement_id AS existing_movement_id,
           min(movement.created_at) AS existing_movement_created_at
    FROM wms.fbs_shipment_items AS item
    CROSS JOIN LATERAL jsonb_array_elements_text(item.assembly_tasks) AS task(task_id)
    JOIN requested ON requested.task_id = task.task_id
    JOIN wms.movements AS movement
      ON movement.movement_id = item.movement_id
     AND movement.product_id = item.product_id
     AND movement.movement_type = 'ship'
    WHERE item.status = 'success'
      AND item.product_id = $2
      AND item.movement_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM wms.fbs_shipment_task_results AS item_result
          WHERE item_result.item_id = item.item_id
      )
    GROUP BY task.task_id, item.item_id, item.movement_id
    HAVING count(*) = 1
), legacy_links AS (
    SELECT DISTINCT ON (task.task_id)
           task.*
    FROM legacy_candidates AS task
    ORDER BY task.task_id, task.existing_movement_created_at DESC,
             task.existing_success_item_id DESC
), task_links AS (
    SELECT DISTINCT ON (result.task_id::text)
           result.task_id::text AS task_id,
           result.item_id AS existing_success_item_id,
           result.movement_id AS existing_movement_id,
           result.movement_created_at AS existing_movement_created_at
    FROM wms.fbs_shipment_task_results AS result
    JOIN requested ON requested.task_id = result.task_id::text
    JOIN wms.movements AS movement
      ON movement.movement_id = result.movement_id
     AND movement.created_at = result.movement_created_at
     AND movement.product_id = result.product_id
     AND movement.movement_type = 'ship'
    WHERE result.outcome = 'written_off'
      AND result.product_id = $2
    ORDER BY result.task_id::text, result.updated_at DESC
)
SELECT * FROM task_links
UNION ALL
SELECT legacy_links.*
FROM legacy_links
WHERE NOT EXISTS (
    SELECT 1 FROM task_links WHERE task_links.task_id = legacy_links.task_id
)
"""

GET_WRITTEN_TASK_OCCURRENCES = """
SELECT item_id, occurrence_index, task_id, movement_id, movement_created_at,
       is_shipped_before, reason
FROM wms.fbs_shipment_task_results
WHERE item_id = ANY($1::bigint[])
  AND outcome = 'written_off'
"""

UPSERT_TASK_RESULT = """
INSERT INTO wms.fbs_shipment_task_results (
    shipment_id, item_id, occurrence_index, task_id, product_id, outcome,
    effect_quantity, movement_id, movement_created_at,
    existing_success_item_id, existing_movement_id,
    existing_movement_created_at, is_shipped_before, reason, details,
    attempt_count, first_processed_at, last_processed_at, last_error
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    $10, $11, $12, $13, $14, $15::jsonb,
    1, now(), now(), $16
)
ON CONFLICT (item_id, occurrence_index) DO UPDATE SET
    task_id = EXCLUDED.task_id,
    product_id = EXCLUDED.product_id,
    outcome = EXCLUDED.outcome,
    effect_quantity = EXCLUDED.effect_quantity,
    movement_id = EXCLUDED.movement_id,
    movement_created_at = EXCLUDED.movement_created_at,
    existing_success_item_id = EXCLUDED.existing_success_item_id,
    existing_movement_id = EXCLUDED.existing_movement_id,
    existing_movement_created_at = EXCLUDED.existing_movement_created_at,
    is_shipped_before = EXCLUDED.is_shipped_before,
    reason = EXCLUDED.reason,
    details = EXCLUDED.details,
    attempt_count = wms.fbs_shipment_task_results.attempt_count + 1,
    last_processed_at = now(),
    last_error = EXCLUDED.last_error,
    updated_at = now()
RETURNING result_id
"""

GET_TASK_RESULTS = """
SELECT result_id, shipment_id, item_id, occurrence_index, task_id, product_id,
       outcome, effect_quantity, movement_id, movement_created_at,
       existing_success_item_id, existing_movement_id,
       existing_movement_created_at, is_shipped_before, reason,
       attempt_count, first_processed_at, last_processed_at, last_error,
       created_at, updated_at
FROM wms.fbs_shipment_task_results
WHERE shipment_id = $1
  AND ($2::varchar IS NULL OR product_id = $2)
  AND ($3::varchar IS NULL OR outcome = $3)
  AND ($4::bigint IS NULL OR task_id = $4)
ORDER BY item_id, occurrence_index
LIMIT $5 OFFSET $6
"""

COUNT_TASK_RESULTS = """
SELECT count(*)::int
FROM wms.fbs_shipment_task_results
WHERE shipment_id = $1
  AND ($2::varchar IS NULL OR product_id = $2)
  AND ($3::varchar IS NULL OR outcome = $3)
  AND ($4::bigint IS NULL OR task_id = $4)
"""

GET_TASK_RESULTS_SUMMARY = """
SELECT count(*)::int AS total_tasks,
       count(*) FILTER (WHERE outcome = 'written_off')::int AS written_off,
       count(*) FILTER (WHERE outcome IN ('duplicate_skipped', 'duplicate_in_payload'))::int AS duplicate_skipped,
       count(*) FILTER (WHERE outcome = 'inconsistent')::int AS inconsistent,
       count(*) FILTER (WHERE outcome = 'not_found')::int AS not_found,
       count(*) FILTER (WHERE outcome = 'pending_retry')::int AS pending_retry,
       count(*) FILTER (WHERE outcome = 'failed')::int AS failed,
       COALESCE(sum(effect_quantity), 0)::int AS effect_quantity
FROM wms.fbs_shipment_task_results
WHERE shipment_id = $1
"""

GET_ITEM_BY_ID = """
SELECT item_id, shipment_id, product_id, quantity, author, supply_id, account,
       assembly_tasks, warehouse_id, delivery_type, wb_warehouse, shipment_date,
       status, task_resolution_status, error_message, retry_count, max_retries,
       next_retry_at, movement_id,
       created_at, updated_at
FROM wms.fbs_shipment_items WHERE item_id = $1
"""

GET_SHIPMENTS = """
SELECT shipment_id, received_at, total_items, status, source, error_message, completed_at
FROM wms.fbs_shipments
WHERE ($1::text IS NULL OR status = $1)
  AND ($2::timestamptz IS NULL OR received_at >= $2)
  AND ($3::timestamptz IS NULL OR received_at <= $3)
  AND ($4::text IS NULL OR source = $4)
ORDER BY received_at DESC
LIMIT $5 OFFSET $6
"""

COUNT_SHIPMENTS = """
SELECT count(*)::int
FROM wms.fbs_shipments
WHERE ($1::text IS NULL OR status = $1)
  AND ($2::timestamptz IS NULL OR received_at >= $2)
  AND ($3::timestamptz IS NULL OR received_at <= $3)
  AND ($4::text IS NULL OR source = $4)
"""

GET_SHIPMENT_BY_ID = """
SELECT shipment_id, received_at, raw_message, total_items, status, source, error_message, completed_at
FROM wms.fbs_shipments
WHERE shipment_id = $1
"""

GET_ITEMS_BY_SHIPMENT_ID = """
SELECT item_id, product_id, quantity, author, supply_id, account, assembly_tasks,
       status, task_resolution_status, error_message, retry_count, movement_id,
       created_at, updated_at
FROM wms.fbs_shipment_items
WHERE shipment_id = $1
ORDER BY item_id
"""

GET_SHIPMENTS_STATS = """
SELECT status, count(*)::int AS count
FROM wms.fbs_shipments
WHERE ($1::text IS NULL OR source = $1)
GROUP BY status
"""

GET_SHIPMENTS_BY_STATUS = """
SELECT shipment_id, raw_message
FROM wms.fbs_shipments
WHERE status = $1
ORDER BY shipment_id
"""

UPDATE_SHIPMENT_VALIDATION_FAILED = """
UPDATE wms.fbs_shipments
SET status = 'validation_failed', error_message = $2
WHERE shipment_id = $1
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
        source: str = "standard",
    ) -> int:
        """INSERT в fbs_shipments. Возвращает shipment_id."""
        row = await conn.fetchrow(
            CREATE_SHIPMENT,
            json.dumps(raw_message, ensure_ascii=False),
            total_items,
            status,
            source,
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

    async def update_item_task_result(
        self,
        conn: Connection,
        *,
        item_id: int,
        status: str,
        task_resolution_status: str,
        error_message: Optional[str],
        movement_id: Optional[int],
        retry_count: Optional[int] = None,
        next_retry_at: Optional[datetime] = None,
    ) -> bool:
        row = await conn.fetchrow(
            UPDATE_ITEM_TASK_RESULT,
            item_id,
            status,
            task_resolution_status,
            error_message,
            movement_id,
            retry_count,
            next_retry_at,
        )
        return row is not None

    async def mark_items_success_in_transaction(
        self,
        conn: Connection,
        *,
        item_ids: Sequence[int],
        movement_id: int,
        retry_count: Optional[int] = None,
    ) -> List[int]:
        rows = await conn.fetch(MARK_ITEMS_SUCCESS, movement_id, list(item_ids), retry_count)
        return [row["item_id"] for row in rows]

    async def lock_items_for_processing(self, conn: Connection, *, item_ids: Sequence[int]) -> list:
        """Блокирует FBS items до конца внешней product-group транзакции."""
        return await conn.fetch(LOCK_ITEMS_FOR_PROCESSING, list(item_ids))

    async def get_success_linked_assembly_tasks(
        self, conn: Connection, *, assembly_tasks: Sequence[str]
    ) -> set[str]:
        rows = await conn.fetch(GET_SUCCESS_LINKED_ASSEMBLY_TASKS, list(assembly_tasks))
        return {str(row["task_id"]) for row in rows}

    async def lock_assembly_tasks(
        self, conn: Connection, *, assembly_tasks: Sequence[str]
    ) -> dict[str, bool]:
        task_ids = sorted({int(task_id) for task_id in assembly_tasks})
        rows = await conn.fetch(LOCK_ASSEMBLY_TASKS, task_ids)
        return {str(row["task_id"]): row["is_shipped"] for row in rows}

    async def get_confirmed_task_links(
        self,
        conn: Connection,
        *,
        assembly_tasks: Sequence[str],
        product_id: str,
    ) -> dict[str, dict]:
        rows = await conn.fetch(
            GET_CONFIRMED_TASK_LINKS,
            list({str(task_id) for task_id in assembly_tasks}),
            product_id,
        )
        return {str(row["task_id"]): dict(row) for row in rows}

    async def get_written_task_occurrences(
        self, conn: Connection, *, item_ids: Sequence[int]
    ) -> dict[tuple[int, int], dict]:
        rows = await conn.fetch(GET_WRITTEN_TASK_OCCURRENCES, list(item_ids))
        return {(row["item_id"], row["occurrence_index"]): dict(row) for row in rows}

    async def mark_assembly_tasks_shipped(
        self, conn: Connection, *, assembly_tasks: Sequence[str]
    ) -> set[str]:
        task_ids = sorted({int(task_id) for task_id in assembly_tasks})
        rows = await conn.fetch(
            """
            UPDATE public.assembly_task
            SET is_shipped = TRUE
            WHERE task_id = ANY($1::bigint[]) AND is_shipped = FALSE
            RETURNING task_id
            """,
            task_ids,
        )
        return {str(row["task_id"]) for row in rows}

    async def upsert_task_results(self, conn: Connection, *, results: Sequence[dict]) -> None:
        for result in results:
            await conn.fetchrow(
                UPSERT_TASK_RESULT,
                result["shipment_id"],
                result["item_id"],
                result["occurrence_index"],
                int(result["task_id"]),
                result["product_id"],
                result["outcome"],
                result.get("effect_quantity", 0),
                result.get("movement_id"),
                result.get("movement_created_at"),
                result.get("existing_success_item_id"),
                result.get("existing_movement_id"),
                result.get("existing_movement_created_at"),
                result.get("is_shipped_before"),
                result.get("reason"),
                json.dumps(result.get("details", {}), ensure_ascii=False),
                result.get("last_error"),
            )

    async def get_item_by_id(self, conn: Connection, item_id: int):
        return await conn.fetchrow(GET_ITEM_BY_ID, item_id)

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
        source: Optional[str] = None,
    ) -> tuple:
        """Список shipments с фильтрацией. Возвращает (записи, total_count)."""
        rows = await conn.fetch(GET_SHIPMENTS, status, date_from, date_to, source, limit, offset)
        total = await conn.fetchval(COUNT_SHIPMENTS, status, date_from, date_to, source)
        return rows, total

    async def get_shipment_by_id(self, conn: Connection, shipment_id: int):
        """Один shipment по ID."""
        return await conn.fetchrow(GET_SHIPMENT_BY_ID, shipment_id)

    async def get_items_by_shipment_id(self, conn: Connection, shipment_id: int) -> list:
        """Все items конкретного shipment."""
        return await conn.fetch(GET_ITEMS_BY_SHIPMENT_ID, shipment_id)

    async def get_task_results(
        self,
        conn: Connection,
        *,
        shipment_id: int,
        product_id: Optional[str] = None,
        outcome: Optional[str] = None,
        task_id: Optional[int] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list, int, object]:
        rows = await conn.fetch(
            GET_TASK_RESULTS,
            shipment_id,
            product_id,
            outcome,
            task_id,
            limit,
            offset,
        )
        total = await conn.fetchval(COUNT_TASK_RESULTS, shipment_id, product_id, outcome, task_id)
        summary = await conn.fetchrow(GET_TASK_RESULTS_SUMMARY, shipment_id)
        return rows, total, summary

    async def get_shipments_stats(self, conn: Connection, source: Optional[str] = None) -> list:
        """GROUP BY status — один запрос."""
        return await conn.fetch(GET_SHIPMENTS_STATS, source)

    async def get_shipments_by_status(self, conn: Connection, status: str) -> list:
        """Все shipments с указанным статусом (shipment_id + raw_message)."""
        return await conn.fetch(GET_SHIPMENTS_BY_STATUS, status)

    async def mark_validation_failed(
        self, conn: Connection, shipment_id: int, error_message: str
    ) -> None:
        await conn.execute(UPDATE_SHIPMENT_VALIDATION_FAILED, shipment_id, error_message)

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
        return await conn.fetch(
            """
            SELECT item_id, shipment_id, product_id, quantity, author,
                   supply_id, account, assembly_tasks, warehouse_id,
                   delivery_type, wb_warehouse, shipment_date,
                   retry_count, max_retries
            FROM wms.fbs_shipment_items
            WHERE status = 'pending_retry'
              AND next_retry_at <= now()
            ORDER BY next_retry_at ASC
        """
        )
