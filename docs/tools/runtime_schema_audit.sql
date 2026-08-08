-- STATUS: UTILITY. Read-only psql audit; no DDL or DML.
\set ON_ERROR_STOP on
\pset pager off
\pset null '<NULL>'

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

\echo '=== audit_context ==='
SELECT
    now() AS audited_at,
    current_database() AS database_name,
    current_user AS database_user,
    version() AS postgres_version,
    current_setting('server_version_num') AS server_version_num,
    current_setting('TimeZone') AS timezone,
    current_setting('search_path') AS search_path;

\echo '=== extensions_and_ltree ==='
SELECT e.extname, e.extversion, n.nspname AS extension_schema
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
ORDER BY e.extname;

SELECT n.nspname AS type_schema, t.typname, t.typtype
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE t.typname IN ('ltree', 'lquery', 'ltxtquery')
ORDER BY n.nspname, t.typname;

\echo '=== schemas_and_relations ==='
SELECT n.nspname AS schema_name, c.relname, c.relkind,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('wms', 'public')
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
ORDER BY n.nspname, c.relkind, c.relname;

\echo '=== columns ==='
SELECT table_schema, table_name, ordinal_position, column_name, data_type,
       udt_schema, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'wms'
   OR (table_schema = 'public' AND table_name IN (
       'products', 'users', 'user_permissions', 'assembly_task',
       'supply_to_sellers_warehouse'
   ))
ORDER BY table_schema, table_name, ordinal_position;

\echo '=== constraints ==='
SELECT n.nspname AS schema_name, c.relname AS table_name, con.conname,
       con.contype, con.convalidated, con.condeferrable, con.condeferred,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'wms'
ORDER BY c.relname, con.contype, con.conname;

\echo '=== critical_constraints ==='
SELECT c.relname AS table_name, con.conname, con.convalidated,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'wms'
  AND con.conname IN (
      'chk_movements_quantity_positive',
      'chk_movements_has_side',
      'chk_fbs_shipments_source',
      'chk_movement_type',
      'container_contents_quantity_check',
      'chk_content_status'
  )
ORDER BY con.conname;

\echo '=== indexes ==='
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'wms'
ORDER BY tablename, indexname;

\echo '=== partitions ==='
SELECT pn.nspname AS parent_schema, parent.relname AS parent_table,
       cn.nspname AS child_schema, child.relname AS child_table,
       pg_get_expr(child.relpartbound, child.oid, true) AS partition_bound
FROM pg_inherits i
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_namespace pn ON pn.oid = parent.relnamespace
JOIN pg_class child ON child.oid = i.inhrelid
JOIN pg_namespace cn ON cn.oid = child.relnamespace
WHERE pn.nspname = 'wms'
ORDER BY parent.relname, child.relname;

\echo '=== functions ==='
SELECT n.nspname AS schema_name, p.proname,
       pg_get_function_identity_arguments(p.oid) AS identity_arguments,
       pg_get_function_result(p.oid) AS result_type,
       p.provolatile, p.proparallel,
       md5(pg_get_functiondef(p.oid)) AS definition_md5,
       pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'wms'
ORDER BY p.proname, identity_arguments;

\echo '=== triggers ==='
SELECT n.nspname AS schema_name, c.relname AS table_name, t.tgname,
       t.tgenabled, pg_get_triggerdef(t.oid, true) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'wms'
  AND NOT t.tgisinternal
ORDER BY c.relname, t.tgname;

\echo '=== views ==='
SELECT n.nspname AS schema_name, c.relname AS view_name, c.relkind,
       pg_get_viewdef(c.oid, true) AS definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'wms'
  AND c.relkind IN ('v', 'm')
ORDER BY c.relname;

\echo '=== optional_objects ==='
SELECT object_name, to_regclass(object_name) IS NOT NULL AS exists
FROM (VALUES
    ('wms.stock_reservation_orders'),
    ('wms.stock_reservation_events'),
    ('wms.v_product_availability'),
    ('wms.operation_locations'),
    ('wms.kit_operations'),
    ('wms.kit_operation_items'),
    ('wms.re_sorting_operations'),
    ('wms.re_sorting_operation_items'),
    ('wms.movements')
) AS objects(object_name)
ORDER BY object_name;

\echo '=== safe_data_quality_counts ==='
SELECT 'bad_movement_quantity' AS metric, COUNT(*)::bigint AS value
FROM wms.movements WHERE quantity IS NULL OR quantity <= 0
UNION ALL
SELECT 'movement_without_side', COUNT(*)::bigint
FROM wms.movements WHERE from_location_id IS NULL AND to_location_id IS NULL
UNION ALL
SELECT 'negative_inventory', COUNT(*)::bigint
FROM wms.inventory WHERE quantity < 0
UNION ALL
SELECT 'orphan_movement_container_code', COUNT(*)::bigint
FROM wms.movements m LEFT JOIN wms.containers c ON c.qr_code = m.container_code
WHERE m.container_code IS NOT NULL AND c.container_id IS NULL
UNION ALL
SELECT 'orphan_inventory_container_code', COUNT(*)::bigint
FROM wms.inventory i LEFT JOIN wms.containers c ON c.qr_code = i.container_code
WHERE i.container_code IS NOT NULL AND c.container_id IS NULL
UNION ALL
SELECT 'orphan_fbs_movement_id', COUNT(*)::bigint
FROM wms.fbs_shipment_items f
WHERE f.movement_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM wms.movements m WHERE m.movement_id = f.movement_id)
ORDER BY metric;

\echo '=== duplicate_movement_ids ==='
SELECT COUNT(*) AS duplicated_movement_id_groups
FROM (
    SELECT movement_id
    FROM wms.movements
    GROUP BY movement_id
    HAVING COUNT(*) > 1
) duplicates;

COMMIT;
