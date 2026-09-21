-- KIZ Stage 2A Phase 4 read-only preflight.
BEGIN READ ONLY;

DO $$
BEGIN
    IF to_regclass('wms.kiz_operations') IS NULL
       OR to_regclass('wms.kiz_operation_items') IS NULL
       OR to_regclass('wms.kiz_movement_links') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'KIZ Stage 2A Phase 1-3 objects are required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'wms.movements'::regclass
          AND attname IN ('source_type', 'source_id', 'source_item_id')
        GROUP BY attrelid HAVING count(*) = 3
    ) THEN
        RAISE EXCEPTION 'movement provenance columns are required';
    END IF;
END;
$$;

SELECT count(*) AS invalid_active_kiz
FROM wms.kiz
WHERE lifecycle_status = 'active' AND (location_id IS NULL OR closed_at IS NOT NULL);

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
