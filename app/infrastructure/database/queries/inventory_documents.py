"""Only new document tables are written. Physical stock is read without row locks."""

LOCK_COMMAND = "SELECT pg_advisory_xact_lock(hashtextextended('inventory-command:' || $1, 0))"
GET_COMMAND = "SELECT * FROM wms.inventory_document_commands WHERE idempotency_key=$1"
SAVE_COMMAND = """
INSERT INTO wms.inventory_document_commands(idempotency_key,document_id,action,request_hash,result)
VALUES($1,$2,$3,$4,$5::jsonb)
"""
CREATE_DOCUMENT = """
INSERT INTO wms.inventory_documents(title,mode,warehouse_location_id,created_by)
VALUES($1,$2,$3,$4) RETURNING *
"""
SAVE_FILTERS = """
INSERT INTO wms.inventory_document_filters(document_id,location_selections,product_ids)
VALUES($1,$2::jsonb,$3::varchar[])
ON CONFLICT(document_id) DO UPDATE
SET location_selections=EXCLUDED.location_selections,product_ids=EXCLUDED.product_ids
"""
LOCK_DOCUMENT = "SELECT * FROM wms.inventory_documents WHERE document_id=$1 FOR UPDATE"
DOCUMENT_SELECT = """
SELECT d.*,f.location_selections AS locations,f.product_ids,
 (SELECT count(*) FROM wms.inventory_document_locations l WHERE l.document_id=d.document_id) AS location_count,
 (SELECT count(*) FROM wms.inventory_document_items i WHERE i.document_id=d.document_id) AS item_count
FROM wms.inventory_documents d JOIN wms.inventory_document_filters f USING(document_id)
"""
GET_DOCUMENT = DOCUMENT_SELECT + " WHERE d.document_id=$1"
LIST_DOCUMENTS = (
    DOCUMENT_SELECT
    + """
WHERE ($1::varchar IS NULL OR d.status=$1)
 AND ($2::bigint IS NULL OR d.warehouse_location_id=$2)
ORDER BY d.document_id DESC LIMIT $3 OFFSET $4
"""
)
COUNT_DOCUMENTS = """SELECT count(*) FROM wms.inventory_documents
WHERE ($1::varchar IS NULL OR status=$1)
 AND ($2::bigint IS NULL OR warehouse_location_id=$2)"""
UPDATE_DRAFT = """UPDATE wms.inventory_documents
SET title=$2,mode=$3,warehouse_location_id=$4,version=version+1,updated_at=clock_timestamp()
WHERE document_id=$1"""
BUMP_VERSION = """UPDATE wms.inventory_documents
SET version=version+1,updated_at=clock_timestamp() WHERE document_id=$1"""
START_DOCUMENT = """UPDATE wms.inventory_documents
SET status='counting',started_at=$2,current_revision=1,version=version+1,
 updated_at=clock_timestamp() WHERE document_id=$1"""
SET_REVISION = """UPDATE wms.inventory_documents
SET current_revision=$2,version=version+1,updated_at=clock_timestamp() WHERE document_id=$1"""
SUBMIT = """UPDATE wms.inventory_documents SET status='pending_approval',
submitted_revision=current_revision,version=version+1,updated_at=clock_timestamp()
WHERE document_id=$1"""
RETURN_TO_COUNT = """UPDATE wms.inventory_documents SET status='counting',
submitted_revision=NULL,version=version+1,updated_at=clock_timestamp() WHERE document_id=$1"""
CLOSE = """UPDATE wms.inventory_documents SET status='closed_without_adjustment',
closed_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp() WHERE document_id=$1"""

