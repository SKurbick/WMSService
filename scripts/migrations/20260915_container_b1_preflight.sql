-- Stage 3B Phase B1: read-only compatibility checks for the flat container contract.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';

DO $$
DECLARE
    problem record;
BEGIN
    IF to_regclass('wms.containers') IS NULL
       OR to_regclass('wms.container_contents') IS NULL
       OR to_regclass('wms.inventory') IS NULL
       OR to_regclass('wms.movements') IS NULL THEN
        RAISE EXCEPTION 'Container B1 prerequisites are missing';
    END IF;

    SELECT container_id, qr_code INTO problem
    FROM wms.containers
    WHERE location_id IS NULL
       OR parent_container_id IS NOT NULL
       OR status IS NULL
       OR qr_code = ''
       OR qr_code <> btrim(qr_code, E' \t\n\r\f\v')
    ORDER BY container_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 incompatible container row: container_id=%, qr_code=%',
            problem.container_id, problem.qr_code;
    END IF;

    SELECT min(container_id) AS container_id, qr_code INTO problem
    FROM wms.containers
    GROUP BY qr_code
    HAVING count(*) > 1
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 duplicate QR identity: container_id=%, qr_code=%',
            problem.container_id, problem.qr_code;
    END IF;

    SELECT container_id, qr_code INTO problem
    FROM wms.containers
    WHERE status NOT IN ('empty', 'open', 'opened', 'sealed', 'blocked')
       OR container_type NOT IN ('pallet', 'box', 'cage', 'trolley')
    ORDER BY container_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 unsupported status/type: container_id=%, qr_code=%',
            problem.container_id, problem.qr_code;
    END IF;

    SELECT container_id, product_id INTO problem
    FROM wms.container_contents
    WHERE status IS NULL
    ORDER BY content_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 NULL content status: container_id=%, product_id=%',
            problem.container_id, problem.product_id;
    END IF;

    SELECT container_id, product_id INTO problem
    FROM wms.container_contents
    GROUP BY container_id, product_id, batch_number, status
    HAVING count(*) > 1
    ORDER BY container_id, product_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 duplicate content scope: container_id=%, product_id=%',
            problem.container_id, problem.product_id;
    END IF;

    SELECT c.container_id, c.qr_code INTO problem
    FROM wms.containers c
    WHERE c.status = 'empty'
      AND EXISTS (
          SELECT 1 FROM wms.container_contents cc
          WHERE cc.container_id = c.container_id AND cc.status = 'active'
      )
    ORDER BY c.container_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 empty container has active contents: container_id=%, qr_code=%',
            problem.container_id, problem.qr_code;
    END IF;

    SELECT NULL::bigint AS container_id, i.container_code AS qr_code INTO problem
    FROM wms.inventory i
    LEFT JOIN wms.containers c ON c.qr_code = i.container_code
    WHERE i.container_code IS NOT NULL
      AND (c.container_id IS NULL OR i.location_id IS DISTINCT FROM c.location_id)
    ORDER BY i.container_code LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 incompatible inventory reference: container_code=%',
            problem.qr_code;
    END IF;

    SELECT NULL::bigint AS container_id, m.container_code AS qr_code INTO problem
    FROM wms.movements m
    LEFT JOIN wms.containers c ON c.qr_code = m.container_code
    WHERE m.container_code IS NOT NULL AND c.container_id IS NULL
    ORDER BY m.created_at, m.movement_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Container B1 orphan movement container_code=%', problem.qr_code;
    END IF;
END;
$$;

SELECT
    (SELECT count(*) FROM wms.containers) AS containers,
    (SELECT count(*) FROM wms.container_contents) AS contents,
    (SELECT count(*) FROM wms.inventory WHERE container_code IS NOT NULL)
        AS container_inventory_rows,
    (SELECT count(*) FROM wms.containers WHERE parent_container_id IS NOT NULL)
        AS nested_rows,
    (SELECT jsonb_object_agg(status, row_count)
     FROM (SELECT status, count(*) AS row_count
           FROM wms.containers GROUP BY status) statuses) AS statuses,
    (SELECT jsonb_object_agg(container_type, row_count)
     FROM (SELECT container_type, count(*) AS row_count
           FROM wms.containers GROUP BY container_type) types) AS container_types;

COMMIT;
