-- B2.1: receipt import регистрирует identity без physical holder/reservation.
-- Применять вручную только после успешного 20260928_kiz_receipt_b21_preflight.sql.

BEGIN;

LOCK TABLE wms.kiz IN ACCESS EXCLUSIVE MODE;
LOCK TABLE wms.kiz_events IN SHARE ROW EXCLUSIVE MODE;

-- Деление на ноль намеренно останавливает migration, если после preflight
-- появились unsafe receipt-import active rows.
SELECT 1 / CASE WHEN EXISTS (
    SELECT 1
    FROM wms.kiz k
    LEFT JOIN wms.locations location USING (location_id)
    WHERE k.origin_type = 'receipt_import'
      AND k.lifecycle_status = 'active'
      AND (
          k.product_id NOT IN ('testwild', 'testwild2')
          OR k.container_id IS NOT NULL
          OR location.location_code IS DISTINCT FROM 'PUSHKINO-ПРИЁМКА'
          OR NOT EXISTS (
              SELECT 1
              FROM wms.kiz_import_message_kiz import_link
              WHERE import_link.kiz_id = k.kiz_id
          )
          OR EXISTS (
              SELECT 1
              FROM wms.kiz_movement_links movement_link
              WHERE movement_link.kiz_id = k.kiz_id
          )
          OR EXISTS (
              SELECT 1
              FROM wms.kiz_events event
              WHERE event.kiz_id = k.kiz_id
                AND (event.event_type = 'shipped' OR event.movement_ref IS NOT NULL)
          )
      )
) THEN 0 ELSE 1 END AS migration_safety_check;

DROP TRIGGER trg_kiz_identity_guard ON wms.kiz;

ALTER TABLE wms.kiz
    DROP CONSTRAINT chk_kiz_lifecycle_holder,
    DROP CONSTRAINT chk_kiz_lifecycle_status;

ALTER TABLE wms.kiz
    ADD CONSTRAINT chk_kiz_lifecycle_status
        CHECK (lifecycle_status IN ('registered', 'active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_lifecycle_holder
        CHECK (
            lifecycle_status = 'registered'
            AND closed_at IS NULL
            AND location_id IS NULL
            AND container_id IS NULL
            OR lifecycle_status = 'active'
            AND closed_at IS NULL
            AND ((location_id IS NOT NULL)::integer + (container_id IS NOT NULL)::integer) = 1
            OR lifecycle_status IN ('error', 'deactivated')
            AND closed_at IS NOT NULL
            AND NOT (location_id IS NOT NULL AND container_id IS NOT NULL)
            OR lifecycle_status = 'shipped'
            AND closed_at IS NOT NULL
            AND location_id IS NULL
            AND container_id IS NULL
        );

ALTER TABLE wms.kiz_events
    DROP CONSTRAINT chk_kiz_events_event_type,
    DROP CONSTRAINT chk_kiz_events_from_status,
    DROP CONSTRAINT chk_kiz_events_to_status,
    DROP CONSTRAINT chk_kiz_events_transition;

ALTER TABLE wms.kiz_events
    ADD CONSTRAINT chk_kiz_events_event_type
        CHECK (event_type IN ('registered', 'assigned', 'marked_as_error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_events_from_status
        CHECK (from_status IN ('registered', 'active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_events_to_status
        CHECK (to_status IN ('registered', 'active', 'error', 'deactivated', 'shipped')),
    ADD CONSTRAINT chk_kiz_events_transition
        CHECK (
            event_type = 'registered'
            AND from_status IS NULL
            AND to_status = 'registered'
            AND location_id IS NULL
            AND container_id IS NULL
            AND movement_ref IS NULL
            OR event_type = 'assigned'
            AND from_status IS NULL
            AND to_status = 'active'
            AND movement_ref IS NULL
            OR event_type = 'marked_as_error'
            AND from_status = 'active'
            AND to_status = 'error'
            AND movement_ref IS NULL
            AND reason IS NOT NULL
            AND reason ~ '[^[:space:]]'
            OR event_type = 'deactivated'
            AND from_status = 'active'
            AND to_status = 'deactivated'
            AND movement_ref IS NULL
            AND reason IS NOT NULL
            AND reason ~ '[^[:space:]]'
            OR event_type = 'shipped'
            AND from_status = 'active'
            AND to_status = 'shipped'
            AND movement_ref IS NOT NULL
        );

CREATE INDEX idx_kiz_current_receipt_origin
    ON wms.kiz (origin_type, origin_reference, product_id, lifecycle_status, kiz_id)
    WHERE lifecycle_status IN ('registered', 'active');

UPDATE wms.kiz
SET lifecycle_status = 'registered',
    location_id = NULL,
    container_id = NULL,
    closed_at = NULL,
    updated_at = now()
WHERE origin_type = 'receipt_import'
  AND lifecycle_status = 'active';

CREATE TRIGGER trg_kiz_identity_guard
BEFORE UPDATE OR DELETE ON wms.kiz
FOR EACH ROW
EXECUTE FUNCTION wms.guard_kiz_identity();

COMMIT;
