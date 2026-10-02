-- Read-only preflight for explicit registered -> active KIZ activation.
-- Review every result set before applying 20261001_add_kiz_activation.sql.

SELECT
    to_regclass('wms.kiz') AS kiz,
    to_regclass('wms.kiz_events') AS kiz_events,
    to_regclass('wms.inventory') AS inventory,
    to_regclass('wms.locations') AS locations,
    to_regclass('wms.kiz_operations') AS kiz_operations,
    to_regclass('wms.kiz_operation_items') AS kiz_operation_items,
    to_regclass('wms.kiz_location_update_authorizations') AS location_authorizations,
    to_regclass('wms.kiz_shipment_authorizations') AS shipment_authorizations,
    to_regclass('wms.kiz_container_holder_authorizations') AS container_authorizations,
    to_regclass('wms.kiz_activation_authorizations') AS existing_activation_authorizations;

SELECT table_name, column_name, data_type, is_nullable, is_identity
FROM information_schema.columns
WHERE table_schema = 'wms'
  AND table_name IN ('kiz_operations', 'kiz_operation_items')
ORDER BY table_name, ordinal_position;

SELECT conrelid::regclass::text AS relation,
       conname,
       pg_get_constraintdef(oid, true) AS definition
FROM pg_constraint
WHERE conrelid IN (
    'wms.kiz'::regclass,
    'wms.kiz_events'::regclass,
    'wms.kiz_operations'::regclass,
    'wms.kiz_operation_items'::regclass
)
  AND conname IN (
      'chk_kiz_lifecycle_status',
      'chk_kiz_lifecycle_holder',
      'chk_kiz_events_event_type',
      'chk_kiz_events_from_status',
      'chk_kiz_events_to_status',
      'chk_kiz_events_transition',
      'chk_kiz_operations_type',
      'uq_kiz_operations_external_identity',
      'uq_kiz_operation_items_external_line'
  )
ORDER BY relation, conname;

SELECT p.proname, pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'wms'
  AND p.proname IN (
      'guard_kiz_identity',
      'guard_kiz_operation_update',
      'guard_kiz_operation_item_update',
      'require_kiz_operation_result_at_commit',
      'require_kiz_final_holder_integrity',
      'guard_kiz_inventory'
  )
ORDER BY p.proname;

SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'wms'
  AND tablename = 'kiz'
  AND indexname IN (
      'uq_kiz_code',
      'idx_kiz_active_scope',
      'idx_kiz_location_status_id',
      'idx_kiz_product_status_id'
  )
ORDER BY indexname;

SELECT kiz_id, kiz_code, product_id, lifecycle_status, location_id, container_id
FROM wms.kiz
WHERE lifecycle_status = 'registered'
  AND (location_id IS NOT NULL OR container_id IS NOT NULL)
ORDER BY kiz_id;

SELECT k.product_id,
       k.location_id,
       count(*) AS active_kiz_count,
       COALESCE(i.quantity, 0) AS physical_quantity
FROM wms.kiz k
LEFT JOIN wms.inventory i
  ON i.product_id = k.product_id
 AND i.location_id = k.location_id
 AND i.status = 'available'
 AND i.batch_number IS NULL
 AND i.container_code IS NULL
WHERE k.lifecycle_status = 'active'
  AND k.location_id IS NOT NULL
  AND k.container_id IS NULL
GROUP BY k.product_id, k.location_id, i.quantity
HAVING count(*) > COALESCE(i.quantity, 0)
ORDER BY k.product_id, k.location_id;
