"""SQL запросы для работы с инвентарём (остатками)"""

# === Остатки товара по локациям ===

GET_INVENTORY_BY_PRODUCT = """
SELECT
    i.inventory_id,
    i.product_id,
    p.name as product_name,
    l.location_code,
    l.zone_type,
    i.quantity,
    i.status,
    i.batch_number,
    i.container_code,
    i.updated_at
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
JOIN wms.locations l ON i.location_id = l.location_id
WHERE i.product_id = $1
  AND i.quantity > 0
ORDER BY l.zone_type, l.location_code, i.container_code NULLS LAST;
"""

# === Остатки в локации ===

GET_INVENTORY_BY_LOCATION = """
SELECT
    i.inventory_id,
    i.product_id,
    p.name as product_name,
    p.category,
    i.quantity,
    i.status,
    i.batch_number,
    i.container_code,
    i.updated_at
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
WHERE i.location_id = $1
  AND i.quantity > 0
ORDER BY p.name, i.container_code NULLS LAST;
"""

# === Агрегированные остатки (через view) ===

GET_INVENTORY_SUMMARY = """
SELECT
    v.product_id,
    v.product_name,
    v.category,
    v.total_quantity,
    v.locations_count,
    v.in_containers,
    v.loose,
    v.last_updated
FROM wms.v_product_stock v
WHERE ($1::varchar IS NULL OR v.category = $1)
ORDER BY v.product_name;
"""

# === Остатки в контейнере ===

GET_INVENTORY_IN_CONTAINER = """
SELECT
    i.product_id,
    p.name as product_name,
    i.quantity,
    i.batch_number,
    l.location_code,
    l.zone_type
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
JOIN wms.locations l ON i.location_id = l.location_id
WHERE i.container_code = $1
  AND i.quantity > 0
ORDER BY p.name;
"""

# === Россыпь в локации ===

GET_LOOSE_INVENTORY = """
SELECT
    i.product_id,
    p.name as product_name,
    i.quantity,
    i.batch_number,
    i.status
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
WHERE i.location_id = $1
  AND i.container_code IS NULL
  AND i.quantity > 0
ORDER BY p.name;
"""

# === Поиск товара ===

SEARCH_INVENTORY = """
SELECT
    i.product_id,
    p.name as product_name,
    l.location_code,
    l.zone_type,
    i.quantity,
    i.container_code,
    i.batch_number,
    i.status
FROM wms.inventory i
JOIN public.products p ON i.product_id = p.id
JOIN wms.locations l ON i.location_id = l.location_id
WHERE (
    i.product_id ILIKE '%' || $1 || '%'
    OR p.name ILIKE '%' || $1 || '%'
    OR i.batch_number ILIKE '%' || $1 || '%'
    OR i.container_code ILIKE '%' || $1 || '%'
)
AND i.quantity > 0
ORDER BY
    CASE
        WHEN i.product_id = $1 THEN 1
        WHEN p.name ILIKE $1 || '%' THEN 2
        ELSE 3
    END,
    p.name
LIMIT 50;
"""
