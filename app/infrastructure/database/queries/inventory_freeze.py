ACTIVE = "SELECT * FROM wms.inventory_freezes WHERE document_id=$1 AND state<>'released'"
LATEST = "SELECT * FROM wms.inventory_freezes WHERE document_id=$1 ORDER BY freeze_id DESC LIMIT 1"
LOCK_FENCES = """SELECT * FROM wms.inventory_location_fences
WHERE location_id=ANY($1::bigint[]) ORDER BY location_id FOR UPDATE NOWAIT"""
CREATE = """INSERT INTO wms.inventory_freezes(document_id,state,created_by)
VALUES($1,'freezing',$2) RETURNING *"""
CLAIM = """UPDATE wms.inventory_location_fences SET freeze_id=$2,generation=generation+1
WHERE location_id=ANY($1::bigint[])"""
CONFIRM = """UPDATE wms.inventory_freezes SET state='frozen',epoch=epoch+1,
confirmed_by=$2,confirmed_at=clock_timestamp(),confirmation=$3,baseline=$4::jsonb
WHERE freeze_id=$1 RETURNING *"""
RELEASE_FENCES = """UPDATE wms.inventory_location_fences SET freeze_id=NULL,generation=generation+1
WHERE freeze_id=$1"""
RELEASE = """UPDATE wms.inventory_freezes SET state='released',released_at=clock_timestamp()
WHERE freeze_id=$1"""
EVENT = """INSERT INTO wms.inventory_freeze_events(freeze_id,action,author,details)
VALUES($1,$2,$3,$4::jsonb)"""
EVENTS = """SELECT event_id,action,author,occurred_at,location_id,product_id,fbs_shipment_id,movement_id,movement_created_at,details
FROM wms.inventory_freeze_events WHERE freeze_id=$1 ORDER BY event_id DESC LIMIT $2 OFFSET $3"""
CANCEL_OPEN = """UPDATE wms.inventory_count_attempts SET status='cancelled'
WHERE document_id=$1 AND status='in_progress'"""
RECOUNT_ALL = "UPDATE wms.inventory_document_items SET needs_recount=true WHERE document_id=$1"
CONFLICTING_TASKS = """WITH RECURSIVE relevant(task_id) AS (
 SELECT t.task_id FROM wms.tasks t
 WHERE t.from_location_id=ANY($1::bigint[]) OR t.to_location_id=ANY($1::bigint[])
  OR EXISTS(SELECT 1 FROM wms.task_items i WHERE i.task_id=t.task_id AND i.from_location_id=ANY($1::bigint[]))
 OR (t.status NOT IN ('completed','completed_with_discrepancy','cancelled')
     AND t.from_location_id IS NULL AND t.to_location_id IS NULL
     AND cardinality(wms.inventory_task_locations(ARRAY[t.task_id]))=0)
 UNION
 SELECT child.task_id FROM wms.tasks child JOIN relevant parent ON child.parent_task_id=parent.task_id
) SELECT t.task_id,t.task_type,t.status,t.from_location_id,t.to_location_id,
 cardinality(wms.inventory_task_locations(ARRAY[t.task_id]))=0 AS scope_unknown
FROM wms.tasks t JOIN relevant r USING(task_id)
WHERE t.status NOT IN ('completed','completed_with_discrepancy','cancelled')
ORDER BY t.task_id LIMIT 101"""
