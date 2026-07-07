"""SQL запросы для операций комплектации и разукомплектации."""

OPERATION_CODE_KIT_OPERATIONS = "kit_operations"
SCOPE_DIRECT = "direct"

GET_LOCATION_BY_CODE = """
SELECT
    location_id,
    location_code,
    name,
    is_active,
    level
FROM wms.locations
WHERE location_code = $1;
"""

GET_ACTIVE_KIT_OPERATION_LOCATION = """
SELECT
    operation_location_id,
    operation_code,
    location_id,
    location_code,
    scope,
    is_active,
    author,
    metadata,
    created_at,
    updated_at
FROM wms.operation_locations
WHERE operation_code = 'kit_operations'
  AND scope = 'direct'
  AND location_code = $1
  AND location_id = $2
  AND is_active = TRUE;
"""

LIST_KIT_OPERATION_LOCATIONS = """
SELECT
    ol.operation_location_id,
    ol.operation_code,
    ol.location_id,
    ol.location_code,
    l.name AS location_name,
    ol.scope,
    ol.is_active,
    ol.author,
    ol.metadata,
    ol.created_at,
    ol.updated_at
FROM wms.operation_locations ol
JOIN wms.locations l ON l.location_id = ol.location_id
WHERE ol.operation_code = 'kit_operations'
  AND ol.scope = 'direct'
  AND ($1::boolean IS NULL OR ol.is_active = $1)
ORDER BY ol.created_at DESC, ol.operation_location_id DESC
LIMIT $2 OFFSET $3;
"""

COUNT_KIT_OPERATION_LOCATIONS = """
SELECT COUNT(*)::int
FROM wms.operation_locations
WHERE operation_code = 'kit_operations'
  AND scope = 'direct'
  AND ($1::boolean IS NULL OR is_active = $1);
"""

CREATE_OR_REACTIVATE_KIT_OPERATION_LOCATION = """
INSERT INTO wms.operation_locations (
    operation_code,
    location_id,
    location_code,
    scope,
    is_active,
    author,
    metadata
)
VALUES ('kit_operations', $1, $2, 'direct', TRUE, $3, COALESCE($4::jsonb, '{}'::jsonb))
ON CONFLICT (operation_code, location_id, scope)
DO UPDATE SET
    location_code = EXCLUDED.location_code,
    is_active = TRUE,
    author = EXCLUDED.author,
    metadata = EXCLUDED.metadata,
    updated_at = NOW()
RETURNING
    operation_location_id,
    operation_code,
    location_id,
    location_code,
    scope,
    is_active,
    author,
    metadata,
    created_at,
    updated_at;
"""

GET_KIT_OPERATION_LOCATION = """
SELECT
    operation_location_id,
    operation_code,
    location_id,
    location_code,
    scope,
    is_active,
    author,
    metadata,
    created_at,
    updated_at
FROM wms.operation_locations
WHERE operation_location_id = $1
  AND operation_code = 'kit_operations'
  AND scope = 'direct';
"""

DEACTIVATE_KIT_OPERATION_LOCATION = """
UPDATE wms.operation_locations
SET
    is_active = FALSE,
    author = $2,
    updated_at = NOW()
WHERE operation_location_id = $1
  AND operation_code = 'kit_operations'
  AND scope = 'direct'
RETURNING
    operation_location_id,
    operation_code,
    location_id,
    location_code,
    scope,
    is_active,
    author,
    metadata,
    created_at,
    updated_at;
"""

GET_KIT_PRODUCT = """
SELECT
    id,
    is_active,
    is_kit,
    kit_components
FROM public.products
WHERE id = $1;
"""

GET_PRODUCTS_BY_IDS = """
SELECT
    id,
    is_active
FROM public.products
WHERE id = ANY($1::varchar[]);
"""

LOCK_KIT_OPERATION_SCOPE = """
SELECT pg_advisory_xact_lock(hashtextextended($1, 0));
"""

GET_LOOSE_INVENTORY_FOR_UPDATE = """
SELECT
    inventory_id,
    quantity
FROM wms.inventory
WHERE product_id = $1
  AND location_id = $2
  AND status = 'available'
  AND batch_number IS NULL
  AND container_code IS NULL
FOR UPDATE;
"""

