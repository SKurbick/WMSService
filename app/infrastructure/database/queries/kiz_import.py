"""SQL для raw inbox и controlled B2 apply импорта КИЗ."""

INSERT_MESSAGE = """
INSERT INTO wms.kiz_import_messages (
    raw_body, raw_payload, parse_status, parse_error, order_guid, supply_number,
    wild_group_count, mark_code_count, exchange_name, routing_key,
    rabbit_message_id, correlation_id, headers
)
VALUES (
    $1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb
)
RETURNING *;
"""

LIST_MESSAGES = """
SELECT
    message_id, received_at, parse_status, order_guid, supply_number,
    exchange_name, routing_key, wild_group_count, mark_code_count,
    business_status, processed_at, business_error_code, business_error_message,
    business_result
FROM wms.kiz_import_messages
ORDER BY message_id DESC
LIMIT $1 OFFSET $2;
"""

GET_MESSAGE = """
SELECT
    message_id, received_at, raw_body, raw_payload, parse_status, parse_error,
    order_guid, supply_number, wild_group_count, mark_code_count, exchange_name,
    routing_key, rabbit_message_id, correlation_id, headers, created_at,
    business_status, processed_at, business_error_code, business_error_message,
    business_result
FROM wms.kiz_import_messages
WHERE message_id = $1;
"""

LOCK_MESSAGE = GET_MESSAGE.rstrip().rstrip(";") + " FOR UPDATE;"

MARK_MESSAGE_APPLIED = """
UPDATE wms.kiz_import_messages
SET business_status = 'applied',
    processed_at = now(),
    business_error_code = NULL,
    business_error_message = NULL,
    business_result = $2::jsonb
WHERE message_id = $1
RETURNING processed_at;
"""

MARK_MESSAGE_REJECTED = """
UPDATE wms.kiz_import_messages
SET business_status = 'rejected',
    processed_at = now(),
    business_error_code = $2,
    business_error_message = $3,
    business_result = NULL
WHERE message_id = $1;
"""

LOCK_RECEIPT_LOCATION = """
SELECT location_id, location_code
FROM wms.locations
WHERE location_code = $1
  AND is_active IS TRUE
FOR SHARE;
"""

LOCK_PRODUCTS = """
SELECT id
FROM public.products
WHERE id = ANY($1::varchar[])
ORDER BY id
FOR SHARE;
"""

LOCK_RECEIPT_ITEMS = """
SELECT receipt_item_id, product_id, quantity
FROM wms.receipt_items
WHERE guid = $1
ORDER BY product_id, receipt_item_id
FOR UPDATE;
"""

LOCK_LOOSE_INVENTORY = """
SELECT inventory_id, product_id, quantity
FROM wms.inventory
WHERE location_id = $1
  AND product_id = ANY($2::varchar[])
  AND status = 'available'
  AND batch_number IS NULL
  AND container_code IS NULL
ORDER BY product_id, inventory_id
FOR UPDATE;
"""

LOCK_EXISTING_CODES = """
SELECT
    kiz_id, kiz_code, product_id, location_id, container_id,
    lifecycle_status, origin_type, origin_reference
FROM wms.kiz
WHERE kiz_code = ANY($1::text[])
ORDER BY kiz_code, kiz_id
FOR UPDATE;
"""

LOCK_ACTIVE_RECEIPT_KIZ = """
SELECT kiz_id, kiz_code, product_id
FROM wms.kiz
WHERE origin_type = 'receipt_import'
  AND origin_reference = $1
  AND product_id = ANY($2::varchar[])
  AND lifecycle_status = 'active'
ORDER BY product_id, kiz_id
FOR UPDATE;
"""

LOCK_ACTIVE_LOOSE_KIZ = """
SELECT kiz_id, kiz_code, product_id
FROM wms.kiz
WHERE location_id = $1
  AND container_id IS NULL
  AND product_id = ANY($2::varchar[])
  AND lifecycle_status = 'active'
ORDER BY product_id, kiz_id
FOR UPDATE;
"""

INSERT_KIZ = """
INSERT INTO wms.kiz (
    kiz_code, product_id, location_id, container_id, lifecycle_status,
    origin_type, origin_reference, created_by, metadata
)
VALUES ($1, $2, $3, NULL, 'active', 'receipt_import', $4, $5, $6::jsonb)
ON CONFLICT (kiz_code) DO NOTHING
RETURNING
    kiz_id, kiz_code, product_id, location_id, container_id,
    lifecycle_status, origin_type, origin_reference;
"""

LOCK_KIZ_BY_CODE = """
SELECT
    kiz_id, kiz_code, product_id, location_id, container_id,
    lifecycle_status, origin_type, origin_reference
FROM wms.kiz
WHERE kiz_code = $1
FOR UPDATE;
"""

INSERT_ASSIGNED_EVENT = """
INSERT INTO wms.kiz_events (
    kiz_id, event_type, from_status, to_status, product_id, location_id,
    container_id, author, reason, metadata, movement_ref
)
VALUES (
    $1, 'assigned', NULL, 'active', $2, $3,
    NULL, $4, NULL, $5::jsonb, NULL
)
RETURNING kiz_event_id;
"""

INSERT_MESSAGE_KIZ_LINK = """
INSERT INTO wms.kiz_import_message_kiz (message_id, kiz_id, was_created)
VALUES ($1, $2, $3)
ON CONFLICT (message_id, kiz_id) DO NOTHING;
"""

COUNT_ACTIVE_RECEIPT_KIZ = """
SELECT product_id, count(*)::bigint AS quantity
FROM wms.kiz
WHERE origin_type = 'receipt_import'
  AND origin_reference = $1
  AND product_id = ANY($2::varchar[])
  AND lifecycle_status = 'active'
GROUP BY product_id
ORDER BY product_id;
"""

COUNT_ACTIVE_LOOSE_KIZ = """
SELECT product_id, count(*)::bigint AS quantity
FROM wms.kiz
WHERE location_id = $1
  AND container_id IS NULL
  AND product_id = ANY($2::varchar[])
  AND lifecycle_status = 'active'
GROUP BY product_id
ORDER BY product_id;
"""

GET_RECEIPT_CAPACITY_VIOLATIONS = """
SELECT
    k.origin_reference AS order_guid,
    k.product_id,
    count(*)::bigint AS active_kiz_count,
    ri.quantity AS receipt_quantity
FROM wms.kiz k
JOIN wms.receipt_items ri
  ON ri.guid = k.origin_reference
 AND ri.product_id = k.product_id
WHERE k.origin_type = 'receipt_import'
  AND k.lifecycle_status = 'active'
GROUP BY k.origin_reference, k.product_id, ri.quantity
HAVING count(*) > ri.quantity
ORDER BY k.origin_reference, k.product_id;
"""

GET_ORPHAN_RECEIPT_KIZ = """
SELECT
    k.kiz_id, k.kiz_code, k.origin_reference AS order_guid, k.product_id,
    k.lifecycle_status
FROM wms.kiz k
LEFT JOIN wms.receipt_items ri
  ON ri.guid = k.origin_reference
 AND ri.product_id = k.product_id
WHERE k.origin_type = 'receipt_import'
  AND ri.receipt_item_id IS NULL
ORDER BY k.kiz_id;
"""
