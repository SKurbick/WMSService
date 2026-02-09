"""SQL запросы для отчётов"""

# === Отчёт по зонам ===

GET_ZONES_REPORT = """
SELECT
    l.zone_type,
    COUNT(DISTINCT i.location_id) as occupied_locations,
    COUNT(DISTINCT i.product_id) as products_count,
    COALESCE(SUM(i.quantity), 0) as total_units,
    COUNT(DISTINCT i.container_code) FILTER (WHERE i.container_code IS NOT NULL) as containers_count
FROM wms.locations l
LEFT JOIN wms.inventory i ON l.location_id = i.location_id AND i.quantity > 0
WHERE l.zone_type IS NOT NULL
GROUP BY l.zone_type
ORDER BY total_units DESC NULLS LAST;
"""

# === Топ товаров по движениям ===

GET_TOP_PRODUCTS = """
SELECT
    m.product_id,
    p.name as product_name,
    p.category,
    COUNT(*) as movements_count,
    SUM(m.quantity) as total_moved,
    COUNT(DISTINCT m.movement_type) as movement_types_count
FROM wms.movements m
JOIN public.products p ON m.product_id = p.id
WHERE ($1::date IS NULL OR m.created_at >= $1)
  AND ($2::date IS NULL OR m.created_at <= $2 + interval '1 day')
GROUP BY m.product_id, p.name, p.category
ORDER BY movements_count DESC
LIMIT $3;
"""

# === ABC-анализ ===

GET_ABC_ANALYSIS = """
WITH product_movements AS (
    SELECT
        m.product_id,
        p.name as product_name,
        COUNT(*) as movements_count,
        SUM(m.quantity) as total_quantity
    FROM wms.movements m
    JOIN public.products p ON m.product_id = p.id
    WHERE m.created_at >= $1 AND m.created_at <= $2 + interval '1 day'
    GROUP BY m.product_id, p.name
),
ranked_products AS (
    SELECT
        *,
        SUM(movements_count) OVER () as total_movements,
        SUM(movements_count) OVER (ORDER BY movements_count DESC) as cumulative_movements,
        (SUM(movements_count) OVER (ORDER BY movements_count DESC))::decimal /
        NULLIF((SUM(movements_count) OVER ())::decimal, 0) * 100 as cumulative_percentage
    FROM product_movements
)
SELECT
    product_id,
    product_name,
    movements_count,
    total_quantity,
    COALESCE(cumulative_percentage, 0) as cumulative_percentage,
    CASE
        WHEN cumulative_percentage <= 80 THEN 'A'
        WHEN cumulative_percentage <= 95 THEN 'B'
        ELSE 'C'
    END as abc_class
FROM ranked_products
ORDER BY movements_count DESC;
"""

# === Оборачиваемость товаров ===

GET_TURNOVER_REPORT = """
WITH shipped AS (
    SELECT
        product_id,
        SUM(quantity) as shipped_quantity
    FROM wms.movements
    WHERE movement_type = 'ship'
      AND created_at >= $1
      AND created_at <= $2 + interval '1 day'
    GROUP BY product_id
),
avg_inventory AS (
    SELECT
        product_id,
        AVG(total_quantity) as avg_quantity
    FROM wms.v_product_stock
    GROUP BY product_id
)
SELECT
    s.product_id,
    p.name as product_name,
    s.shipped_quantity,
    COALESCE(ai.avg_quantity, 0) as avg_inventory,
    CASE
        WHEN ai.avg_quantity > 0 THEN
            ROUND((s.shipped_quantity / ai.avg_quantity)::numeric, 2)
        ELSE NULL
    END as turnover_ratio,
    CASE
        WHEN ai.avg_quantity > 0 AND s.shipped_quantity > 0 THEN
            ROUND((($2::date - $1::date) / (s.shipped_quantity / ai.avg_quantity))::numeric, 1)
        ELSE NULL
    END as days_of_inventory
FROM shipped s
JOIN public.products p ON s.product_id = p.id
LEFT JOIN avg_inventory ai ON s.product_id = ai.product_id
ORDER BY turnover_ratio DESC NULLS LAST;
"""

# === Отчёт по партиям (FIFO/FEFO) ===

GET_BATCHES_REPORT = """
SELECT
    i.product_id,
    p.name as product_name,
    i.batch_number,
    l.location_code,
    l.zone_type,
    SUM(i.quantity) as total_quantity,
    MIN(m.created_at) as first_received_at,
    COUNT(DISTINCT i.location_id) as locations_count
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
JOIN wms.locations l ON i.location_id = l.location_id
LEFT JOIN wms.movements m ON i.product_id = m.product_id
    AND i.batch_number = m.batch_number
    AND m.movement_type = 'receive'
WHERE ($1::varchar IS NULL OR i.product_id = $1)
  AND i.quantity > 0
  AND i.batch_number IS NOT NULL
GROUP BY i.product_id, p.name, i.batch_number, l.location_code, l.zone_type
ORDER BY first_received_at ASC, i.product_id;
"""
