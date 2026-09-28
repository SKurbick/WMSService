-- Read-only preflight для B2.1 registered receipt KIZ.
-- Все result sets должны быть просмотрены до ручного запуска migration.

SELECT
    to_regclass('wms.kiz') AS kiz,
    to_regclass('wms.kiz_events') AS kiz_events,
    to_regclass('wms.kiz_movement_links') AS kiz_movement_links,
    to_regclass('wms.kiz_import_message_kiz') AS kiz_import_message_kiz;

SELECT conname, pg_get_constraintdef(oid, true) AS definition
FROM pg_constraint
WHERE conrelid IN ('wms.kiz'::regclass, 'wms.kiz_events'::regclass)
  AND conname IN (
      'chk_kiz_lifecycle_status',
      'chk_kiz_lifecycle_holder',
      'chk_kiz_events_event_type',
      'chk_kiz_events_from_status',
      'chk_kiz_events_to_status',
      'chk_kiz_events_transition'
  )
ORDER BY conrelid::regclass::text, conname;

SELECT
    product_id,
    origin_reference AS order_guid,
    lifecycle_status,
    count(*) AS kiz_count
FROM wms.kiz
WHERE origin_type = 'receipt_import'
GROUP BY product_id, origin_reference, lifecycle_status
ORDER BY product_id, origin_reference, lifecycle_status;

SELECT
    k.kiz_id,
    k.kiz_code,
    k.product_id,
    k.origin_reference AS order_guid,
    k.location_id,
    location.location_code,
    k.container_id,
    EXISTS (
        SELECT 1
        FROM wms.kiz_movement_links movement_link
        WHERE movement_link.kiz_id = k.kiz_id
    ) AS has_movement_links,
    EXISTS (
        SELECT 1
        FROM wms.kiz_events event
        WHERE event.kiz_id = k.kiz_id
          AND (event.event_type = 'shipped' OR event.movement_ref IS NOT NULL)
    ) AS has_physical_event_history,
    EXISTS (
        SELECT 1
        FROM wms.kiz_import_message_kiz import_link
        WHERE import_link.kiz_id = k.kiz_id
    ) AS has_import_link
FROM wms.kiz k
LEFT JOIN wms.locations location USING (location_id)
WHERE k.origin_type = 'receipt_import'
  AND k.lifecycle_status = 'active'
ORDER BY k.kiz_id;

WITH unsafe AS (
    SELECT k.kiz_id
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
)
SELECT
    count(*) AS unsafe_receipt_import_active_count,
    count(*) = 0 AS migration_ready
FROM unsafe;
