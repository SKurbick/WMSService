BEGIN;

DO $$
DECLARE
    invalid_movements bigint;
    inconsistent_scopes bigint;
BEGIN
    IF to_regprocedure('wms.register_container(character varying,character varying,character varying,jsonb)') IS NULL THEN
        RAISE EXCEPTION 'Missing wms.register_container(varchar,varchar,varchar,jsonb)';
    END IF;
    IF to_regprocedure('wms.unpack_from_container(character varying,character varying,numeric)') IS NULL THEN
        RAISE EXCEPTION 'Missing legacy wms.unpack_from_container(varchar,varchar,numeric)';
    END IF;
    IF to_regprocedure('wms.move_container_inventory()') IS NULL THEN
        RAISE EXCEPTION 'Missing legacy wms.move_container_inventory()';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'wms.containers'::regclass
          AND tgname = 'trg_move_container_inventory'
          AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'Missing legacy trigger wms.containers.trg_move_container_inventory';
    END IF;

    SELECT COUNT(*) INTO invalid_movements
    FROM wms.movements m
    WHERE (m.container_code IS NOT NULL
           AND m.source_type IS DISTINCT FROM 'container_operation')
       OR (m.source_type = 'container_operation'
           AND (m.source_id IS NULL OR m.source_item_id IS NULL));
    IF invalid_movements <> 0 THEN
        RAISE EXCEPTION 'B4 preflight: % container movements have invalid provenance', invalid_movements;
    END IF;

    WITH active_contents AS (
        SELECT c.qr_code, c.location_id, cc.product_id, cc.batch_number,
               SUM(cc.quantity) AS quantity
        FROM wms.containers c
        JOIN wms.container_contents cc
          ON cc.container_id = c.container_id AND cc.status = 'active'
        GROUP BY c.qr_code, c.location_id, cc.product_id, cc.batch_number
    ), contained_inventory AS (
        SELECT i.container_code AS qr_code, i.location_id, i.product_id, i.batch_number,
               SUM(i.quantity) AS quantity
        FROM wms.inventory i
        WHERE i.container_code IS NOT NULL AND i.status = 'available'
        GROUP BY i.container_code, i.location_id, i.product_id, i.batch_number
    )
    SELECT COUNT(*) INTO inconsistent_scopes
    FROM active_contents c
    FULL OUTER JOIN contained_inventory i
      ON i.qr_code = c.qr_code
     AND i.location_id = c.location_id
     AND i.product_id = c.product_id
     AND i.batch_number IS NOT DISTINCT FROM c.batch_number
    WHERE c.qr_code IS NULL OR i.qr_code IS NULL OR c.quantity <> i.quantity;
    IF inconsistent_scopes <> 0 THEN
        RAISE EXCEPTION 'B4 preflight: % container content/inventory scopes are inconsistent', inconsistent_scopes;
    END IF;
END;
$$;

ROLLBACK;
