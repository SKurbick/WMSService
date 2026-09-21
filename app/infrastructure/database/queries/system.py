"""SQL запросы для системных операций"""

# === Проверка целостности данных ===

VALIDATE_INTEGRITY = """
WITH movement_ledger AS (
    SELECT
        product_id,
        to_location_id as location_id,
        'available'::varchar as status,
        batch_number,
        container_code,
        quantity as signed_quantity
    FROM wms.movements
    WHERE to_location_id IS NOT NULL

    UNION ALL

    SELECT
        product_id,
        from_location_id as location_id,
        'available'::varchar as status,
        batch_number,
        container_code,
        -ABS(quantity) as signed_quantity
    FROM wms.movements
    WHERE from_location_id IS NOT NULL
),
calculated_inventory AS (
    SELECT
        product_id,
        location_id,
        status,
        batch_number,
        container_code,
        SUM(signed_quantity) as calculated_quantity
    FROM movement_ledger
    GROUP BY product_id, location_id, status, batch_number, container_code
    HAVING ABS(SUM(signed_quantity)) > 0.0001
),
current_inventory AS (
    SELECT
        product_id,
        location_id,
        status,
        batch_number,
        container_code,
        SUM(quantity) as inventory_quantity
    FROM wms.inventory
    WHERE status = 'available'
    GROUP BY product_id, location_id, status, batch_number, container_code
)
SELECT
    COALESCE(ci.product_id, i.product_id) as product_id,
    l.location_code,
    COALESCE(ci.status, i.status) as status,
    COALESCE(ci.batch_number, i.batch_number) as batch_number,
    COALESCE(ci.container_code, i.container_code) as container_code,
    COALESCE(ci.calculated_quantity, 0) as from_movements,
    COALESCE(i.inventory_quantity, 0) as from_inventory,
    COALESCE(ci.calculated_quantity, 0) - COALESCE(i.inventory_quantity, 0) as difference
FROM calculated_inventory ci
FULL OUTER JOIN current_inventory i
    ON ci.product_id = i.product_id
    AND ci.location_id = i.location_id
    AND ci.status = i.status
    AND ci.batch_number IS NOT DISTINCT FROM i.batch_number
    AND ci.container_code IS NOT DISTINCT FROM i.container_code
LEFT JOIN wms.locations l ON COALESCE(ci.location_id, i.location_id) = l.location_id
WHERE ABS(COALESCE(ci.calculated_quantity, 0) - COALESCE(i.inventory_quantity, 0)) > 0.0001
ORDER BY
    product_id,
    location_code NULLS LAST,
    batch_number NULLS FIRST,
    container_code NULLS FIRST,
    status;
"""

# === Пересчёт остатков ===

CALCULATED_AVAILABLE_INVENTORY_CTE = """
WITH movement_ledger AS (
    SELECT
        product_id,
        to_location_id as location_id,
        'available'::varchar as status,
        batch_number,
        container_code,
        quantity as signed_quantity
    FROM wms.movements
    WHERE to_location_id IS NOT NULL
      AND ($1::varchar IS NULL OR product_id = $1)

    UNION ALL

    SELECT
        product_id,
        from_location_id as location_id,
        'available'::varchar as status,
        batch_number,
        container_code,
        -ABS(quantity) as signed_quantity
    FROM wms.movements
    WHERE from_location_id IS NOT NULL
      AND ($1::varchar IS NULL OR product_id = $1)
),
calculated_inventory AS (
    SELECT
        product_id,
        location_id,
        status,
        batch_number,
        container_code,
        SUM(signed_quantity) as calculated_quantity
    FROM movement_ledger
    GROUP BY product_id, location_id, status, batch_number, container_code
)
"""

# Шаг 1: Диагностика отрицательных calculated available остатков
CHECK_NEGATIVE_CALCULATED_INVENTORY = CALCULATED_AVAILABLE_INVENTORY_CTE + """
SELECT
    product_id,
    location_id,
    batch_number,
    container_code,
    calculated_quantity
FROM calculated_inventory
WHERE calculated_quantity < -0.0001
ORDER BY product_id, location_id, batch_number NULLS FIRST, container_code NULLS FIRST
LIMIT 20;
"""

