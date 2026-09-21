-- Stage 3B Phase B2.1: read-only prerequisites for container fill.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';

DO $$
DECLARE
    problem record;
BEGIN
    IF to_regclass('wms.containers') IS NULL
       OR to_regclass('wms.container_contents') IS NULL
       OR to_regclass('wms.inventory') IS NULL
       OR to_regclass('wms.movements') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'Container B2.1 prerequisites are missing';
    END IF;

    IF to_regclass('wms.container_operations') IS NOT NULL
       OR to_regclass('wms.container_operation_items') IS NOT NULL THEN
        RAISE EXCEPTION 'Container B2.1 operation tables already exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='wms.containers'::regclass
          AND conname='chk_container_flat'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='wms.container_contents'::regclass
          AND conname='uq_container_content'
    ) THEN
        RAISE EXCEPTION 'Container B1 contract is not applied';
    END IF;

    WITH scopes AS (
        SELECT c.container_id,c.qr_code,c.location_id,cc.product_id,cc.batch_number,
               cc.quantity AS content_quantity
        FROM wms.containers c
        JOIN wms.container_contents cc ON cc.container_id=c.container_id
        WHERE cc.status='active'
    )
    SELECT s.container_id,s.product_id INTO problem
    FROM scopes s
    LEFT JOIN wms.inventory i
      ON i.product_id=s.product_id
     AND i.location_id=s.location_id
     AND i.status='available'
     AND i.batch_number IS NOT DISTINCT FROM s.batch_number
     AND i.container_code=s.qr_code
    WHERE i.quantity IS DISTINCT FROM s.content_quantity
    ORDER BY s.container_id,s.product_id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B2.1 contents/inventory mismatch: container_id=%, product_id=%',
            problem.container_id,problem.product_id;
    END IF;

    SELECT c.container_id,i.product_id INTO problem
    FROM wms.inventory i
    JOIN wms.containers c ON c.qr_code=i.container_code
    LEFT JOIN wms.container_contents cc
      ON cc.container_id=c.container_id
     AND cc.product_id=i.product_id
     AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
     AND cc.status='active'
    WHERE i.status='available' AND cc.content_id IS NULL
    ORDER BY c.container_id,i.product_id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B2.1 contained inventory has no active content: container_id=%, product_id=%',
            problem.container_id,problem.product_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM wms.movements WHERE source_type='container_operation' LIMIT 1
    ) THEN
        RAISE EXCEPTION 'Container B2.1 source_type is already used';
    END IF;
END;
$$;

SELECT
    (SELECT count(*) FROM wms.containers) AS containers,
    (SELECT count(*) FROM wms.container_contents WHERE status='active') AS active_contents,
    (SELECT count(*) FROM wms.inventory WHERE container_code IS NULL
       AND status='available') AS loose_available_scopes,
    (SELECT count(*) FROM wms.inventory WHERE container_code IS NOT NULL
       AND status='available') AS contained_available_scopes;

COMMIT;
