"""Preparations, atomic applications and movement links; stock changes only via movements."""

GET_PREPARATION = """SELECT * FROM wms.inventory_application_preparations
WHERE document_id=$1 AND submitted_revision=$2"""
LATEST_PREPARATION = """SELECT * FROM wms.inventory_application_preparations
WHERE document_id=$1 ORDER BY preparation_id DESC LIMIT 1"""
INSERT_PREPARATION = """INSERT INTO wms.inventory_application_preparations
(document_id,submitted_revision,observed_document_version,created_by,blockers,evidence,lines)
VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb) RETURNING *"""
INSERT_COMPONENT = """INSERT INTO wms.inventory_application_components
(preparation_id,document_id,item_id,component_number,stock_evidence)
VALUES($1,$2,$3,$4,$5::jsonb)"""
GET_COMPONENTS = """SELECT component_id,item_id,component_number,stock_evidence
FROM wms.inventory_application_components WHERE preparation_id=$1
ORDER BY item_id,component_number"""

PILOT_COMPONENTS = """SELECT component_id,item_id,component_number,stock_evidence,adjustment_quantity,target_quantity
FROM wms.inventory_application_components WHERE preparation_id=$1 ORDER BY item_id,component_number"""
INSERT_PILOT = """INSERT INTO wms.inventory_application_preparations
(document_id,submitted_revision,observed_document_version,created_by,blockers,evidence,lines,can_apply,freeze_id,freeze_epoch)
VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10) RETURNING *"""
INSERT_PILOT_COMPONENT = """INSERT INTO wms.inventory_application_components
(preparation_id,document_id,item_id,component_number,stock_evidence,adjustment_quantity,target_quantity)
VALUES($1,$2,$3,$4,$5::jsonb,$6,$7)"""
GET_APPLICATION = "SELECT * FROM wms.inventory_applications WHERE document_id=$1"
CREATE_APPLICATION = """INSERT INTO wms.inventory_applications
(document_id,preparation_id,approved_revision,freeze_id,freeze_epoch,status,applied_by)
VALUES($1,$2,$3,$4,$5,'applying',$6) RETURNING application_id"""
INSERT_ADJUSTMENT = """INSERT INTO wms.movements
(movement_type,product_id,from_location_id,to_location_id,quantity,batch_number,container_code,
 user_name,reason,source_type,source_id,source_item_id)
VALUES('adjust',$1,$2,$3,$4,NULL,NULL,$5,'Inventory final count','inventory_document',$6,$7)
RETURNING movement_id,created_at"""
LINK = """INSERT INTO wms.inventory_application_movements(application_id,component_id,movement_ref)
SELECT $1,$2,movement_ref FROM wms.movement_registry WHERE movement_id=$3 AND movement_created_at=$4
RETURNING movement_ref"""
MOVEMENTS = """SELECT l.component_id,r.movement_ref,m.movement_id,m.created_at,m.movement_type,
m.product_id,m.from_location_id,m.to_location_id,m.quantity,m.source_type,m.source_id,m.source_item_id
FROM wms.inventory_application_movements l JOIN wms.movement_registry r USING(movement_ref)
JOIN wms.movements m ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
WHERE l.application_id=$1 ORDER BY l.component_id"""
