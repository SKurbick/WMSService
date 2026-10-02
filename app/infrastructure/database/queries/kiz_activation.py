"""SQL for atomic registered-to-active KIZ activation."""

LOCK_LOCATION = """
SELECT location_id, location_code, is_active
FROM wms.locations
WHERE location_code = $1
FOR SHARE;
"""

CREATE_OPERATION = """
INSERT INTO wms.kiz_operations (
    operation_type, source_system, external_operation_id,
    request_fingerprint, author, result_payload
)
VALUES ('activate', 'api', $1, $2, $3, NULL)
ON CONFLICT (source_system, operation_type, external_operation_id) DO NOTHING
RETURNING operation_id, operation_type, source_system, external_operation_id,
          request_fingerprint, author, result_payload;
"""

LOCK_OPERATION_BY_KEY = """
SELECT operation_id, operation_type, source_system, external_operation_id,
       request_fingerprint, author, result_payload
FROM wms.kiz_operations
WHERE source_system = 'api'
  AND operation_type = 'activate'
  AND external_operation_id = $1
FOR UPDATE;
"""

LOCK_KIZ_BY_CODES = """
SELECT kiz_id, kiz_code, product_id, lifecycle_status,
       location_id, container_id, origin_type, origin_reference
FROM wms.kiz
WHERE kiz_code = ANY($1::text[])
ORDER BY kiz_code
FOR UPDATE;
"""

LOCK_PRODUCT_SCOPE = """
SELECT pg_advisory_xact_lock(
    hashtextextended('kiz-activate:' || $1::bigint::text || ':' || $2, 0)
);
"""

LOCK_LOOSE_INVENTORY = """
SELECT inventory_id, quantity
FROM wms.inventory
WHERE product_id = $1
  AND location_id = $2
  AND status = 'available'
  AND batch_number IS NULL
  AND container_code IS NULL
FOR UPDATE;
"""

COUNT_ACTIVE_LOOSE_KIZ = """
SELECT count(*)::bigint
FROM wms.kiz
WHERE product_id = $1
  AND location_id = $2
  AND container_id IS NULL
  AND lifecycle_status = 'active';
"""

CREATE_OPERATION_ITEM = """
INSERT INTO wms.kiz_operation_items (
    operation_id, external_line_id, movement_ref
)
VALUES ($1, $2, NULL)
RETURNING operation_item_id;
"""

AUTHORIZE_ACTIVATION = """
INSERT INTO wms.kiz_activation_authorizations (
    transaction_id, operation_id, kiz_id, product_id, location_id
)
VALUES (txid_current(), $1, $2, $3, $4);
"""

ACTIVATE_KIZ = """
UPDATE wms.kiz
SET lifecycle_status = 'active',
    location_id = $2,
    container_id = NULL,
    closed_at = NULL,
    updated_at = now()
WHERE kiz_id = $1
  AND lifecycle_status = 'registered'
  AND location_id IS NULL
  AND container_id IS NULL
RETURNING kiz_id, kiz_code, product_id, lifecycle_status, location_id, container_id;
"""

CREATE_ACTIVATED_EVENT = """
INSERT INTO wms.kiz_events (
    kiz_id, event_type, from_status, to_status, product_id,
    location_id, container_id, author, reason, metadata, movement_ref
)
VALUES (
    $1, 'activated', 'registered', 'active', $2,
    $3, NULL, $4, NULL, $5::jsonb, NULL
)
RETURNING kiz_event_id;
"""

COMPLETE_OPERATION = """
UPDATE wms.kiz_operations
SET result_payload = $2::jsonb
WHERE operation_id = $1 AND result_payload IS NULL
RETURNING operation_id;
"""
