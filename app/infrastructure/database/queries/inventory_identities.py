"""Identity snapshot includes own contents once, with effective ancestor location."""
CONFIG = "SELECT * FROM wms.inventory_identity_config WHERE document_id=$1"
COUNTS = "SELECT * FROM wms.inventory_identity_counts WHERE document_id=$1 ORDER BY count_id DESC LIMIT $2 OFFSET $3"
LATEST = "SELECT * FROM wms.inventory_identity_counts WHERE document_id=$1 ORDER BY count_id DESC LIMIT 1"
SCANS = "SELECT * FROM wms.inventory_identity_scans WHERE count_id=$1 ORDER BY scan_id"
CONTAINERS = """WITH RECURSIVE tree AS (
 SELECT c.*,c.location_id AS effective_location_id,ARRAY[c.container_id] AS ancestry
 FROM wms.containers c WHERE c.parent_container_id IS NULL
 UNION ALL
 SELECT c.*,t.effective_location_id,t.ancestry||c.container_id
 FROM wms.containers c JOIN tree t ON c.parent_container_id=t.container_id
 WHERE NOT c.container_id=ANY(t.ancestry)
)
SELECT container_id,qr_code,parent_container_id,location_id,effective_location_id,status,ancestry
FROM tree WHERE effective_location_id=ANY($1::bigint[]) ORDER BY container_id LIMIT 100001"""
KIZ = """SELECT kiz_id,kiz_code,product_id,location_id,container_id,lifecycle_status,closed_at,updated_at
 FROM wms.kiz WHERE (location_id=ANY($1::bigint[]) OR container_id=ANY($2::bigint[]))
 AND (cardinality($3::varchar[])=0 OR product_id=ANY($3::varchar[]))
 ORDER BY kiz_id LIMIT 100001"""
CONTENTS = """SELECT content_id,container_id,product_id,batch_number,quantity,status
 FROM wms.container_contents WHERE container_id=ANY($1::bigint[]) AND status='active'
 ORDER BY content_id LIMIT 100001"""
STOCK = """SELECT inventory_id,product_id,location_id,container_code,batch_number,status,quantity,updated_at
 FROM wms.inventory WHERE location_id=ANY($1::bigint[])
 ORDER BY inventory_id LIMIT 100001"""
