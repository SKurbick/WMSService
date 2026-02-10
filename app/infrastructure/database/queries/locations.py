"""SQL запросы для работы с локациями"""

# === CREATE ===

CREATE_LOCATION = """
INSERT INTO wms.locations (
    parent_location_id,
    name,
    zone_type,
    level,
    max_weight,
    max_volume,
    is_active,
    is_pickable,
    metadata
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
RETURNING
    location_id,
    location_code,
    path::text,
    name,
    zone_type,
    level,
    max_weight,
    max_volume,
    is_active,
    is_pickable,
    metadata,
    parent_location_id,
    NULL::varchar as parent_location_code,
    NULL::varchar as parent_name,
    created_at,
    updated_at;
"""

# === READ ===

GET_LOCATION_BY_ID = """
SELECT
    l.location_id,
    l.location_code,
    l.path::text,
    l.name,
    l.zone_type,
    l.level,
    l.max_weight,
    l.max_volume,
    l.is_active,
    l.is_pickable,
    l.metadata,
    l.parent_location_id,
    p.location_code as parent_location_code,
    p.name as parent_name,
    l.created_at,
    l.updated_at
FROM wms.locations l
LEFT JOIN wms.locations p ON l.parent_location_id = p.location_id
WHERE l.location_id = $1;
"""

GET_LOCATION_BY_CODE = """
SELECT
    l.location_id,
    l.location_code,
    l.path::text,
    l.name,
    l.zone_type,
    l.level,
    l.max_weight,
    l.max_volume,
    l.is_active,
    l.is_pickable,
    l.metadata,
    l.parent_location_id,
    p.location_code as parent_location_code,
    p.name as parent_name,
    l.created_at,
    l.updated_at
FROM wms.locations l
LEFT JOIN wms.locations p ON l.parent_location_id = p.location_id
WHERE l.location_code = $1;
"""

GET_CHILDREN_RECURSIVE = """
SELECT
    l.location_id,
    l.location_code,
    l.name,
    l.zone_type,
    l.level,
    l.path::text,
    l.is_active,
    nlevel(l.path) - nlevel(parent.path) as depth
FROM wms.locations l
CROSS JOIN (
    SELECT path FROM wms.locations WHERE location_id = $1
) parent
WHERE l.path <@ parent.path
  AND l.location_id != $1
ORDER BY l.path;
"""

GET_CHILDREN_DIRECT = """
SELECT
    l.location_id,
    l.location_code,
    l.name,
    l.zone_type,
    l.level,
    l.path::text,
    l.is_active,
    1 as depth
FROM wms.locations l
WHERE l.parent_location_id = $1
ORDER BY l.location_code;
"""

GET_ZONES = """
SELECT 
    l.location_id,
    l.location_code,
    l.name,
    l.zone_type,
    l.level,
    l.path::text,
    l.is_active,
    l.is_pickable,
    l.max_weight,
    l.max_volume,
    l.metadata,
    p.location_code as warehouse_code,
    p.name as warehouse_name
FROM wms.locations l
JOIN wms.locations p ON l.parent_location_id = p.location_id
WHERE l.level = 1
  AND l.is_active = TRUE
ORDER BY l.location_id;
"""

# === UPDATE ===

UPDATE_LOCATION = """
UPDATE wms.locations
SET
    name = COALESCE($2, name),
    zone_type = COALESCE($3, zone_type),
    max_weight = COALESCE($4, max_weight),
    max_volume = COALESCE($5, max_volume),
    is_active = COALESCE($6, is_active),
    is_pickable = COALESCE($7, is_pickable),
    metadata = COALESCE($8, metadata),
    updated_at = NOW()
WHERE location_id = $1
RETURNING
    location_id,
    location_code,
    path::text,
    name,
    zone_type,
    level,
    max_weight,
    max_volume,
    is_active,
    is_pickable,
    metadata,
    parent_location_id,
    NULL::varchar as parent_location_code,
    NULL::varchar as parent_name,
    created_at,
    updated_at;
"""

DEACTIVATE_LOCATION = """
UPDATE wms.locations
SET is_active = FALSE,
    updated_at = NOW()
WHERE location_id = $1
RETURNING location_id, location_code, is_active;
"""

# === SPECIAL ===

FIND_AVAILABLE_LOCATION = """
SELECT * FROM wms.find_available_location($1, $2, $3);
"""