GET_LOCATIONS = """SELECT location_id,location_code,parent_location_id,path::text AS path,
is_active FROM wms.locations WHERE location_id=ANY($1::bigint[])"""
EXPAND_SCOPE = """
SELECT l.location_id,l.location_code,l.path::text AS path,coalesce(l.is_active,false) AS was_active
FROM wms.locations l JOIN wms.locations root ON root.location_id=$1
WHERE l.path OPERATOR(wms.<@) root.path
 AND (cardinality($2::bigint[])=0 OR l.location_id=ANY($2::bigint[])
      OR EXISTS(SELECT 1 FROM wms.locations selected
                WHERE selected.location_id=ANY($3::bigint[])
                AND l.path OPERATOR(wms.<@) selected.path))
ORDER BY l.location_id
"""
GET_PRODUCTS = "SELECT id FROM public.products WHERE id=ANY($1::varchar[])"
PARENT_TREE = """WITH RECURSIVE tree(location_id) AS (
 SELECT location_id FROM wms.locations WHERE location_id=$1
 UNION
 SELECT l.location_id FROM wms.locations l JOIN tree t ON l.parent_location_id=t.location_id
) SELECT location_id FROM tree"""
INSERT_LOCATION = """INSERT INTO wms.inventory_document_locations
(document_id,location_id,location_code,path,was_active) VALUES($1,$2,$3,$4,$5)"""
INSERT_CLAIM = "INSERT INTO wms.inventory_location_claims(location_id,document_id) VALUES($1,$2)"
RELEASE_CLAIMS = "DELETE FROM wms.inventory_location_claims WHERE document_id=$1"
FIXED_LOCATIONS = (
    "SELECT * FROM wms.inventory_document_locations WHERE document_id=$1 ORDER BY location_id"
)

# No timestamp watermark: older-created movements committed after the snapshot must
# be detectable by identity-set comparison too. Bounded evidence, never silently truncated.
GET_STOCK = """
SELECT i.inventory_id,i.product_id,i.location_id,i.status,i.batch_number,i.container_code,
 i.quantity AS physical_quantity,i.updated_at AS source_updated_at,
 coalesce(l.is_active,false) AS location_active,l.location_code,
 CASE WHEN i.batch_number IS NULL THEN
   (SELECT count(*) FROM wms.kiz k WHERE k.product_id=i.product_id
    AND k.location_id=i.location_id AND k.container_id IS NULL AND k.lifecycle_status='active')
 ELSE 0 END AS active_kiz_quantity
FROM wms.inventory i JOIN wms.locations l USING(location_id)
WHERE i.location_id=ANY($1::bigint[]) AND i.status='available' AND i.container_code IS NULL
 AND (cardinality($2::varchar[])=0 OR i.product_id=ANY($2::varchar[]))
ORDER BY i.product_id,i.location_id,i.inventory_id LIMIT 100001
"""
GET_KIZ = """
SELECT kiz_id,product_id,location_id,container_id,lifecycle_status,updated_at
FROM wms.kiz
WHERE ((location_id=ANY($1::bigint[]) AND container_id IS NULL)
 OR (lifecycle_status='registered' AND location_id IS NULL AND container_id IS NULL))
 AND (cardinality($2::varchar[])=0 OR product_id=ANY($2::varchar[]))
ORDER BY kiz_id LIMIT 100001
"""
GET_MOVEMENTS = """
SELECT m.movement_id,m.created_at,r.movement_ref,m.movement_type,m.product_id,
 m.from_location_id,m.to_location_id,m.quantity,m.batch_number,m.container_code,
 m.source_type,m.source_id,m.source_item_id
FROM wms.movements m LEFT JOIN wms.movement_registry r
 ON r.movement_id=m.movement_id AND r.movement_created_at=m.created_at
WHERE (m.from_location_id=ANY($1::bigint[]) OR m.to_location_id=ANY($1::bigint[]))
 AND m.container_code IS NULL
 AND (cardinality($2::varchar[])=0 OR m.product_id=ANY($2::varchar[]))
ORDER BY m.created_at,m.movement_id LIMIT 100001
"""
GET_EVENTS = """
SELECT kiz_event_id,kiz_id,event_type,from_status,to_status,product_id,location_id,
 container_id,movement_ref,occurred_at
FROM wms.kiz_events
WHERE (location_id=ANY($1::bigint[]) OR location_id IS NULL)
 AND (cardinality($2::varchar[])=0 OR product_id=ANY($2::varchar[]))
ORDER BY kiz_event_id LIMIT 100001
"""
INSERT_COMPONENT = """INSERT INTO wms.inventory_document_stock_components
(document_id,inventory_id,product_id,location_id,status,batch_number,container_code,
 physical_quantity,active_kiz_quantity,unidentified_quantity,source_updated_at)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"""
