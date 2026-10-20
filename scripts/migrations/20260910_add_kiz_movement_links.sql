-- KIZ Stage 2A, Phase 2: immutable KIZ-to-movement associations and future shipment state.
-- Apply manually after Phase 1. This migration does not move or ship KIZ.
BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE wms.kiz, wms.kiz_events IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE wms.kiz
    DROP CONSTRAINT kiz_lifecycle_status_check,
    DROP CONSTRAINT kiz_check,
    ALTER COLUMN location_id DROP NOT NULL,
    ADD CONSTRAINT chk_kiz_lifecycle_status
        CHECK (lifecycle_status IN ('active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_lifecycle_location
        CHECK (
            (lifecycle_status = 'active' AND location_id IS NOT NULL AND closed_at IS NULL)
            OR (lifecycle_status IN ('error', 'deactivated')
                AND location_id IS NOT NULL AND closed_at IS NOT NULL)
            OR (lifecycle_status = 'shipped' AND location_id IS NULL AND closed_at IS NOT NULL)
        );

CREATE TABLE wms.kiz_movement_links (
    kiz_id bigint NOT NULL,
    movement_ref bigint NOT NULL,
    CONSTRAINT pk_kiz_movement_links PRIMARY KEY (kiz_id, movement_ref),
    CONSTRAINT fk_kiz_movement_links_kiz
        FOREIGN KEY (kiz_id) REFERENCES wms.kiz(kiz_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_kiz_movement_links_movement
        FOREIGN KEY (movement_ref) REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_kiz_movement_links_movement_ref_kiz_id
    ON wms.kiz_movement_links(movement_ref, kiz_id);

CREATE FUNCTION wms.guard_kiz_movement_link_immutable() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'KIZ movement associations are immutable';
END;
$$;

CREATE TRIGGER trg_kiz_movement_links_immutable
BEFORE UPDATE OR DELETE ON wms.kiz_movement_links
FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_movement_link_immutable();

ALTER TABLE wms.kiz_events
    ADD COLUMN movement_ref bigint,
    DROP CONSTRAINT kiz_events_event_type_check,
    DROP CONSTRAINT kiz_events_from_status_check,
    DROP CONSTRAINT kiz_events_to_status_check,
    DROP CONSTRAINT kiz_events_check,
    ADD CONSTRAINT chk_kiz_events_event_type
        CHECK (event_type IN ('assigned', 'marked_as_error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_events_from_status
        CHECK (from_status IN ('active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_events_to_status
        CHECK (to_status IN ('active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT fk_kiz_events_link
        FOREIGN KEY (kiz_id, movement_ref)
        REFERENCES wms.kiz_movement_links(kiz_id, movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT chk_kiz_events_transition
        CHECK (
            (event_type = 'assigned' AND from_status IS NULL
                AND to_status = 'active' AND movement_ref IS NULL)
            OR (event_type = 'marked_as_error' AND from_status = 'active'
                AND to_status = 'error' AND movement_ref IS NULL
                AND reason IS NOT NULL AND reason ~ '[^[:space:]]')
            OR (event_type = 'deactivated' AND from_status = 'active'
                AND to_status = 'deactivated' AND movement_ref IS NULL
                AND reason IS NOT NULL AND reason ~ '[^[:space:]]')
            OR (event_type = 'shipped' AND from_status = 'active'
                AND to_status = 'shipped' AND movement_ref IS NOT NULL)
        );

COMMENT ON TABLE wms.kiz_movement_links IS
'Immutable many-to-many association between KIZ identity and stable physical movement identity.';
COMMENT ON COLUMN wms.kiz_events.movement_ref IS
'Stable physical movement reference; required for shipped events and NULL for KIZ v1 identity events.';
COMMENT ON CONSTRAINT chk_kiz_lifecycle_location ON wms.kiz IS
'Active/error/deactivated retain a location; future shipped state has no current location. Direct location UPDATE remains guarded.';

COMMIT;
