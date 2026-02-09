"""SQL запросы для работы с контейнерами"""

# === REGISTER ===

REGISTER_CONTAINER = """
SELECT * FROM wms.register_container($1, $2, $3, $4);
"""

# === READ ===

GET_CONTAINER_BY_QR = """
SELECT
    c.container_id,
    c.qr_code,
    c.container_type,
    c.status,
    l.location_code,
    l.zone_type,
    c.parent_container_id,
    pc.qr_code as parent_qr_code,
    c.metadata,
    c.created_at,
    c.updated_at,
    json_agg(
        json_build_object(
            'product_id', cc.product_id,
            'product_name', p.name,
            'quantity', cc.quantity,
            'batch_number', cc.batch_number,
            'is_scanned', cc.is_scanned
        ) ORDER BY cc.product_id
    ) FILTER (WHERE cc.status = 'active') as contents
FROM wms.containers c
LEFT JOIN wms.locations l ON c.location_id = l.location_id
LEFT JOIN wms.containers pc ON c.parent_container_id = pc.container_id
LEFT JOIN wms.container_contents cc ON c.container_id = cc.container_id
LEFT JOIN public.products p ON cc.product_id = p.id
WHERE c.qr_code = $1
GROUP BY c.container_id, c.qr_code, c.container_type, c.status,
         l.location_code, l.zone_type, c.parent_container_id,
         pc.qr_code, c.metadata, c.created_at, c.updated_at;
"""

GET_CONTAINER_BY_ID = """
SELECT
    c.container_id,
    c.qr_code,
    c.container_type,
    c.status,
    l.location_code,
    l.zone_type,
    c.parent_container_id,
    pc.qr_code as parent_qr_code,
    c.metadata,
    c.created_at,
    c.updated_at
FROM wms.containers c
LEFT JOIN wms.locations l ON c.location_id = l.location_id
LEFT JOIN wms.containers pc ON c.parent_container_id = pc.container_id
WHERE c.container_id = $1;
"""

# === UPDATE LOCATION ===

UPDATE_CONTAINER_LOCATION = """
UPDATE wms.containers
SET location_id = (
    SELECT location_id
    FROM wms.locations
    WHERE location_code = $2
),
updated_at = NOW()
WHERE container_id = $1
RETURNING container_id, qr_code, location_id;
"""

# === UNPACK ===

UNPACK_FROM_CONTAINER = """
SELECT * FROM wms.unpack_from_container($1, $2, $3);
"""

# === UPDATE STATUS ===

UPDATE_CONTAINER_STATUS = """
UPDATE wms.containers
SET status = $2,
    updated_at = NOW()
WHERE container_id = $1
  AND status != 'blocked'
RETURNING container_id, qr_code, status, updated_at;
"""

# === HISTORY ===

GET_CONTAINER_HISTORY = """
SELECT
    m.movement_id,
    m.movement_type,
    m.product_id,
    p.name as product_name,
    l_from.location_code as from_location,
    l_to.location_code as to_location,
    m.quantity,
    m.batch_number,
    m.user_name,
    m.reason,
    m.created_at
FROM wms.movements m
LEFT JOIN public.products p ON m.product_id = p.id
LEFT JOIN wms.locations l_from ON m.from_location_id = l_from.location_id
LEFT JOIN wms.locations l_to ON m.to_location_id = l_to.location_id
WHERE m.container_code = $1
ORDER BY m.created_at DESC;
"""

# === CONTAINERS IN LOCATION ===

GET_CONTAINERS_IN_LOCATION = """
SELECT
    c.container_id,
    c.qr_code,
    c.container_type,
    c.status,
    COUNT(DISTINCT cc.product_id) as products_count,
    COALESCE(SUM(cc.quantity), 0) as total_units,
    c.created_at
FROM wms.containers c
LEFT JOIN wms.container_contents cc ON c.container_id = cc.container_id AND cc.status = 'active'
WHERE c.location_id = $1
  AND ($2::varchar IS NULL OR c.status = $2)
  AND ($3::varchar IS NULL OR c.container_type = $3)
GROUP BY c.container_id, c.qr_code, c.container_type, c.status, c.created_at
ORDER BY c.created_at DESC;
"""

# === CHECK EXISTS ===

CHECK_CONTAINER_EXISTS = """
SELECT container_id FROM wms.containers WHERE qr_code = $1;
"""
