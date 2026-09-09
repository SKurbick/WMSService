"""Exact available/NULL batch/NULL container scope only."""
GET_PRODUCT = 'SELECT id FROM public.products WHERE id = $1'
GET_LOCATION = 'SELECT location_id, location_code FROM wms.locations WHERE location_code = $1'
GET_KIZ = '''
SELECT k.*, l.location_code FROM wms.kiz k
JOIN wms.locations l USING (location_id) WHERE k.kiz_code = $1
'''
LOCK_KIZ = GET_KIZ + ' FOR UPDATE OF k'
LOCK_INVENTORY = '''
SELECT inventory_id, quantity FROM wms.inventory
WHERE product_id = $1 AND location_id = $2 AND status = 'available'
  AND batch_number IS NULL AND container_code IS NULL FOR UPDATE
'''
TOUCH_INVENTORY = 'UPDATE wms.inventory SET updated_at = now() WHERE inventory_id = $1'
COUNT_ACTIVE = '''
SELECT count(*) FROM wms.kiz
WHERE product_id = $1 AND location_id = $2 AND lifecycle_status = 'active'
'''
INSERT_KIZ = '''
INSERT INTO wms.kiz (kiz_code, product_id, location_id, created_by, metadata,
                     lifecycle_status, origin_type)
VALUES ($1, $2, $3, $4, $5::jsonb, 'active', 'warehouse_assignment') RETURNING kiz_id
'''
INSERT_EVENT = '''
INSERT INTO wms.kiz_events
 (kiz_id, event_type, from_status, to_status, product_id, location_id, author, reason, metadata)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
'''
TERMINATE = '''
UPDATE wms.kiz SET lifecycle_status = $2, closed_at = now(), updated_at = now()
WHERE kiz_id = $1 AND lifecycle_status = 'active'
'''
SUMMARY = '''
SELECT $1::varchar AS product_id, l.location_id, l.location_code,
    COALESCE(i.quantity, 0) AS physical_quantity, k.identified_quantity,
    COALESCE(i.quantity, 0) - k.identified_quantity AS unidentified_quantity,
    COALESCE(i.quantity, 0) >= k.identified_quantity AS integrity_ok
FROM wms.locations l
LEFT JOIN wms.inventory i ON i.location_id = l.location_id AND i.product_id = $1
    AND i.status = 'available' AND i.batch_number IS NULL AND i.container_code IS NULL
CROSS JOIN LATERAL (
    SELECT count(*) AS identified_quantity FROM wms.kiz k
    WHERE k.product_id = $1 AND k.location_id = l.location_id AND lifecycle_status = 'active'
) k
WHERE l.location_id = $2
'''
FILTER = '''
FROM wms.kiz k JOIN wms.locations l USING (location_id)
WHERE ($1::varchar IS NULL OR k.product_id = $1)
  AND ($2::varchar IS NULL OR l.location_code = $2)
  AND ($3::varchar IS NULL OR k.lifecycle_status = $3)
'''
LIST_KIZ = 'SELECT k.*, l.location_code ' + FILTER + ' ORDER BY k.kiz_id LIMIT $4 OFFSET $5'
COUNT_KIZ = 'SELECT count(*) ' + FILTER
LIST_EVENTS = '''
SELECT * FROM wms.kiz_events WHERE kiz_id = $1
ORDER BY occurred_at, kiz_event_id LIMIT $2 OFFSET $3
'''
COUNT_EVENTS = 'SELECT count(*) FROM wms.kiz_events WHERE kiz_id = $1'
INTEGRITY = '''
WITH identified AS (
    SELECT product_id, location_id, count(*) AS identified_quantity FROM wms.kiz
    WHERE lifecycle_status = 'active' AND ($1::varchar IS NULL OR product_id = $1)
    GROUP BY product_id, location_id
)
SELECT k.product_id, k.location_id, l.location_code,
    COALESCE(i.quantity, 0) AS physical_quantity, k.identified_quantity,
    k.identified_quantity - COALESCE(i.quantity, 0) AS difference,
    i.inventory_id IS NULL AS inventory_missing
FROM identified k JOIN wms.locations l USING (location_id)
LEFT JOIN wms.inventory i ON i.product_id = k.product_id AND i.location_id = k.location_id
    AND i.status = 'available' AND i.batch_number IS NULL AND i.container_code IS NULL
WHERE k.identified_quantity > COALESCE(i.quantity, 0)
ORDER BY k.product_id, k.location_id
'''
