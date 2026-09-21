-- KIZ Stage 2A, Phase 2: read-only compatibility check.
-- Run before 20260910_add_kiz_movement_links.sql.
BEGIN READ ONLY;

DO $$
BEGIN
    IF to_regclass('wms.kiz') IS NULL
       OR to_regclass('wms.kiz_events') IS NULL
       OR to_regclass('wms.movement_registry') IS NULL THEN
        RAISE EXCEPTION 'Phase 2 requires wms.kiz, wms.kiz_events and wms.movement_registry';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'wms.movement_registry'::regclass
          AND contype = 'p'
          AND pg_get_constraintdef(oid) = 'PRIMARY KEY (movement_ref)'
    ) THEN
        RAISE EXCEPTION 'Phase 1 contract mismatch: movement_registry PK is not movement_ref';
    END IF;

    IF (
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = 'wms.kiz'::regclass
          AND conname IN ('kiz_lifecycle_status_check', 'kiz_check')
    ) <> 2 OR (
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = 'wms.kiz_events'::regclass
          AND conname IN (
              'kiz_events_event_type_check',
              'kiz_events_from_status_check',
              'kiz_events_to_status_check',
              'kiz_events_check'
          )
    ) <> 4 THEN
        RAISE EXCEPTION 'KIZ v1 constraint names differ from the Phase 2 migration contract';
    END IF;

    IF EXISTS (
        SELECT 1 FROM wms.kiz
        WHERE lifecycle_status NOT IN ('active', 'error', 'deactivated')
           OR location_id IS NULL
           OR (lifecycle_status = 'active' AND closed_at IS NOT NULL)
           OR (lifecycle_status IN ('error', 'deactivated') AND closed_at IS NULL)
    ) THEN
        RAISE EXCEPTION 'Existing wms.kiz rows violate the KIZ v1 lifecycle/location contract';
    END IF;

    IF EXISTS (
        SELECT 1 FROM wms.kiz_events
        WHERE event_type NOT IN ('assigned', 'marked_as_error', 'deactivated')
           OR (from_status IS NOT NULL AND from_status NOT IN ('active', 'error', 'deactivated'))
           OR to_status NOT IN ('active', 'error', 'deactivated')
           OR location_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Existing wms.kiz_events rows violate the KIZ v1 contract';
    END IF;
END;
$$;

SELECT
    (SELECT count(*) FROM wms.kiz) AS kiz_rows,
    (SELECT count(*) FROM wms.kiz_events) AS kiz_event_rows,
    (SELECT count(*) FROM wms.movement_registry) AS movement_registry_rows;

ROLLBACK;