INSERT_KIZ_SNAPSHOT = """INSERT INTO wms.inventory_document_kiz_snapshot
(document_id,kiz_id,product_id,location_id,container_id,lifecycle_status)
VALUES($1,$2,$3,$4,$5,$6)"""
INSERT_REVISION = """INSERT INTO wms.inventory_document_revisions
(document_id,revision_no,kind,observed_at,author,evidence) VALUES($1,$2,$3,$4,$5,$6::jsonb)"""
GET_REVISION = (
    "SELECT * FROM wms.inventory_document_revisions WHERE document_id=$1 AND revision_no=$2"
)
LIST_REVISIONS = """SELECT * FROM wms.inventory_document_revisions
WHERE document_id=$1 ORDER BY revision_no LIMIT $2 OFFSET $3"""
COUNT_REVISIONS = "SELECT count(*) FROM wms.inventory_document_revisions WHERE document_id=$1"

INSERT_ITEM = """INSERT INTO wms.inventory_document_items
(document_id,product_id,location_id,initial_expected_quantity,discovered,initial_conflicts)
VALUES($1,$2,$3,$4,$5,$6::jsonb) RETURNING item_id"""
ITEM_SELECT = """
SELECT i.*,p.name AS product_name,l.location_code,
 a.status AS attempt_status,a.actual_quantity,a.conflicts AS attempt_conflicts,a.evidence AS attempt_evidence
FROM wms.inventory_document_items i JOIN public.products p ON p.id=i.product_id
JOIN wms.inventory_document_locations l ON l.document_id=i.document_id AND l.location_id=i.location_id
LEFT JOIN wms.inventory_count_attempts a ON a.attempt_id=i.latest_attempt_id
"""
GET_ITEM = ITEM_SELECT + " WHERE i.document_id=$1 AND i.item_id=$2"
ALL_ITEMS = ITEM_SELECT + " WHERE i.document_id=$1 ORDER BY i.item_id"
LIST_ITEMS = ALL_ITEMS + " LIMIT $2 OFFSET $3"
COUNT_ITEMS = "SELECT count(*) FROM wms.inventory_document_items WHERE document_id=$1"
REQUIRE_RECOUNT = """UPDATE wms.inventory_document_items SET needs_recount=true
WHERE document_id=$1 AND item_id=ANY($2::bigint[])"""
SET_LATEST_ATTEMPT = """UPDATE wms.inventory_document_items
SET latest_attempt_id=$3 WHERE document_id=$1 AND item_id=$2"""
FINISH_ITEM = """UPDATE wms.inventory_document_items SET needs_recount=false
WHERE document_id=$1 AND item_id=$2"""

CREATE_ATTEMPT = """INSERT INTO wms.inventory_count_attempts
(document_id,item_id,attempt_number,revision_no,author,evidence)
SELECT $1,$2,coalesce(max(attempt_number),0)+1,$3,$4,$5::jsonb
FROM wms.inventory_count_attempts WHERE item_id=$2 RETURNING *"""
GET_ATTEMPT = """SELECT * FROM wms.inventory_count_attempts
WHERE document_id=$1 AND item_id=$2 AND attempt_id=$3"""
SAVE_PROGRESS = """UPDATE wms.inventory_count_attempts SET actual_quantity=$2
WHERE attempt_id=$1 RETURNING *"""
FINISH_ATTEMPT = """UPDATE wms.inventory_count_attempts
SET actual_quantity=$2,status='completed',completed_at=clock_timestamp(),evidence=$3::jsonb,conflicts=$4::jsonb
WHERE attempt_id=$1 RETURNING *"""
LIST_ATTEMPTS = """SELECT * FROM wms.inventory_count_attempts
WHERE document_id=$1 AND item_id=$2 ORDER BY attempt_number LIMIT $3 OFFSET $4"""
COUNT_ATTEMPTS = (
    "SELECT count(*) FROM wms.inventory_count_attempts WHERE document_id=$1 AND item_id=$2"
)
INSERT_HISTORY = """INSERT INTO wms.inventory_document_history
(document_id,document_version,action,author,details) VALUES($1,$2,$3,$4,$5::jsonb)"""
LIST_HISTORY = """SELECT * FROM wms.inventory_document_history
WHERE document_id=$1 ORDER BY event_id LIMIT $2 OFFSET $3"""
COUNT_HISTORY = "SELECT count(*) FROM wms.inventory_document_history WHERE document_id=$1"
