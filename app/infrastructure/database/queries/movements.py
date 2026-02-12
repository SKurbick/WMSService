"""SQL запросы для работы с движениями товаров"""

# === CREATE ===

CREATE_MOVEMENT = """
INSERT INTO wms.movements (
    movement_type,
    product_id,
    from_location_id,
    to_location_id,
    quantity,
    batch_number,
    container_code,
    user_name,
    reason
)
VALUES (
    $1,
    $2,
    (SELECT location_id FROM wms.locations WHERE location_code = $3),
    (SELECT location_id FROM wms.locations WHERE location_code = $4),
    $5,
    $6,
    $7,
    $8,
    $9
)
RETURNING
    movement_id,
    movement_type,
    product_id,
    from_location_id,
    to_location_id,
    quantity,
    created_at;
"""

# === READ (с фильтрами) ===

GET_MOVEMENTS = """
SELECT
    m.movement_id,
    m.movement_type,
    m.product_id,
    p.name as product_name,
    l_from.location_code as from_location,
    l_to.location_code as to_location,
    m.quantity,
    m.batch_number,
    m.container_code,
    m.user_name,
    m.reason,
    m.created_at
FROM wms.movements m
LEFT JOIN public.products p ON m.product_id = p.id
LEFT JOIN wms.locations l_from ON m.from_location_id = l_from.location_id
LEFT JOIN wms.locations l_to ON m.to_location_id = l_to.location_id
WHERE ($1::varchar IS NULL OR m.product_id = $1)
  AND ($2::varchar IS NULL OR m.container_code = $2)
  AND ($3::varchar IS NULL OR m.movement_type = $3)
  AND ($4::date IS NULL OR m.created_at >= $4)
  AND ($5::date IS NULL OR m.created_at <= $5 + interval '1 day')
ORDER BY m.created_at DESC
LIMIT $6 OFFSET $7;
"""

# === История по товару ===

GET_MOVEMENTS_BY_PRODUCT = """
SELECT
    m.product_id,
    m.movement_id,
    m.movement_type,
    l_from.location_code as from_location,
    l_to.location_code as to_location,
    m.quantity,
    m.batch_number,
    m.container_code,
    m.user_name,
    m.reason,
    m.created_at
FROM wms.movements m
LEFT JOIN wms.locations l_from ON m.from_location_id = l_from.location_id
LEFT JOIN wms.locations l_to ON m.to_location_id = l_to.location_id
WHERE m.product_id = $1
ORDER BY m.created_at DESC
LIMIT $2;
"""
