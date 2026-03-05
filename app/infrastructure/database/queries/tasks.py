"""SQL запросы для системы заявок"""

# ============================================================
# LIST / DETAIL
# ============================================================

GET_TASKS = """
SELECT *
FROM wms.v_tasks_with_users
WHERE ($1::varchar  IS NULL OR status           = $1)
  AND ($2::varchar  IS NULL OR task_type        = $2)
  AND ($3::integer  IS NULL OR assigned_to      = $3)
  AND ($4::bigint   IS NULL OR from_location_id = $4)
  AND ($5::bigint   IS NULL OR to_location_id   = $5)
  AND ($6::date     IS NULL OR created_at      >= $6)
  AND ($7::date     IS NULL OR created_at      <= $7)
ORDER BY
    CASE status
        WHEN 'in_progress' THEN 1
        WHEN 'assigned'    THEN 2
        WHEN 'pending'     THEN 3
        ELSE 4
    END,
    priority  ASC,
    due_date  ASC NULLS LAST
LIMIT $8 OFFSET $9;
"""

GET_TASK_BY_ID = """
SELECT *
FROM wms.v_tasks_with_users
WHERE task_id = $1;
"""

GET_TASK_ITEMS = """
SELECT *
FROM wms.get_task_items_summary($1);
"""

# ============================================================
# CREATE
# ============================================================

CREATE_TASK = """
INSERT INTO wms.tasks (
    task_type, status, priority,
    from_location_id, to_location_id,
    due_date, reason, notes, created_by
)
VALUES (
    $1, 'pending', $2,
    (SELECT location_id FROM wms.locations WHERE location_code = $3),
    (SELECT location_id FROM wms.locations WHERE location_code = $4),
    $5, $6, $7, $8
)
RETURNING task_id, task_type, status, created_at;
"""

INSERT_TASK_ITEM = """
INSERT INTO wms.task_items (task_id, product_id, quantity_planned, batch_number)
VALUES ($1, $2, $3, $4)
RETURNING item_id;
"""

# Считаем кол-во позиций после вставки
COUNT_TASK_ITEMS = """
SELECT COUNT(*) AS items_count
FROM wms.task_items
WHERE task_id = $1;
"""

# ============================================================
# UPDATE
# ============================================================

UPDATE_TASK = """
UPDATE wms.tasks
SET
    priority   = COALESCE($2, priority),
    due_date   = COALESCE($3, due_date),
    notes      = COALESCE($4, notes),
    updated_at = NOW()
WHERE task_id = $1
  AND status IN ('pending', 'assigned')
RETURNING task_id, status, priority, due_date, notes;
"""

CANCEL_TASK = """
UPDATE wms.tasks
SET status = 'cancelled', updated_at = NOW()
WHERE task_id = $1
  AND status IN ('pending', 'assigned')
RETURNING task_id, status;
"""

# ============================================================
# ТСД-операции
# ============================================================

ASSIGN_TASK = """
UPDATE wms.tasks
SET
    status      = 'assigned',
    assigned_to = $2,
    assigned_at = NOW(),
    updated_at  = NOW()
WHERE task_id = $1
  AND status = 'pending'
  AND assigned_to IS NULL
RETURNING task_id, status, assigned_to, assigned_at;
"""

START_TASK = """
UPDATE wms.tasks
SET
    status     = 'in_progress',
    started_at = NOW(),
    updated_at = NOW()
WHERE task_id = $1
  AND assigned_to = $2
  AND status = 'assigned'
RETURNING task_id, status, started_at;
"""

UPDATE_TASK_ITEM_ACTUAL = """
UPDATE wms.task_items
SET
    quantity_actual  = $2,
    from_location_id = (SELECT location_id FROM wms.locations WHERE location_code = $4),
    discrepancy_reason = $5
WHERE item_id = $1
  AND task_id = $3
RETURNING item_id;
"""

CHECK_DISCREPANCIES = """
SELECT
    ti.item_id,
    ti.product_id,
    ti.quantity_planned,
    ti.quantity_actual,
    (ti.quantity_actual <> ti.quantity_planned)   AS has_discrepancy,
    (ti.quantity_actual - ti.quantity_planned)     AS discrepancy_qty,
    CASE WHEN ti.quantity_planned > 0
         THEN ABS((ti.quantity_actual - ti.quantity_planned)
                  / ti.quantity_planned * 100)
    END                                            AS discrepancy_percent,
    ti.discrepancy_reason                          AS reason,
    l.location_code                                AS from_location_code
FROM wms.task_items ti
LEFT JOIN wms.locations l ON ti.from_location_id = l.location_id
WHERE ti.task_id = $1;
"""

COMPLETE_TASK = """
UPDATE wms.tasks
SET
    status = CASE
        WHEN EXISTS (
            SELECT 1 FROM wms.task_items
            WHERE task_id = $1 AND quantity_actual <> quantity_planned
        ) THEN 'completed_with_discrepancy'
        ELSE 'completed'
    END,
    completed_at = NOW(),
    updated_at   = NOW()
WHERE task_id = $1
RETURNING task_id, status, completed_at;
"""

SET_TASK_STATUS = """
UPDATE wms.tasks
SET status = $2, updated_at = NOW()
WHERE task_id = $1
RETURNING task_id, status;
"""

# ============================================================
# Подсказки (suggestions) — ТСД
# ============================================================

