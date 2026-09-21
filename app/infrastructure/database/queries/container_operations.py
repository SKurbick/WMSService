"""SQL primitives for caller-owned container operation transactions."""

TRY_CREATE_OPERATION = """
INSERT INTO wms.container_operations(
    operation_type,container_id,source_system,external_operation_id,request_fingerprint,author
) VALUES ($1,$2,$3,$4,$5,$6)
ON CONFLICT (source_system,operation_type,external_operation_id) DO NOTHING
RETURNING *;
"""
GET_OPERATION_FOR_UPDATE = """
SELECT * FROM wms.container_operations
WHERE operation_type=$1 AND source_system=$2 AND external_operation_id=$3
FOR UPDATE;
"""
CREATE_ITEM = """
INSERT INTO wms.container_operation_items(
    operation_id,external_line_id,container_id,product_id,batch_number,quantity
) VALUES ($1,$2,$3,$4,$5,$6)
RETURNING *;
"""
ATTACH_MOVEMENTS = """
UPDATE wms.container_operation_items
SET outgoing_movement_ref=$2,incoming_movement_ref=$3
WHERE operation_item_id=$1
  AND outgoing_movement_ref IS NULL
  AND incoming_movement_ref IS NULL
RETURNING *;
"""
STORE_RESULT = """
UPDATE wms.container_operations
SET result_payload=$2::jsonb,updated_at=now()
WHERE operation_id=$1 AND result_payload IS NULL
RETURNING *;
"""
LOCK_CONTAINER = """
SELECT container_id,qr_code,location_id,status,parent_container_id
FROM wms.containers WHERE container_id=$1 FOR UPDATE
"""
GET_PRODUCTS = "SELECT id FROM public.products WHERE id=ANY($1::varchar[]) ORDER BY id"
LOCK_SCOPE = """
SELECT inventory_id,container_code,quantity
FROM wms.inventory
WHERE product_id=$1 AND location_id=$2 AND status='available'
  AND batch_number IS NOT DISTINCT FROM $3::varchar
  AND (container_code IS NULL OR container_code=$4)
ORDER BY container_code NULLS FIRST
FOR UPDATE
"""
CREATE_OUTGOING_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type,product_id,from_location_id,to_location_id,quantity,
    batch_number,container_code,user_name,reason,source_type,source_id,source_item_id
) VALUES ('transfer',$1,$2,NULL,$3,$4,NULL,$5,
          'Fill container from loose stock','container_operation',$6,$7)
RETURNING movement_id,created_at
"""
CREATE_INCOMING_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type,product_id,from_location_id,to_location_id,quantity,
    batch_number,container_code,user_name,reason,source_type,source_id,source_item_id
) VALUES ('transfer',$1,NULL,$2,$3,$4,$5,$6,
          'Fill container from loose stock','container_operation',$7,$8)
RETURNING movement_id,created_at
"""
GET_MOVEMENT_REF = """
SELECT movement_ref FROM wms.movement_registry
WHERE movement_id=$1 AND movement_created_at=$2
"""
AUTHORIZE_CONTENT_INSERT = """
SELECT set_config('wms.container_fill_item_id',$1::text,true)
"""
UPSERT_CONTENT = """
INSERT INTO wms.container_contents(
    container_id,product_id,quantity,batch_number,status,is_scanned
) VALUES ($1,$2,$3,$4,'active',false)
ON CONFLICT ON CONSTRAINT uq_container_content
DO UPDATE SET quantity=wms.container_contents.quantity+EXCLUDED.quantity,
              updated_at=now()
RETURNING content_id,quantity
"""
CLEAR_CONTENT_AUTHORIZATION = """
SELECT set_config('wms.container_fill_item_id','',true)
"""
OPEN_CONTAINER = """
UPDATE wms.containers SET status='open',updated_at=now()
WHERE container_id=$1 AND status='empty'
"""
CHECK_SCOPE = """
SELECT
    COALESCE((SELECT quantity FROM wms.inventory
      WHERE product_id=$1 AND location_id=$2 AND status='available'
        AND batch_number IS NOT DISTINCT FROM $3::varchar
        AND container_code IS NULL),0) AS loose_quantity,
    COALESCE((SELECT quantity FROM wms.inventory
      WHERE product_id=$1 AND location_id=$2 AND status='available'
        AND batch_number IS NOT DISTINCT FROM $3::varchar
        AND container_code=$4),0) AS contained_quantity,
    COALESCE((SELECT quantity FROM wms.container_contents
      WHERE container_id=$5 AND product_id=$1
        AND batch_number IS NOT DISTINCT FROM $3::varchar
        AND status='active'),0) AS content_quantity
"""

