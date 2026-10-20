"""Holder-aware chronological KIZ history."""
GET_CURRENT_STATE = """
SELECT k.*,l.location_code,c.qr_code AS container_qr_code,
 c.location_id AS container_location_id,cl.location_code AS container_location_code
FROM wms.kiz k LEFT JOIN wms.locations l ON l.location_id=k.location_id
LEFT JOIN wms.containers c ON c.container_id=k.container_id
LEFT JOIN wms.locations cl ON cl.location_id=c.location_id
WHERE k.kiz_code=$1
"""
GET_TIMELINE = """
WITH lifecycle_entries AS (
 SELECT e.event_type,e.occurred_at,el.location_code,
  NULL::varchar from_location_code,NULL::varchar to_location_code,
  e.movement_ref,CASE WHEN e.movement_ref IS NULL THEN ARRAY[]::bigint[] ELSE ARRAY[e.movement_ref] END movement_refs,
  NULL::numeric quantity,e.kiz_event_id,e.from_status,e.to_status,e.author,e.reason,
  e.container_id,c.qr_code container_qr_code,NULL::varchar container_operation_type,
  0::smallint source_rank,e.kiz_event_id source_identity,false registry_missing,
  false movement_missing,NULL::varchar movement_type,0::bigint shipped_event_count
 FROM wms.kiz_events e LEFT JOIN wms.locations el ON el.location_id=e.location_id
 LEFT JOIN wms.containers c ON c.container_id=e.container_id
 WHERE e.kiz_id=$1 AND NOT(e.event_type='shipped' AND EXISTS(
  SELECT 1 FROM wms.kiz_movement_links kl JOIN wms.movement_registry r USING(movement_ref)
  JOIN wms.movements m ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
  WHERE kl.kiz_id=e.kiz_id AND kl.movement_ref=e.movement_ref AND m.movement_type='ship'))
), raw_physical AS (
 SELECT m.*,kl.movement_ref,r.movement_ref IS NULL registry_missing,m.movement_id IS NULL movement_missing,
  fl.location_code from_location_code,tl.location_code to_location_code,
  se.kiz_event_id,se.from_status,se.to_status,se.author event_author,se.reason event_reason,
  COALESCE(se.match_count,0)::bigint shipped_event_count,o.operation_type container_operation_type,
  oi.container_id,c.qr_code container_qr_code
 FROM wms.kiz_movement_links kl LEFT JOIN wms.movement_registry r USING(movement_ref)
 LEFT JOIN wms.movements m ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
 LEFT JOIN wms.locations fl ON fl.location_id=m.from_location_id
 LEFT JOIN wms.locations tl ON tl.location_id=m.to_location_id
 LEFT JOIN wms.container_operation_items oi ON m.source_type='container_operation' AND oi.operation_item_id=m.source_item_id
 LEFT JOIN wms.container_operations o ON o.operation_id=oi.operation_id
 LEFT JOIN wms.containers c ON c.container_id=oi.container_id
 LEFT JOIN LATERAL(SELECT e.*,count(*) OVER() match_count FROM wms.kiz_events e
  WHERE e.kiz_id=kl.kiz_id AND e.movement_ref=kl.movement_ref AND e.event_type='shipped'
  ORDER BY e.kiz_event_id LIMIT 1) se ON true
 WHERE kl.kiz_id=$1
), physical_entries AS (
 SELECT CASE WHEN source_type='container_operation' THEN container_operation_type ELSE movement_type END event_type,
  min(created_at) occurred_at,NULL::varchar location_code,
  min(from_location_code) FILTER(WHERE from_location_code IS NOT NULL) from_location_code,
  min(to_location_code) FILTER(WHERE to_location_code IS NOT NULL) to_location_code,
  CASE WHEN count(*)=1 THEN min(movement_ref) ELSE NULL END movement_ref,
  array_agg(movement_ref ORDER BY movement_ref) movement_refs,max(quantity) quantity,
  max(kiz_event_id) kiz_event_id,max(from_status) from_status,max(to_status) to_status,
  COALESCE(max(event_author),max(user_name)) author,COALESCE(max(event_reason),max(reason)) reason,
  max(container_id) container_id,max(container_qr_code) container_qr_code,
  max(container_operation_type) container_operation_type,1::smallint source_rank,
  min(movement_ref) source_identity,bool_or(registry_missing) registry_missing,
  bool_or(movement_missing) movement_missing,max(movement_type) movement_type,
  max(shipped_event_count) shipped_event_count
 FROM raw_physical
 GROUP BY CASE WHEN source_type='container_operation' THEN source_id ELSE NULL END,
          CASE WHEN source_type='container_operation' THEN source_item_id ELSE NULL END,
          CASE WHEN source_type='container_operation' THEN container_operation_type ELSE movement_type END,
          CASE WHEN source_type='container_operation' THEN 0 ELSE movement_ref END
)
SELECT * FROM(SELECT * FROM lifecycle_entries UNION ALL SELECT * FROM physical_entries) timeline
ORDER BY occurred_at NULLS LAST,source_rank,source_identity
"""
