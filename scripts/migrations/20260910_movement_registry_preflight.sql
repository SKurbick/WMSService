-- Read-only preflight for KIZ Stage 2A Phase 1.
-- Run against the target immediately before the movement registry migration.

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT current_setting('server_version_num')::integer >= 150000 AS supported_postgresql;

SELECT movement_id, created_at, count(*) AS row_count
FROM wms.movements
GROUP BY movement_id, created_at
HAVING count(*) > 1
ORDER BY created_at, movement_id;

SELECT
    count(*) AS movement_rows,
    min(created_at) AS oldest_movement_created_at,
    max(created_at) AS newest_movement_created_at
FROM wms.movements;

SELECT
    child.oid::regclass AS partition_name,
    pg_get_expr(child.relpartbound, child.oid) AS partition_bound,
    count(m.*) AS movement_rows
FROM pg_inherits AS inheritance
JOIN pg_class AS child ON child.oid = inheritance.inhrelid
LEFT JOIN wms.movements AS m ON m.tableoid = child.oid
WHERE inheritance.inhparent = 'wms.movements'::regclass
GROUP BY child.oid, child.relpartbound
ORDER BY child.oid::regclass::text;

COMMIT;
