-- KIZ Stage 2A Phase 3: read-only prerequisites and name collision check.
BEGIN READ ONLY;

DO $$
BEGIN
    IF to_regclass('wms.movement_registry') IS NULL
       OR to_regclass('wms.kiz_movement_links') IS NULL THEN
        RAISE EXCEPTION 'Phase 3 requires applied KIZ Stage 2A Phase 1 and Phase 2';
    END IF;
    IF to_regclass('wms.kiz_operations') IS NOT NULL
       OR to_regclass('wms.kiz_operation_items') IS NOT NULL THEN
        RAISE EXCEPTION 'Phase 3 table name collision or migration already applied';
    END IF;
END;
$$;

SELECT
    (SELECT count(*) FROM wms.movement_registry) AS movement_registry_rows,
    (SELECT count(*) FROM wms.kiz_movement_links) AS kiz_movement_link_rows;

ROLLBACK;