GET_SUGGESTIONS = """
SELECT
    ti.product_id,
    ti.quantity_planned          AS quantity_needed,
    i.location_id,
    l.location_code,
    i.quantity,
    i.batch_number,
    i.created_at,
    ROW_NUMBER() OVER (
        PARTITION BY ti.product_id
        ORDER BY i.created_at ASC
    )                            AS fifo_priority
FROM wms.task_items ti
JOIN wms.tasks      t  ON ti.task_id   = t.task_id
JOIN wms.inventory  i  ON ti.product_id = i.product_id
JOIN wms.locations  l  ON i.location_id = l.location_id
WHERE ti.task_id = $1
  AND l.path <@ (
        SELECT path FROM wms.locations
        WHERE location_id = t.from_location_id
      )
  AND i.quantity > 0
ORDER BY ti.product_id, i.created_at ASC;
"""

# Суммарное количество по товару в зоне (для warnings при создании)
GET_PRODUCT_QTY_IN_ZONE = """
SELECT COALESCE(SUM(i.quantity), 0) AS available
FROM wms.inventory i
JOIN wms.locations l ON i.location_id = l.location_id
WHERE i.product_id = $1
  AND l.path <@ (SELECT path FROM wms.locations WHERE location_code = $2);
"""

# Проверка доступности товара при старте заявки
CHECK_AVAILABILITY_ON_START = """
WITH task_avail AS (
    SELECT
        ti.product_id,
        ti.quantity_planned,
        COALESCE(SUM(i.quantity), 0) AS quantity_available
    FROM wms.task_items ti
    JOIN wms.tasks t ON ti.task_id = t.task_id
    LEFT JOIN wms.inventory i ON ti.product_id = i.product_id
    LEFT JOIN wms.locations l ON i.location_id = l.location_id
    WHERE ti.task_id = $1
      AND (l.path IS NULL OR
           l.path <@ (SELECT path FROM wms.locations
                      WHERE location_id = t.from_location_id))
    GROUP BY ti.product_id, ti.quantity_planned
)
SELECT
    product_id,
    quantity_planned,
    quantity_available
FROM task_avail
WHERE quantity_available < quantity_planned;
"""

# ============================================================
# Дочерние заявки (discrepancy_approval / recount)
# ============================================================

CREATE_CHILD_TASK = """
INSERT INTO wms.tasks (
    task_type, status, priority, parent_task_id,
    from_location_id, to_location_id,
    assigned_to, created_by, metadata
)
SELECT
    $2,           -- task_type
    'pending',
    priority,
    task_id,      -- parent_task_id
    from_location_id,
    to_location_id,
    $3,           -- assigned_to (nullable)
    $5,            -- system user
    $4::jsonb     -- metadata
FROM wms.tasks
WHERE task_id = $1
RETURNING task_id, task_type, status, parent_task_id;
"""

# ============================================================
# Подтверждение расхождения (admin)
# ============================================================

GET_TASK_ITEM_DETAIL = """
SELECT
    ti.item_id,
    ti.product_id,
    ti.quantity_planned,
    ti.quantity_actual,
    ti.from_location_id,
    l.location_code AS from_location_code,
    ti.batch_number
FROM wms.task_items ti
LEFT JOIN wms.locations l ON ti.from_location_id = l.location_id
WHERE ti.item_id = $1;
"""

UPDATE_TASK_ITEM_APPROVED_QTY = """
UPDATE wms.task_items
SET quantity_actual = $2
WHERE item_id = $1
RETURNING item_id, product_id, quantity_actual, from_location_id, batch_number;
"""

GET_INVENTORY_QTY_IN_LOCATION = """
SELECT COALESCE(SUM(quantity), 0) AS available
FROM wms.inventory
WHERE product_id = $1
  AND location_id = $2;
"""

GET_TASK_ITEMS_FOR_MOVEMENTS = """
SELECT
    ti.item_id,
    ti.product_id,
    ti.quantity_actual,
    ti.from_location_id,
    l.location_code AS from_location_code,
    ti.batch_number
FROM wms.task_items ti
LEFT JOIN wms.locations l ON ti.from_location_id = l.location_id
WHERE ti.task_id = $1
  AND ti.quantity_actual IS NOT NULL;
"""

GET_APPROVERS = """
SELECT user_id
FROM public.user_permissions
WHERE approve_discrepancies = TRUE;
"""

CHECK_USER_CAN_APPROVE = """
SELECT COALESCE(approve_discrepancies, FALSE) AS can_approve
FROM public.user_permissions
WHERE user_id = $1;
"""

# ============================================================
# Пересчёт (recount)
# ============================================================

SAVE_RECOUNT_RESULTS = """
UPDATE wms.tasks
SET
    metadata     = jsonb_set(
                       COALESCE(metadata, '{}'::jsonb),
                       '{recount_results}',
                       $2::jsonb
                   ),
    status       = 'completed',
    completed_at = NOW(),
    updated_at   = NOW()
WHERE task_id = $1
  AND task_type = 'recount'
  AND assigned_to = $3
RETURNING task_id, status, parent_task_id, completed_at;
"""

GET_INVENTORY_QTY_BY_LOCATION_CODE = """
SELECT COALESCE(SUM(i.quantity), 0) AS current_qty
FROM wms.inventory i
JOIN wms.locations l ON i.location_id = l.location_id
WHERE i.product_id = $1
  AND l.location_code = $2;
"""

VALIDATE_ITEM_IDS_BELONG_TO_TASK = """
SELECT item_id
FROM wms.task_items
WHERE task_id = $1
  AND item_id = ANY($2::bigint[]);
"""