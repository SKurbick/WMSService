-- Все проверки read-only. Ожидается: required objects существуют,
-- receipt location активна, B2 objects/columns ещё отсутствуют.

SELECT
    to_regclass('wms.kiz_import_messages') AS kiz_import_messages,
    to_regclass('wms.kiz') AS kiz,
    to_regclass('wms.kiz_events') AS kiz_events,
    to_regclass('wms.receipt_items') AS receipt_items,
    to_regclass('wms.inventory') AS inventory;

SELECT location_id, location_code, is_active
FROM wms.locations
WHERE location_code = 'PUSHKINO-ПРИЁМКА';

SELECT conname, pg_get_constraintdef(oid, true) AS definition
FROM pg_constraint
WHERE conrelid = 'wms.kiz'::regclass
  AND conname = 'kiz_origin_type_check';

SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'wms'
  AND table_name = 'kiz_import_messages'
  AND column_name IN (
      'business_status',
      'processed_at',
      'business_error_code',
      'business_error_message',
      'business_result'
  )
ORDER BY column_name;

SELECT to_regclass('wms.kiz_import_message_kiz') AS existing_message_kiz_table;

SELECT to_regprocedure('wms.guard_kiz_import_message_kiz_immutable()')
    AS existing_message_kiz_guard;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'wms'
  AND tablename IN ('kiz', 'kiz_import_messages')
ORDER BY tablename, indexname;
