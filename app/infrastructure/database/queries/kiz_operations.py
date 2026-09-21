"""SQL for caller-owned KIZ-aware idempotency transactions."""

TRY_CREATE_OPERATION = """
INSERT INTO wms.kiz_operations(
    operation_type,source_system,external_operation_id,request_fingerprint,author
) VALUES ($1,$2,$3,$4,$5)
ON CONFLICT (source_system,operation_type,external_operation_id) DO NOTHING
RETURNING *;
"""

GET_OPERATION_FOR_UPDATE = """
SELECT * FROM wms.kiz_operations
WHERE source_system=$1 AND operation_type=$2 AND external_operation_id=$3
FOR UPDATE;
"""

CREATE_ITEM = """
INSERT INTO wms.kiz_operation_items(operation_id,external_line_id)
VALUES ($1,$2)
RETURNING *;
"""

ATTACH_MOVEMENT = """
UPDATE wms.kiz_operation_items
SET movement_ref=$2
WHERE operation_item_id=$1 AND movement_ref IS NULL
RETURNING *;
"""

STORE_RESULT = """
UPDATE wms.kiz_operations
SET result_payload=$2::jsonb,updated_at=now()
WHERE operation_id=$1 AND result_payload IS NULL
RETURNING *;
"""

GET_OPERATION = "SELECT * FROM wms.kiz_operations WHERE operation_id=$1;"