LOCK_CONTENT_SCOPE = """
SELECT content_id,quantity
FROM wms.container_contents
WHERE container_id=$1 AND product_id=$2
  AND batch_number IS NOT DISTINCT FROM $3::varchar
  AND status='active'
FOR UPDATE
"""
CREATE_EXTRACT_OUTGOING_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type,product_id,from_location_id,to_location_id,quantity,
    batch_number,container_code,user_name,reason,source_type,source_id,source_item_id
) VALUES ('transfer',$1,$2,NULL,$3,$4,$5,$6,
          'Extract stock from container','container_operation',$7,$8)
RETURNING movement_id,created_at
"""
CREATE_EXTRACT_INCOMING_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type,product_id,from_location_id,to_location_id,quantity,
    batch_number,container_code,user_name,reason,source_type,source_id,source_item_id
) VALUES ('transfer',$1,NULL,$2,$3,$4,NULL,$5,
          'Extract stock from container','container_operation',$6,$7)
RETURNING movement_id,created_at
"""
APPLY_EXTRACT_CONTENT = """
SELECT wms.apply_container_extract_content($1)
"""
SYNC_CONTAINER_STATUS = """
UPDATE wms.containers c
SET status=CASE WHEN EXISTS (
        SELECT 1 FROM wms.container_contents cc
        WHERE cc.container_id=c.container_id AND cc.status='active'
    ) THEN 'open' ELSE 'empty' END,
    updated_at=now()
WHERE c.container_id=$1
RETURNING status
"""

