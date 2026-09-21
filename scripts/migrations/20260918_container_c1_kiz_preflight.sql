BEGIN;
SET TRANSACTION READ ONLY;

DO $$
DECLARE invalid_count bigint;
BEGIN
    IF to_regclass('wms.containers') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='wms.containers'::regclass AND contype='p') THEN
        RAISE EXCEPTION 'C1 preflight: wms.containers primary key is missing';
    END IF;
    IF to_regprocedure('wms.guard_controlled_container_movement()') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='wms.movements'::regclass AND tgname='trg_guard_controlled_container_movement' AND tgenabled<>'D') THEN
        RAISE EXCEPTION 'C1 preflight: B4 controlled container movement guard is missing';
    END IF;
    IF to_regclass('wms.movement_registry') IS NULL OR to_regclass('wms.kiz_movement_links') IS NULL THEN
        RAISE EXCEPTION 'C1 preflight: movement registry/KIZ links infrastructure is missing';
    END IF;
    SELECT count(*) INTO invalid_count FROM wms.kiz
    WHERE (lifecycle_status='active' AND (location_id IS NULL OR closed_at IS NOT NULL))
       OR (lifecycle_status IN ('error','deactivated') AND closed_at IS NULL)
       OR (lifecycle_status='shipped' AND (location_id IS NOT NULL OR closed_at IS NULL));
    IF invalid_count<>0 THEN
        RAISE EXCEPTION 'C1 preflight: % KIZ rows violate the current holder lifecycle',invalid_count;
    END IF;
END $$;

ROLLBACK;
