-- Stage 3B Phase B3 read-only production preflight.
-- Run manually before 20260916_add_container_b3_move_unpack_all.sql.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    violations bigint;
BEGIN
    IF to_regclass('wms.container_operations') IS NULL
       OR to_regclass('wms.container_operation_items') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'B1/B2 container operation schema is missing';
    END IF;
    IF to_regprocedure('wms.apply_container_extract_content(bigint)') IS NULL THEN
        RAISE EXCEPTION 'B2.2 extract protocol is missing';
    END IF;
    IF to_regprocedure('wms.move_container_inventory()') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_trigger
           WHERE tgrelid='wms.containers'::regclass
             AND tgname='trg_move_container_inventory' AND NOT tgisinternal
       ) THEN
        RAISE EXCEPTION 'Expected legacy container move trigger/function is missing';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='wms' AND table_name='container_operations'
          AND column_name='container_id'
    ) OR EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='wms' AND table_name='container_operation_items'
          AND column_name='movement_ref'
    ) THEN
        RAISE EXCEPTION 'B3 schema appears partially or fully applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.container_operations
        WHERE operation_type NOT IN ('fill','extract')
    ) THEN
        RAISE EXCEPTION 'Unexpected pre-B3 container operation type exists';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM wms.container_operations o
        LEFT JOIN wms.container_operation_items oi ON oi.operation_id=o.operation_id
        GROUP BY o.operation_id
        HAVING count(oi.operation_item_id)=0 OR count(DISTINCT oi.container_id)<>1
    ) THEN
        RAISE EXCEPTION 'Existing operation cannot be backfilled to exactly one container';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.containers
        WHERE parent_container_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Nested containers are forbidden for Phase B3';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.containers
        WHERE location_id IS NULL
           OR status NOT IN ('empty','open','sealed','blocked')
           OR container_type NOT IN ('pallet','box','cage','trolley')
    ) THEN
        RAISE EXCEPTION 'Container has unsupported location/status/type';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.container_contents
        WHERE status='active'
        GROUP BY container_id,product_id,batch_number
        HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'Duplicate active container content scope exists';
    END IF;
    SELECT count(*) INTO violations
    FROM wms.container_contents cc
    JOIN wms.containers c ON c.container_id=cc.container_id
    LEFT JOIN wms.inventory i
      ON i.product_id=cc.product_id
     AND i.location_id=c.location_id
     AND i.status='available'
     AND i.batch_number IS NOT DISTINCT FROM cc.batch_number
     AND i.container_code=c.qr_code
    WHERE cc.status='active'
      AND (i.inventory_id IS NULL OR i.quantity IS DISTINCT FROM cc.quantity);
    IF violations<>0 THEN
        RAISE EXCEPTION 'Active contents without matching contained inventory: %',violations;
    END IF;
    SELECT count(*) INTO violations
    FROM wms.inventory i
    LEFT JOIN wms.containers c ON c.qr_code=i.container_code
    LEFT JOIN wms.container_contents cc
      ON cc.container_id=c.container_id
     AND cc.product_id=i.product_id
     AND cc.batch_number IS NOT DISTINCT FROM i.batch_number
     AND cc.status='active'
    WHERE i.container_code IS NOT NULL
      AND (c.container_id IS NULL OR i.status<>'available'
           OR i.location_id<>c.location_id OR cc.content_id IS NULL
           OR cc.quantity IS DISTINCT FROM i.quantity);
    IF violations<>0 THEN
        RAISE EXCEPTION 'Contained inventory without matching current contents/location: %',violations;
    END IF;
END;
$$;

ROLLBACK;
