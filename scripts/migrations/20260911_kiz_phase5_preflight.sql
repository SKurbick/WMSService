-- KIZ Stage 2A Phase 5 read-only preflight.
BEGIN READ ONLY;

DO $$
BEGIN
    IF to_regclass('wms.kiz_location_update_authorizations') IS NULL
       OR to_regclass('wms.kiz_operations') IS NULL
       OR to_regclass('wms.kiz_operation_items') IS NULL
       OR to_regclass('wms.kiz_movement_links') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'KIZ Stage 2A Phase 1-4 objects are required';
    END IF;
    IF to_regprocedure('wms.transfer_kiz_location(bigint,bigint,character varying,bigint,bigint)') IS NULL THEN
        RAISE EXCEPTION 'Phase 4 controlled transfer function is required';
    END IF;
END;
$$;

SELECT count(*) AS invalid_kiz_lifecycle_rows
FROM wms.kiz
WHERE (lifecycle_status = 'active' AND (location_id IS NULL OR closed_at IS NOT NULL))
   OR (lifecycle_status = 'shipped' AND (location_id IS NOT NULL OR closed_at IS NULL));

WITH identified AS (
    SELECT product_id, location_id, count(*) AS quantity
    FROM wms.kiz WHERE lifecycle_status = 'active'
    GROUP BY product_id, location_id
)
SELECT count(*) AS current_integrity_violations
FROM identified k
LEFT JOIN wms.inventory i
  ON i.product_id=k.product_id AND i.location_id=k.location_id
 AND i.status='available' AND i.batch_number IS NULL AND i.container_code IS NULL
WHERE k.quantity > COALESCE(i.quantity,0);

ROLLBACK;
