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

# Шаг 2: Очистка только available inventory
DELETE_AVAILABLE_INVENTORY = """
DELETE FROM wms.inventory
WHERE status = 'available'
  AND ($1::varchar IS NULL OR product_id = $1);
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
    (
        SELECT COUNT(*)
        FROM wms.movements
        WHERE quantity IS NULL OR quantity <= 0
    ) AS bad_movement_quantity_count,
    (
        SELECT COUNT(*)
        FROM wms.movements
        WHERE from_location_id IS NULL
          AND to_location_id IS NULL
    ) AS movement_without_sides_count,
    (
        SELECT COUNT(*)
        FROM wms.movements m
        LEFT JOIN wms.containers c ON c.qr_code = m.container_code
        WHERE m.container_code IS NOT NULL
          AND c.container_id IS NULL
    ) AS orphan_movement_container_code_count,
    (
        SELECT COUNT(*)
        FROM wms.inventory i
        LEFT JOIN wms.containers c ON c.qr_code = i.container_code
        WHERE i.container_code IS NOT NULL
          AND c.container_id IS NULL
    ) AS orphan_inventory_container_code_count,
    (
        SELECT COUNT(*)
        FROM wms.fbs_shipment_items f
        LEFT JOIN wms.movements m ON m.movement_id = f.movement_id
        WHERE f.movement_id IS NOT NULL
          AND m.movement_id IS NULL
    ) AS orphan_fbs_movement_count,
    (
        SELECT COUNT(*)
        FROM wms.inventory
        WHERE quantity < 0
    ) AS negative_inventory_quantity_count,
    (
        SELECT COUNT(*)
        FROM wms.locations l
        LEFT JOIN wms.locations p ON p.location_id = l.parent_location_id
        WHERE l.parent_location_id IS NOT NULL
          AND p.location_id IS NULL
    ) AS orphan_location_parent_count;
"""
