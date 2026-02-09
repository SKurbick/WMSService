"""SQL запросы для системных операций"""

# === Проверка целостности данных ===

VALIDATE_INTEGRITY = """
WITH calculated_inventory AS (
    SELECT
        product_id,
        COALESCE(to_location_id, from_location_id) as location_id,
        batch_number,
        container_code,
        SUM(
            CASE
                WHEN to_location_id IS NOT NULL THEN quantity
                WHEN from_location_id IS NOT NULL THEN -quantity
                ELSE 0
            END
        ) as calculated_quantity
    FROM wms.movements
    GROUP BY product_id, COALESCE(to_location_id, from_location_id), batch_number, container_code
    HAVING SUM(
        CASE
            WHEN to_location_id IS NOT NULL THEN quantity
            WHEN from_location_id IS NOT NULL THEN -quantity
            ELSE 0
        END
    ) > 0
)
SELECT
    ci.product_id,
    l.location_code,
    ci.batch_number,
    ci.container_code,
    ci.calculated_quantity as from_movements,
    COALESCE(i.quantity, 0) as from_inventory,
    ci.calculated_quantity - COALESCE(i.quantity, 0) as difference
FROM calculated_inventory ci
LEFT JOIN wms.inventory i
    ON ci.product_id = i.product_id
    AND ci.location_id = i.location_id
    AND COALESCE(ci.batch_number, '') = COALESCE(i.batch_number, '')
    AND COALESCE(ci.container_code, '') = COALESCE(i.container_code, '')
LEFT JOIN wms.locations l ON ci.location_id = l.location_id
WHERE ci.calculated_quantity != COALESCE(i.quantity, 0);
"""

# === Пересчёт остатков ===

# Шаг 1: Очистка inventory
DELETE_INVENTORY = """
DELETE FROM wms.inventory
WHERE ($1::varchar IS NULL OR product_id = $1);
"""

# Шаг 2: Пересчёт из movements
RECALCULATE_INVENTORY = """
INSERT INTO wms.inventory (product_id, location_id, quantity, status, batch_number, container_code)
SELECT
    m.product_id,
    COALESCE(m.to_location_id, m.from_location_id) as location_id,
    SUM(
        CASE
            WHEN m.to_location_id IS NOT NULL THEN m.quantity
            WHEN m.from_location_id IS NOT NULL THEN -m.quantity
            ELSE 0
        END
    ) as quantity,
    'available' as status,
    m.batch_number,
    m.container_code
FROM wms.movements m
WHERE ($1::varchar IS NULL OR m.product_id = $1)
  AND ($2::date IS NULL OR m.created_at >= $2)
GROUP BY m.product_id, COALESCE(m.to_location_id, m.from_location_id), m.batch_number, m.container_code
HAVING SUM(
    CASE
        WHEN m.to_location_id IS NOT NULL THEN m.quantity
        WHEN m.from_location_id IS NOT NULL THEN -m.quantity
        ELSE 0
    END
) > 0
ON CONFLICT (product_id, location_id, status, batch_number, container_code)
DO UPDATE SET
    quantity = EXCLUDED.quantity,
    updated_at = NOW();
"""

# Шаг 3: Статистика после пересчёта
GET_INVENTORY_STATS = """
SELECT
    COUNT(*) as inventory_records,
    COALESCE(SUM(quantity), 0) as total_units,
    COUNT(DISTINCT product_id) as products_count
FROM wms.inventory
WHERE ($1::varchar IS NULL OR product_id = $1);
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
