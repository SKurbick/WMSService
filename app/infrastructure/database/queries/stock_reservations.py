"""SQL запросы для мягких резервов товара"""

PRODUCT_EXISTS = """
SELECT EXISTS (
    SELECT 1
    FROM public.products
    WHERE id = $1
);
"""

UPSERT_RESERVATION_ORDER = """
INSERT INTO wms.stock_reservation_orders (
    source_type,
    product_id,
    external_order_id,
    external_status,
    is_reserved,
    reserved_qty,
    external_created_at,
    last_event_at,
    raw_payload,
    updated_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8::jsonb, now())
ON CONFLICT (source_type, product_id, external_order_id)
DO UPDATE SET
    external_status = EXCLUDED.external_status,
    is_reserved = EXCLUDED.is_reserved,
    reserved_qty = EXCLUDED.reserved_qty,
    external_created_at = EXCLUDED.external_created_at,
    last_event_at = now(),
    raw_payload = EXCLUDED.raw_payload,
    updated_at = now()
RETURNING *;
"""

INSERT_RESERVATION_EVENT = """
INSERT INTO wms.stock_reservation_events (
    source_type,
    product_id,
    external_order_id,
    external_status,
    reserved_qty,
    external_created_at,
    processing_result,
    error_message,
    raw_payload
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
RETURNING *;
"""

GET_PRODUCT_AVAILABILITY = """
SELECT
    requested.product_id,
    COALESCE(v.physical_qty, 0)::numeric(20,3) AS physical_qty,
    COALESCE(v.reserved_qty, 0)::numeric(20,3) AS reserved_qty,
    COALESCE(v.free_qty, 0)::numeric(20,3) AS free_qty,
    COALESCE(v.shortage_qty, 0)::numeric(20,3) AS shortage_qty
FROM (SELECT $1::text AS product_id) requested
LEFT JOIN wms.v_product_availability v
    ON v.product_id = requested.product_id;
"""

LIST_PRODUCT_AVAILABILITY = """
SELECT
    product_id,
    COALESCE(physical_qty, 0)::numeric(20,3) AS physical_qty,
    COALESCE(reserved_qty, 0)::numeric(20,3) AS reserved_qty,
    COALESCE(free_qty, 0)::numeric(20,3) AS free_qty,
    COALESCE(shortage_qty, 0)::numeric(20,3) AS shortage_qty
FROM wms.v_product_availability
WHERE ($1::text IS NULL OR product_id = $1)
  AND ($2::boolean IS NOT TRUE OR shortage_qty > 0)
  AND ($3::boolean IS NOT TRUE OR reserved_qty > 0)
ORDER BY product_id
LIMIT $4 OFFSET $5;
"""

GET_AVAILABILITY_TOTALS = """
SELECT
    COALESCE(SUM(physical_qty), 0)::numeric(20,3) AS physical_qty,
    COALESCE(SUM(reserved_qty), 0)::numeric(20,3) AS reserved_qty,
    COALESCE(SUM(free_qty), 0)::numeric(20,3) AS free_qty,
    COALESCE(SUM(shortage_qty), 0)::numeric(20,3) AS shortage_qty,
    COUNT(*)::bigint AS products_total,
    COUNT(*) FILTER (WHERE shortage_qty > 0)::bigint AS products_with_shortage,
    COUNT(*) FILTER (WHERE reserved_qty > 0)::bigint AS products_with_active_reserve
FROM wms.v_product_availability;
"""

GET_LOCATION_SUBTREE_AVAILABILITY = """
WITH parent_location AS (
    SELECT path
    FROM wms.locations
    WHERE location_id = $1
), subtree_physical AS (
    SELECT
        i.product_id,
        COALESCE(SUM(i.quantity), 0)::numeric(20,3) AS physical_qty
    FROM wms.inventory i
    JOIN wms.locations l ON i.location_id = l.location_id
    JOIN parent_location parent ON l.path <@ parent.path
    WHERE i.status = 'available'
      AND i.quantity > 0
    GROUP BY i.product_id
), global_reserved AS (
    SELECT
        r.product_id,
        COALESCE(SUM(r.reserved_qty), 0)::numeric(20,3) AS reserved_qty
    FROM wms.stock_reservation_orders r
    CROSS JOIN parent_location parent
    WHERE r.is_reserved = true
    GROUP BY r.product_id
), product_ids AS (
    SELECT product_id FROM subtree_physical
    UNION
    SELECT product_id FROM global_reserved
)
SELECT
    p.product_id,
    COALESCE(sp.physical_qty, 0)::numeric(20,3) AS physical_qty,
    COALESCE(gr.reserved_qty, 0)::numeric(20,3) AS reserved_qty,
    (COALESCE(sp.physical_qty, 0) - COALESCE(gr.reserved_qty, 0))::numeric(20,3) AS free_qty,
    GREATEST(COALESCE(gr.reserved_qty, 0) - COALESCE(sp.physical_qty, 0), 0)::numeric(20,3) AS shortage_qty
FROM product_ids p
LEFT JOIN subtree_physical sp ON sp.product_id = p.product_id
LEFT JOIN global_reserved gr ON gr.product_id = p.product_id
ORDER BY p.product_id;
"""

LIST_RESERVATIONS = """
SELECT
    reservation_order_id,
    source_type,
    product_id,
    external_order_id,
    external_status,
    is_reserved,
    reserved_qty,
    external_created_at,
    last_event_at,
    raw_payload,
    created_at,
    updated_at
FROM wms.stock_reservation_orders
WHERE ($1::text IS NULL OR product_id = $1)
  AND ($2::bigint IS NULL OR external_order_id = $2)
  AND ($3::boolean IS NULL OR is_reserved = $3)
  AND ($4::text IS NULL OR external_status = $4)
  AND ($5::text IS NULL OR source_type = $5)
  AND ($6::int IS NULL OR last_event_at < now() - ($6::int * interval '1 hour'))
ORDER BY last_event_at DESC, reservation_order_id DESC
LIMIT $7 OFFSET $8;
"""

LIST_RESERVATION_EVENTS = """
SELECT
    reservation_event_id,
    source_type,
    product_id,
    external_order_id,
    external_status,
    reserved_qty,
    external_created_at,
    event_received_at,
    processing_result,
    error_message,
    raw_payload
FROM wms.stock_reservation_events
WHERE ($1::text IS NULL OR product_id = $1)
  AND ($2::bigint IS NULL OR external_order_id = $2)
  AND ($3::text IS NULL OR external_status = $3)
  AND ($4::text IS NULL OR processing_result = $4)
  AND ($5::text IS NULL OR source_type = $5)
  AND ($6::timestamptz IS NULL OR event_received_at >= $6)
  AND ($7::timestamptz IS NULL OR event_received_at <= $7)
ORDER BY event_received_at DESC, reservation_event_id DESC
LIMIT $8 OFFSET $9;
"""
