-- Stage 3B Phase B2.2: read-only prerequisites for controlled extract.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';

DO $$
DECLARE
    problem record;
BEGIN
    IF to_regclass('wms.container_operations') IS NULL
       OR to_regclass('wms.container_operation_items') IS NULL
       OR to_regclass('wms.containers') IS NULL
       OR to_regclass('wms.container_contents') IS NULL
       OR to_regclass('wms.inventory') IS NULL
       OR to_regclass('wms.movements') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'Container B2.2 prerequisites are missing';
    END IF;

    IF to_regprocedure('wms.apply_container_extract_content(bigint)') IS NOT NULL THEN
        RAISE EXCEPTION 'Container B2.2 extract protocol is already applied';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='wms.container_operations'::regclass
          AND conname='chk_container_operations_type'
          AND pg_get_constraintdef(oid) LIKE '%operation_type%fill%'
    ) THEN
        RAISE EXCEPTION 'Container B2.1 operation contract is not applied';
    END IF;

    IF EXISTS (
        SELECT 1 FROM wms.container_operations o
        WHERE o.result_payload IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM wms.container_operation_items oi
               WHERE oi.operation_id=o.operation_id
           )
           OR EXISTS (
               SELECT 1 FROM wms.container_operation_items oi
               WHERE oi.operation_id=o.operation_id
                 AND (oi.outgoing_movement_ref IS NULL
                      OR oi.incoming_movement_ref IS NULL)
           )
    ) THEN
        RAISE EXCEPTION 'Incomplete committed container operation exists';
    END IF;

    WITH scopes AS (
        SELECT c.container_id,c.qr_code,c.location_id,cc.product_id,cc.batch_number,
               cc.quantity AS content_quantity
        FROM wms.containers c
        JOIN wms.container_contents cc ON cc.container_id=c.container_id
        WHERE cc.status='active'
    )
    SELECT s.container_id,s.product_id,s.batch_number INTO problem
    FROM scopes s
    LEFT JOIN wms.inventory i
      ON i.product_id=s.product_id
     AND i.location_id=s.location_id
     AND i.status='available'
     AND i.batch_number IS NOT DISTINCT FROM s.batch_number
     AND i.container_code=s.qr_code
    WHERE i.quantity IS DISTINCT FROM s.content_quantity
    ORDER BY s.container_id,s.product_id,s.batch_number NULLS FIRST
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B2.2 contents/inventory mismatch: container_id=%, product_id=%, batch=%',
            problem.container_id,problem.product_id,problem.batch_number;
    END IF;

    SELECT c.container_id,i.product_id,i.batch_number INTO problem
    FROM wms.inventory i
    JOIN wms.containers c ON c.qr_code=i.container_code
    LEFT JOIN wms.container_contents cc
      ON cc.container_id=c.container_id
     AND cc.product_id=i.product_id
     AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
     AND cc.status='active'
    WHERE i.status='available' AND cc.content_id IS NULL
    ORDER BY c.container_id,i.product_id,i.batch_number NULLS FIRST
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B2.2 contained inventory has no active content: container_id=%, product_id=%, batch=%',
            problem.container_id,problem.product_id,problem.batch_number;
    END IF;

    SELECT c.container_id,NULL::varchar,NULL::varchar INTO problem
    FROM wms.containers c
    WHERE c.status='empty' AND EXISTS (
              SELECT 1 FROM wms.container_contents cc
              WHERE cc.container_id=c.container_id AND cc.status='active'
          )
    ORDER BY c.container_id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Container B2.2 status/content mismatch: container_id=%',
            problem.container_id;
    END IF;
END;
$$;

SELECT
    (SELECT count(*) FROM wms.container_operations WHERE operation_type='fill') AS fill_operations,
    (SELECT count(*) FROM wms.containers WHERE status='open') AS open_containers,
    (SELECT count(*) FROM wms.container_contents WHERE status='active') AS active_contents;

COMMIT;