# Remove only scopes absent from the calculated positive projection, after UPSERT.
DELETE_AVAILABLE_INVENTORY = CALCULATED_AVAILABLE_INVENTORY_CTE + """
DELETE FROM wms.inventory i
WHERE i.status = 'available'
  AND ($1::varchar IS NULL OR i.product_id = $1)
  AND NOT EXISTS (
    SELECT 1 FROM calculated_inventory c
    WHERE c.product_id = i.product_id AND c.location_id = i.location_id
      AND c.status = i.status
      AND c.batch_number IS NOT DISTINCT FROM i.batch_number
      AND c.container_code IS NOT DISTINCT FROM i.container_code
      AND c.calculated_quantity > 0.0001
  );
"""

CHECK_CALCULATED_KIZ = CALCULATED_AVAILABLE_INVENTORY_CTE + """
, identified AS (
    SELECT product_id, location_id, count(*) AS identified_quantity FROM wms.kiz
    WHERE lifecycle_status = 'active' AND ($1::varchar IS NULL OR product_id = $1)
    GROUP BY product_id, location_id
)
SELECT k.product_id, k.location_id, l.location_code,
       COALESCE(c.calculated_quantity, 0) AS calculated_quantity,
       k.identified_quantity
FROM identified k JOIN wms.locations l USING (location_id)
LEFT JOIN calculated_inventory c
    ON c.product_id = k.product_id AND c.location_id = k.location_id
    AND c.batch_number IS NULL AND c.container_code IS NULL
    AND c.calculated_quantity > 0.0001
WHERE COALESCE(c.calculated_quantity, 0) < k.identified_quantity
ORDER BY k.product_id, k.location_id;
"""


CHECK_CONTAINER_PROJECTION = CALCULATED_AVAILABLE_INVENTORY_CTE + """
, ledger_by_location AS (
    SELECT product_id, location_id, batch_number, container_code,
           calculated_quantity AS quantity
    FROM calculated_inventory
    WHERE container_code IS NOT NULL
      AND ABS(calculated_quantity) > 0.0001
), ledger_scope AS (
    SELECT product_id, batch_number, container_code,
           SUM(quantity) AS quantity,
           MIN(location_id) AS location_id,
           COUNT(DISTINCT location_id) AS location_count
    FROM ledger_by_location
    GROUP BY product_id, batch_number, container_code
), content_scope AS (
    SELECT cc.product_id, cc.batch_number, c.qr_code AS container_code,
           SUM(cc.quantity) AS quantity,
           c.location_id,
           1::bigint AS location_count
    FROM wms.container_contents cc
    JOIN wms.containers c ON c.container_id = cc.container_id
    WHERE cc.status = 'active'
      AND ($1::varchar IS NULL OR cc.product_id = $1)
    GROUP BY cc.product_id, cc.batch_number, c.qr_code, c.location_id
), inventory_scope AS (
    SELECT i.product_id, i.batch_number, i.container_code,
           SUM(i.quantity) AS quantity,
           MIN(i.location_id) AS location_id,
           COUNT(DISTINCT i.location_id) AS location_count
    FROM wms.inventory i
    WHERE i.status = 'available'
      AND i.container_code IS NOT NULL
      AND ($1::varchar IS NULL OR i.product_id = $1)
    GROUP BY i.product_id, i.batch_number, i.container_code
), scope_keys AS (
    SELECT product_id, batch_number, container_code FROM ledger_scope
    UNION
    SELECT product_id, batch_number, container_code FROM content_scope
    UNION
    SELECT product_id, batch_number, container_code FROM inventory_scope
)
SELECT k.product_id,
       k.batch_number,
       k.container_code,
       l.quantity AS ledger_quantity,
       c.quantity AS contents_quantity,
       i.quantity AS inventory_quantity,
       l.location_id AS ledger_location_id,
       c.location_id AS container_location_id,
       i.location_id AS inventory_location_id,
       CASE
         WHEN l.product_id IS NULL THEN 'ledger_side_missing'
         WHEN c.product_id IS NULL THEN 'active_contents_missing'
         WHEN i.product_id IS NULL THEN 'contained_inventory_missing'
         WHEN l.location_count <> 1 THEN 'ledger_multiple_locations'
         WHEN i.location_count <> 1 THEN 'inventory_multiple_locations'
         WHEN l.location_id IS DISTINCT FROM c.location_id THEN 'ledger_location_mismatch'
         WHEN i.location_id IS DISTINCT FROM c.location_id THEN 'inventory_location_mismatch'
         WHEN l.quantity IS DISTINCT FROM c.quantity THEN 'ledger_contents_quantity_mismatch'
         WHEN i.quantity IS DISTINCT FROM c.quantity THEN 'inventory_contents_quantity_mismatch'
       END AS issue
FROM scope_keys k
LEFT JOIN ledger_scope l
  ON l.product_id = k.product_id
 AND l.batch_number IS NOT DISTINCT FROM k.batch_number
 AND l.container_code = k.container_code
LEFT JOIN content_scope c
  ON c.product_id = k.product_id
 AND c.batch_number IS NOT DISTINCT FROM k.batch_number
 AND c.container_code = k.container_code
LEFT JOIN inventory_scope i
  ON i.product_id = k.product_id
 AND i.batch_number IS NOT DISTINCT FROM k.batch_number
 AND i.container_code = k.container_code
WHERE l.product_id IS NULL OR c.product_id IS NULL OR i.product_id IS NULL
   OR l.location_count <> 1 OR i.location_count <> 1
   OR l.location_id IS DISTINCT FROM c.location_id
   OR i.location_id IS DISTINCT FROM c.location_id
   OR l.quantity IS DISTINCT FROM c.quantity
   OR i.quantity IS DISTINCT FROM c.quantity
ORDER BY k.container_code, k.product_id, k.batch_number NULLS FIRST
LIMIT 100;
"""