GET_CONTAINER_INVENTORY_QUANTITY = """
SELECT COALESCE(SUM(quantity), 0)::numeric AS quantity
FROM wms.inventory
WHERE product_id = $1
  AND location_id = $2
  AND status = 'available'
  AND container_code IS NOT NULL;
"""

CREATE_KIT_OPERATION = """
INSERT INTO wms.kit_operations (
    operation_type,
    kit_product_id,
    quantity,
    operation_location_id,
    location_id,
    location_code,
    author,
    status
)
VALUES ($1, $2, $3, $4, $5, $6, $7, 'processing')
RETURNING
    operation_id,
    operation_location_id,
    operation_type,
    kit_product_id,
    quantity,
    location_id,
    location_code,
    author,
    status,
    created_at,
    completed_at;
"""

CREATE_KIT_OPERATION_ITEM = """
INSERT INTO wms.kit_operation_items (
    operation_id,
    role,
    product_id,
    quantity_per_kit,
    total_quantity
)
VALUES ($1, $2, $3, $4, $5)
RETURNING
    item_id,
    operation_id,
    role,
    product_id,
    quantity_per_kit,
    total_quantity,
    movement_id,
    movement_created_at;
"""

CREATE_KIT_MOVEMENT = """
INSERT INTO wms.movements (
    movement_type,
    product_id,
    from_location_id,
    to_location_id,
    quantity,
    batch_number,
    container_code,
    user_name,
    reason,
    metadata,
    source_type,
    source_id,
    source_item_id
)
VALUES ($1, $2, $3, $4, $5, NULL, NULL, $6, $7, $8::jsonb, 'kit_operation', $9, $10)
RETURNING movement_id, created_at;
"""

SET_ITEM_MOVEMENT = """
UPDATE wms.kit_operation_items
SET
    movement_id = $2,
    movement_created_at = $3
WHERE item_id = $1
RETURNING
    item_id,
    operation_id,
    role,
    product_id,
    quantity_per_kit,
    total_quantity,
    movement_id,
    movement_created_at;
"""

COMPLETE_KIT_OPERATION = """
UPDATE wms.kit_operations
SET
    status = 'completed',
    completed_at = NOW()
WHERE operation_id = $1
RETURNING
    operation_id,
    operation_location_id,
    operation_type,
    kit_product_id,
    quantity,
    location_id,
    location_code,
    author,
    status,
    created_at,
    completed_at;
"""

GET_KIT_OPERATION = """
SELECT
    ko.operation_id,
    ko.operation_location_id,
    ko.operation_type,
    ko.kit_product_id,
    ko.quantity,
    ko.location_code,
    ko.status,
    ko.author,
    ko.created_at,
    ko.completed_at
FROM wms.kit_operations ko
WHERE ko.operation_id = $1;
"""

GET_KIT_OPERATION_ITEMS = """
SELECT
    item_id,
    role,
    product_id,
    quantity_per_kit,
    total_quantity,
    movement_id
FROM wms.kit_operation_items
WHERE operation_id = $1
ORDER BY item_id;
"""

LIST_KIT_OPERATIONS = """
SELECT
    ko.operation_id,
    ko.operation_location_id,
    ko.operation_type,
    ko.kit_product_id,
    ko.quantity,
    ko.location_code,
    ko.status,
    ko.author,
    ko.created_at,
    ko.completed_at
FROM wms.kit_operations ko
WHERE ($1::varchar IS NULL OR ko.operation_type = $1)
  AND ($2::varchar IS NULL OR ko.kit_product_id = $2)
  AND ($3::varchar IS NULL OR ko.status = $3)
  AND ($4::varchar IS NULL OR ko.location_code = $4)
  AND ($5::timestamptz IS NULL OR ko.created_at >= $5)
  AND ($6::timestamptz IS NULL OR ko.created_at <= $6)
ORDER BY ko.created_at DESC
LIMIT $7 OFFSET $8;
"""
