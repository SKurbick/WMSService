"""SQL primitives for one caller-owned explicit KIZ shipment transaction."""

GET_PRODUCTS = "SELECT id FROM public.products WHERE id = ANY($1::varchar[]) ORDER BY id"
GET_LOCATIONS = """
SELECT location_id, location_code FROM wms.locations
WHERE location_code = ANY($1::varchar[]) ORDER BY location_id
"""
LOCK_LOCATIONS = """
SELECT location_id FROM wms.locations
WHERE location_id = ANY($1::bigint[]) ORDER BY location_id FOR UPDATE
"""
LOCK_INVENTORY = """
SELECT inventory_id, product_id, location_id, quantity
FROM wms.inventory
WHERE product_id=$1 AND location_id=$2 AND status='available'
  AND batch_number IS NULL AND container_code IS NULL
FOR UPDATE
"""
LOCK_KIZ = """
SELECT kiz_id, kiz_code, product_id, location_id, lifecycle_status, closed_at
FROM wms.kiz WHERE kiz_code = ANY($1::varchar[])
ORDER BY kiz_id FOR UPDATE
"""
COUNT_ACTIVE = """
SELECT count(*) FROM wms.kiz
WHERE product_id=$1 AND location_id=$2 AND lifecycle_status='active'
"""
CONTROLLED_SHIP_KIZ = "SELECT wms.ship_kiz($1,$2,$3,$4,$5)"
CREATE_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type, product_id, from_location_id, to_location_id, quantity,
    batch_number, container_code, user_name, reason,
    source_type, source_id, source_item_id
) VALUES ('ship',$1,$2,NULL,$3,NULL,NULL,$4,$5,'kiz_operation',$6,$7)
RETURNING movement_id, created_at
"""
GET_MOVEMENT_REF = """
SELECT movement_ref FROM wms.movement_registry
WHERE movement_id=$1 AND movement_created_at=$2
"""
CREATE_LINKS = """
INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref)
SELECT unnest($1::bigint[]), $2
"""
CREATE_SHIPPED_EVENTS = """
INSERT INTO wms.kiz_events(
    kiz_id,event_type,from_status,to_status,product_id,location_id,
    movement_ref,author,reason,metadata
)
SELECT kiz_id,'shipped','active','shipped',$2,$3,$4,$5,$6,'{}'::jsonb
FROM unnest($1::bigint[]) AS selected(kiz_id)
"""
CHECK_SCOPE = """
SELECT COALESCE(i.quantity,0) AS physical_quantity,
       (SELECT count(*) FROM wms.kiz k
        WHERE k.product_id=$1 AND k.location_id=$2
          AND k.lifecycle_status='active') AS identified_quantity
FROM (SELECT 1) seed
LEFT JOIN wms.inventory i ON i.product_id=$1 AND i.location_id=$2
  AND i.status='available' AND i.batch_number IS NULL AND i.container_code IS NULL
"""