# Шаг 3: Пересчёт available остатков из movements
RECALCULATE_INVENTORY = CALCULATED_AVAILABLE_INVENTORY_CTE + """
INSERT INTO wms.inventory (product_id, location_id, quantity, status, batch_number, container_code)
SELECT
    product_id,
    location_id,
    calculated_quantity as quantity,
    status,
    batch_number,
    container_code
FROM calculated_inventory
WHERE calculated_quantity > 0.0001
ON CONFLICT (product_id, location_id, status, batch_number, container_code)
DO UPDATE SET
    quantity = EXCLUDED.quantity,
    updated_at = NOW();
"""

# Шаг 4: Статистика после пересчёта available остатков
GET_INVENTORY_STATS = """
SELECT
    COUNT(*) as inventory_records,
    COALESCE(SUM(quantity), 0) as total_units,
    COUNT(DISTINCT product_id) as products_count
FROM wms.inventory
WHERE status = 'available'
  AND ($1::varchar IS NULL OR product_id = $1);
"""

# === Создание снимка остатков ===

CREATE_SNAPSHOT = """
INSERT INTO wms.inventory_snapshots (
    snapshot_date,
    product_id,
    location_id,
    container_code,
    quantity,
    status
)
SELECT
    COALESCE($1::date, CURRENT_DATE),
    product_id,
    location_id,
    container_code,
    quantity,
    status
FROM wms.inventory
WHERE status = 'available';
"""

GET_SNAPSHOT_STATS = """
SELECT
    COALESCE($1::date, CURRENT_DATE) as snapshot_date,
    COUNT(*) as records_count,
    COALESCE(SUM(quantity), 0) as total_units,
    COUNT(DISTINCT product_id) as products_count
FROM wms.inventory_snapshots
WHERE snapshot_date = COALESCE($1::date, CURRENT_DATE);
"""

# === Обновление материализованных представлений ===

REFRESH_MATERIALIZED_VIEW = """
REFRESH MATERIALIZED VIEW CONCURRENTLY wms.mv_product_stock;
"""

GET_MATERIALIZED_VIEW_STATS = """
SELECT
    'mv_product_stock' as view_name,
    COUNT(*) as records_count,
    COALESCE(SUM(total_quantity), 0) as total_units,
    NOW() as refreshed_at
FROM wms.mv_product_stock;
"""

# === Агрегированный read-only аудит известных рисков ===

