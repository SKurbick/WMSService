BEGIN;

CREATE OR REPLACE FUNCTION wms.guard_controlled_container_movement() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.container_code IS NOT NULL
       AND (
           NEW.source_type IS DISTINCT FROM 'container_operation'
           OR NEW.source_id IS NULL
           OR NEW.source_item_id IS NULL
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Container inventory movement requires controlled container_operation provenance';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_controlled_container_movement ON wms.movements;
CREATE TRIGGER trg_guard_controlled_container_movement
BEFORE INSERT ON wms.movements
FOR EACH ROW EXECUTE FUNCTION wms.guard_controlled_container_movement();

DROP TRIGGER IF EXISTS trg_move_container_inventory ON wms.containers;
DROP FUNCTION wms.move_container_inventory();
DROP FUNCTION wms.unpack_from_container(varchar, varchar, numeric);

CREATE OR REPLACE FUNCTION wms.register_container(
    p_qr_code varchar,
    p_container_type varchar,
    p_location_code varchar,
    p_contents jsonb
) RETURNS TABLE(container_id bigint, qr_code varchar, items_registered integer)
LANGUAGE plpgsql
AS $$
DECLARE
    v_container_id bigint;
    v_location_id bigint;
BEGIN
    IF p_contents IS NULL
       OR jsonb_typeof(p_contents) <> 'array'
       OR jsonb_array_length(p_contents) <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Containers must be registered empty; use container fill operation';
    END IF;
    IF EXISTS (SELECT 1 FROM wms.containers c WHERE c.qr_code = p_qr_code) THEN
        RAISE EXCEPTION 'Container with QR code % already exists. Cannot register twice.',
            p_qr_code;
    END IF;
    SELECT l.location_id INTO v_location_id
    FROM wms.locations l WHERE l.location_code = p_location_code;
    IF v_location_id IS NULL THEN
        RAISE EXCEPTION 'Location % not found', p_location_code;
    END IF;

    INSERT INTO wms.containers(qr_code, container_type, location_id, status)
    VALUES (p_qr_code, p_container_type, v_location_id, 'empty')
    RETURNING wms.containers.container_id INTO v_container_id;

    RETURN QUERY SELECT v_container_id, p_qr_code, 0;
END;
$$;

COMMENT ON FUNCTION wms.register_container(varchar, varchar, varchar, jsonb) IS
'Creates only an empty container. Physical contents must enter through controlled fill.';
COMMENT ON FUNCTION wms.guard_controlled_container_movement() IS
'Rejects container-coded movements without complete container_operation provenance.';

COMMIT;
