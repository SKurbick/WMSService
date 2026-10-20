-- Read-only preflight for FBS + KIZ Stage 1.

SELECT
    to_regclass('wms.fbs_shipment_task_results') AS task_results,
    to_regclass('wms.fbs_shipment_items') AS shipment_items,
    to_regclass('wms.fbs_shipments') AS shipments,
    to_regclass('wms.kiz') AS kiz,
    to_regclass('wms.kiz_events') AS kiz_events,
    to_regclass('wms.kiz_operations') AS kiz_operations,
    to_regclass('wms.kiz_operation_items') AS kiz_operation_items,
    to_regclass('wms.kiz_movement_links') AS kiz_movement_links,
    to_regclass('wms.movement_registry') AS movement_registry,
    to_regclass('public.assembly_task') AS assembly_task;

SELECT
    count(*) FILTER (WHERE result.outcome = 'written_off') AS written_off,
    count(*) FILTER (
        WHERE result.outcome = 'written_off'
          AND result.movement_id IS NOT NULL
          AND result.movement_created_at IS NOT NULL
    ) AS written_off_with_identity,
    count(*) FILTER (
        WHERE result.outcome = 'written_off'
          AND registry.movement_ref IS NOT NULL
    ) AS resolved_movement_ref,
    count(*) FILTER (
        WHERE result.outcome = 'written_off'
          AND registry.movement_ref IS NULL
    ) AS missing_registry_mapping
FROM wms.fbs_shipment_task_results AS result
LEFT JOIN wms.movement_registry AS registry
  ON registry.movement_id = result.movement_id
 AND registry.movement_created_at = result.movement_created_at;

SELECT movement_id, movement_created_at, count(*) AS registry_rows
FROM wms.movement_registry
GROUP BY movement_id, movement_created_at
HAVING count(*) > 1;

SELECT
    constraint_name,
    constraint_type
FROM information_schema.table_constraints
WHERE table_schema = 'wms'
  AND table_name = 'fbs_shipment_task_results'
ORDER BY constraint_name;