GET_AUDIT_SUMMARY = """
SELECT
    (SELECT COUNT(*) FROM wms.movements WHERE quantity IS NULL OR quantity <= 0)
        AS bad_movement_quantity_count,
    (SELECT COUNT(*) FROM wms.movements
      WHERE from_location_id IS NULL AND to_location_id IS NULL)
        AS movement_without_sides_count,
    (SELECT COUNT(*) FROM wms.movements m
      LEFT JOIN wms.containers c ON c.qr_code = m.container_code
      WHERE m.container_code IS NOT NULL AND c.container_id IS NULL)
        AS orphan_movement_container_code_count,
    (SELECT COUNT(*) FROM wms.inventory i
      LEFT JOIN wms.containers c ON c.qr_code = i.container_code
      WHERE i.container_code IS NOT NULL AND c.container_id IS NULL)
        AS orphan_inventory_container_code_count,
    (SELECT COUNT(*) FROM wms.fbs_shipment_items f
      LEFT JOIN wms.movements m ON m.movement_id = f.movement_id
      WHERE f.movement_id IS NOT NULL AND m.movement_id IS NULL)
        AS orphan_fbs_movement_count,
    (SELECT COUNT(*) FROM wms.inventory WHERE quantity < 0)
        AS negative_inventory_quantity_count,
    (SELECT COUNT(*) FROM wms.locations l
      LEFT JOIN wms.locations p ON p.location_id = l.parent_location_id
      WHERE l.parent_location_id IS NOT NULL AND p.location_id IS NULL)
        AS orphan_location_parent_count,
    (SELECT COUNT(*) FROM wms.container_contents cc
      JOIN wms.containers c ON c.container_id = cc.container_id
      WHERE cc.status = 'active' AND NOT EXISTS (
          SELECT 1 FROM wms.inventory i
          WHERE i.product_id = cc.product_id
            AND i.location_id = c.location_id
            AND i.status = 'available'
            AND i.batch_number IS NOT DISTINCT FROM cc.batch_number
            AND i.container_code = c.qr_code))
        AS active_contents_without_inventory_count,
    (SELECT COUNT(*) FROM wms.inventory i
      JOIN wms.containers c ON c.qr_code = i.container_code
      WHERE i.status = 'available' AND NOT EXISTS (
          SELECT 1 FROM wms.container_contents cc
          WHERE cc.container_id = c.container_id
            AND cc.product_id = i.product_id
            AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
            AND cc.status = 'active'))
        AS contained_inventory_without_contents_count,
    (SELECT COUNT(*) FROM wms.container_contents cc
      JOIN wms.containers c ON c.container_id = cc.container_id
      JOIN wms.inventory i
        ON i.product_id = cc.product_id
       AND i.location_id = c.location_id
       AND i.status = 'available'
       AND i.batch_number IS NOT DISTINCT FROM cc.batch_number
       AND i.container_code = c.qr_code
      WHERE cc.status = 'active' AND cc.quantity <> i.quantity)
        AS container_quantity_mismatch_count,
    (SELECT COUNT(*) FROM wms.inventory i
      JOIN wms.containers c ON c.qr_code = i.container_code
      WHERE i.location_id <> c.location_id)
        AS container_location_mismatch_count,
    (SELECT COUNT(*) FROM wms.inventory
      WHERE container_code IS NOT NULL AND status <> 'available')
        AS unsupported_container_inventory_status_count,
    (SELECT COUNT(*) FROM wms.containers c
      WHERE c.status = 'empty' AND EXISTS (
          SELECT 1 FROM wms.container_contents cc
          WHERE cc.container_id = c.container_id AND cc.status = 'active'))
        AS empty_container_with_contents_count,
    (SELECT COUNT(*) FROM wms.containers c
      WHERE c.status IN ('open', 'sealed') AND NOT EXISTS (
          SELECT 1 FROM wms.container_contents cc
          WHERE cc.container_id = c.container_id AND cc.status = 'active'))
        AS nonempty_container_without_contents_count,
    (SELECT COUNT(*) FROM wms.movements m
      WHERE (m.container_code IS NOT NULL
             AND m.source_type IS DISTINCT FROM 'container_operation')
         OR (m.source_type = 'container_operation'
             AND (m.source_id IS NULL OR m.source_item_id IS NULL)))
        AS invalid_container_movement_provenance_count;
"""