CREATE_SNAPSHOT_ITEM = """
INSERT INTO wms.container_operation_items(
    operation_id,external_line_id,container_id,product_id,batch_number,quantity
) VALUES ($1,$2,$3,$4,$5,$6)
RETURNING *;
"""
ATTACH_MOVE_MOVEMENT = """
UPDATE wms.container_operation_items
SET movement_ref=$2
WHERE operation_item_id=$1
  AND movement_ref IS NULL
  AND outgoing_movement_ref IS NULL
  AND incoming_movement_ref IS NULL
RETURNING *;
"""
GET_LOCATION_ID_BY_CODE = "SELECT location_id FROM wms.locations WHERE location_code=$1"
LOCK_LOCATION_CONTEXTS = """
SELECT target.location_id,target.location_code,target.is_active,
       root.location_id AS warehouse_id,root.location_code AS warehouse_code
FROM wms.locations target
JOIN LATERAL (
    SELECT ancestor.location_id,ancestor.location_code
    FROM wms.locations ancestor
    WHERE ancestor.parent_location_id IS NULL
      AND ancestor.path @> target.path
    ORDER BY nlevel(ancestor.path) DESC
    LIMIT 1
) root ON true
WHERE target.location_id=ANY($1::bigint[])
ORDER BY target.location_id
FOR UPDATE OF target
"""
LOCK_ACTIVE_CONTENTS = """
SELECT content_id,product_id,batch_number,quantity
FROM wms.container_contents
WHERE container_id=$1 AND status='active'
ORDER BY product_id,batch_number NULLS FIRST,content_id
FOR UPDATE
"""
LOCK_MOVE_INVENTORY_SCOPE = """
SELECT inventory_id,location_id,quantity
FROM wms.inventory
WHERE product_id=$1
  AND location_id=ANY($2::bigint[])
  AND status='available'
  AND batch_number IS NOT DISTINCT FROM $3::varchar
  AND container_code=$4
ORDER BY location_id,inventory_id
FOR UPDATE
"""
CHECK_MOVE_SCOPE = """
SELECT
    COALESCE((SELECT quantity FROM wms.inventory
      WHERE product_id=$1 AND location_id=$2 AND status='available'
        AND batch_number IS NOT DISTINCT FROM $4::varchar
        AND container_code=$5),0) AS from_quantity,
    COALESCE((SELECT quantity FROM wms.inventory
      WHERE product_id=$1 AND location_id=$3 AND status='available'
        AND batch_number IS NOT DISTINCT FROM $4::varchar
        AND container_code=$5),0) AS to_quantity
"""
CHECK_CONTAINER_PROJECTION = """
SELECT
    NOT EXISTS (
        SELECT 1
        FROM wms.container_contents cc
        LEFT JOIN wms.inventory i
          ON i.product_id=cc.product_id
         AND i.location_id=$3
         AND i.status='available'
         AND i.batch_number IS NOT DISTINCT FROM cc.batch_number
         AND i.container_code=$2
        WHERE cc.container_id=$1 AND cc.status='active'
          AND (i.inventory_id IS NULL OR i.quantity IS DISTINCT FROM cc.quantity)
    )
    AND NOT EXISTS (
        SELECT 1
        FROM wms.inventory i
        LEFT JOIN wms.container_contents cc
          ON cc.container_id=$1
         AND cc.product_id=i.product_id
         AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
         AND cc.status='active'
        WHERE i.container_code=$2
          AND (
              i.status<>'available'
              OR i.location_id<>$3
              OR cc.content_id IS NULL
              OR cc.quantity IS DISTINCT FROM i.quantity
          )
    ) AS is_valid
"""
CREATE_MOVE_MOVEMENT = """
INSERT INTO wms.movements(
    movement_type,product_id,from_location_id,to_location_id,quantity,
    batch_number,container_code,user_name,reason,source_type,source_id,source_item_id
) VALUES ('transfer',$1,$2,$3,$4,$5,$6,$7,
          'Move container between locations','container_operation',$8,$9)
RETURNING movement_id,created_at
"""
AUTHORIZE_CONTAINER_MOVE = "SELECT set_config('wms.container_move_operation_id',$1::text,true)"
UPDATE_CONTAINER_LOCATION_CONTROLLED = """
UPDATE wms.containers SET location_id=$2,updated_at=now()
WHERE container_id=$1 AND location_id=$3
RETURNING location_id
"""
CLEAR_CONTAINER_MOVE_AUTHORIZATION = "SELECT set_config('wms.container_move_operation_id','',true)"

LOCK_KIZ_CODES = """
SELECT kiz_id,kiz_code,product_id,location_id,container_id,lifecycle_status,closed_at
FROM wms.kiz WHERE kiz_code=ANY($1::text[]) ORDER BY kiz_id FOR UPDATE
"""
LOCK_CONTAINER_KIZ = """
SELECT kiz_id,kiz_code,product_id,location_id,container_id,lifecycle_status,closed_at
FROM wms.kiz WHERE container_id=$1 AND lifecycle_status='active'
ORDER BY kiz_id FOR UPDATE
"""
COUNT_ACTIVE_LOOSE_KIZ = """
SELECT count(*) FROM wms.kiz WHERE product_id=$1 AND location_id=$2
AND container_id IS NULL AND lifecycle_status='active'
"""
COUNT_ACTIVE_CONTAINER_KIZ = """
SELECT count(*) FROM wms.kiz WHERE product_id=$1 AND container_id=$2
AND location_id IS NULL AND lifecycle_status='active'
"""
TRANSITION_KIZ_CONTAINER_HOLDER = "SELECT wms.transition_kiz_container_holder($1,$2,$3)"
CREATE_KIZ_LINKS = """
INSERT INTO wms.kiz_movement_links(kiz_id,movement_ref)
SELECT selected.kiz_id,refs.movement_ref
FROM unnest($1::bigint[]) selected(kiz_id)
CROSS JOIN unnest($2::bigint[]) refs(movement_ref)
"""
SET_KIZ_CONTAINER_OPERATION = "SELECT set_config('wms.kiz_container_operation_id',$1::text,true)"
CLEAR_KIZ_CONTAINER_OPERATION = "SELECT set_config('wms.kiz_container_operation_id','',true)"
CHECK_KIZ_INTEGRITY = "SELECT * FROM wms.check_kiz_holder_integrity()"
