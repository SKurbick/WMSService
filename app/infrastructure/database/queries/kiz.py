"""KIZ holder-aware exact loose/container scopes."""
GET_PRODUCT = 'SELECT id FROM public.products WHERE id = $1'
GET_LOCATION = 'SELECT location_id, location_code FROM wms.locations WHERE location_code = $1'
GET_CONTAINER = '''
SELECT c.container_id,c.qr_code,c.location_id,l.location_code
FROM wms.containers c JOIN wms.locations l ON l.location_id=c.location_id
WHERE ($1::bigint IS NULL OR c.container_id=$1)
  AND ($2::varchar IS NULL OR c.qr_code=$2)
'''
STATE_SELECT = '''
SELECT k.*,l.location_code,c.qr_code AS container_qr_code,
       c.location_id AS container_location_id,cl.location_code AS container_location_code
FROM wms.kiz k
LEFT JOIN wms.locations l ON l.location_id=k.location_id
LEFT JOIN wms.containers c ON c.container_id=k.container_id
LEFT JOIN wms.locations cl ON cl.location_id=c.location_id
'''
GET_KIZ = STATE_SELECT + ' WHERE k.kiz_code=$1'
LOCK_KIZ = GET_KIZ + ' FOR UPDATE OF k'
LOCK_INVENTORY = '''
SELECT inventory_id, quantity FROM wms.inventory
WHERE product_id = $1 AND location_id = $2 AND status = 'available'
  AND batch_number IS NULL AND container_code IS NULL FOR UPDATE
'''
TOUCH_INVENTORY = 'UPDATE wms.inventory SET updated_at = now() WHERE inventory_id = $1'
COUNT_ACTIVE = '''
SELECT count(*) FROM wms.kiz
WHERE product_id=$1 AND location_id=$2 AND container_id IS NULL AND lifecycle_status='active'
'''
INSERT_KIZ = '''
INSERT INTO wms.kiz(kiz_code,product_id,location_id,container_id,created_by,metadata,lifecycle_status,origin_type)
VALUES($1,$2,$3,NULL,$4,$5::jsonb,'active','warehouse_assignment') RETURNING kiz_id
'''
INSERT_EVENT = '''
INSERT INTO wms.kiz_events
(kiz_id,event_type,from_status,to_status,product_id,location_id,container_id,author,reason,metadata)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
'''
TERMINATE = '''
UPDATE wms.kiz SET lifecycle_status=$2,closed_at=now(),updated_at=now()
WHERE kiz_id=$1 AND lifecycle_status='active'
'''
SUMMARY = '''
SELECT $1::varchar product_id,l.location_id,l.location_code,
 NULL::bigint container_id,NULL::varchar container_qr_code,
 NULL::bigint container_location_id,NULL::varchar container_location_code,
 COALESCE(i.quantity,0) physical_quantity,k.identified_quantity,
 COALESCE(i.quantity,0)-k.identified_quantity unidentified_quantity,
 COALESCE(i.quantity,0)>=k.identified_quantity integrity_ok
FROM wms.locations l
LEFT JOIN wms.inventory i ON i.location_id=l.location_id AND i.product_id=$1
 AND i.status='available' AND i.batch_number IS NULL AND i.container_code IS NULL
CROSS JOIN LATERAL(SELECT count(*) identified_quantity FROM wms.kiz k
 WHERE k.product_id=$1 AND k.location_id=l.location_id AND k.container_id IS NULL
 AND k.lifecycle_status='active') k WHERE l.location_id=$2
'''
CONTAINER_SUMMARY = '''
SELECT $1::varchar product_id,NULL::bigint location_id,NULL::varchar location_code,
 c.container_id,c.qr_code container_qr_code,c.location_id container_location_id,
 l.location_code container_location_code,COALESCE(i.quantity,0) physical_quantity,
 k.identified_quantity,COALESCE(i.quantity,0)-k.identified_quantity unidentified_quantity,
 COALESCE(i.quantity,0)>=k.identified_quantity integrity_ok
FROM wms.containers c JOIN wms.locations l ON l.location_id=c.location_id
LEFT JOIN wms.inventory i ON i.product_id=$1 AND i.location_id=c.location_id
 AND i.status='available' AND i.batch_number IS NULL AND i.container_code=c.qr_code
CROSS JOIN LATERAL(SELECT count(*) identified_quantity FROM wms.kiz k
 WHERE k.product_id=$1 AND k.container_id=c.container_id AND k.location_id IS NULL
 AND k.lifecycle_status='active') k WHERE c.container_id=$2
'''
FILTER = '''
FROM wms.kiz k
LEFT JOIN wms.locations l ON l.location_id=k.location_id
LEFT JOIN wms.containers c ON c.container_id=k.container_id
LEFT JOIN wms.locations cl ON cl.location_id=c.location_id
WHERE ($1::varchar IS NULL OR k.product_id=$1)
 AND ($2::varchar IS NULL OR l.location_code=$2)
 AND ($3::varchar IS NULL OR k.lifecycle_status=$3)
 AND ($4::bigint IS NULL OR k.container_id=$4)
 AND ($5::varchar IS NULL OR c.qr_code=$5)
'''
LIST_KIZ = '''SELECT k.*,l.location_code,c.qr_code container_qr_code,
 c.location_id container_location_id,cl.location_code container_location_code ''' + FILTER + ' ORDER BY k.kiz_id LIMIT $6 OFFSET $7'
COUNT_KIZ = 'SELECT count(*) ' + FILTER
LIST_EVENTS = 'SELECT * FROM wms.kiz_events WHERE kiz_id=$1 ORDER BY occurred_at,kiz_event_id LIMIT $2 OFFSET $3'
COUNT_EVENTS = 'SELECT count(*) FROM wms.kiz_events WHERE kiz_id=$1'
INTEGRITY = "SELECT * FROM wms.check_kiz_holder_integrity() WHERE ($1::varchar IS NULL OR product_id=$1)"
LOCK_CONTAINER_HOLDER = '''
SELECT c.container_id,c.qr_code,c.location_id FROM wms.containers c
WHERE c.container_id=$1 FOR UPDATE
'''
LOCK_CONTAINER_INVENTORY = '''
SELECT i.inventory_id,i.quantity FROM wms.inventory i JOIN wms.containers c
 ON c.container_id=$2 AND c.qr_code=i.container_code AND c.location_id=i.location_id
WHERE i.product_id=$1 AND i.status='available' AND i.batch_number IS NULL FOR UPDATE
'''
