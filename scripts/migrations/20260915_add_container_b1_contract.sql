-- Stage 3B Phase B1: stable identity and flat-container structural contract.
-- Does not create movements or change inventory/container quantities.
BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE wms.containers, wms.container_contents IN SHARE ROW EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wms.containers WHERE location_id IS NULL) THEN
        RAISE EXCEPTION 'Container B1 requires non-NULL direct locations';
    END IF;
    IF EXISTS (SELECT 1 FROM wms.containers WHERE parent_container_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Container B1 supports flat containers only';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.container_contents
        GROUP BY container_id, product_id, batch_number, status
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Container B1 duplicate content scopes must be resolved first';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.containers
        WHERE status IS NULL
           OR status NOT IN ('empty', 'open', 'opened', 'sealed', 'blocked')
           OR container_type NOT IN ('pallet', 'box', 'cage', 'trolley')
           OR qr_code = ''
           OR qr_code <> btrim(qr_code, E' \t\n\r\f\v')
    ) THEN
        RAISE EXCEPTION 'Container B1 has incompatible identity/status/type rows';
    END IF;
    IF EXISTS (SELECT 1 FROM wms.container_contents WHERE status IS NULL) THEN
        RAISE EXCEPTION 'Container B1 requires non-NULL content status';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM wms.containers c
        WHERE c.status = 'empty'
          AND EXISTS (
              SELECT 1 FROM wms.container_contents cc
              WHERE cc.container_id = c.container_id AND cc.status = 'active'
          )
    ) THEN
        RAISE EXCEPTION 'Container B1 empty container has active contents';
    END IF;
END;
$$;

-- `opened` is the legacy spelling of the same state. Runtime preflight confirmed
-- that production currently has no container rows, but the mapping is safe for stage.
UPDATE wms.containers SET status = 'open' WHERE status = 'opened';

ALTER TABLE wms.containers
    DROP CONSTRAINT chk_container_status,
    DROP CONSTRAINT chk_container_type,
    ALTER COLUMN location_id SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ADD CONSTRAINT chk_container_status
        CHECK (status IN ('empty', 'open', 'sealed', 'blocked')),
    ADD CONSTRAINT chk_container_type
        CHECK (container_type IN ('pallet', 'box', 'cage', 'trolley')),
    ADD CONSTRAINT chk_container_flat
        CHECK (parent_container_id IS NULL),
    ADD CONSTRAINT chk_container_qr_identity
        CHECK (qr_code <> '' AND qr_code = btrim(qr_code, E' \t\n\r\f\v'));

ALTER TABLE wms.container_contents
    DROP CONSTRAINT uq_container_content,
    ALTER COLUMN status SET NOT NULL,
    ADD CONSTRAINT uq_container_content
        UNIQUE NULLS NOT DISTINCT (container_id, product_id, batch_number, status);

CREATE FUNCTION wms.guard_container_identity() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Hard delete container identity is prohibited';
    END IF;
    IF NEW.container_id IS DISTINCT FROM OLD.container_id
       OR NEW.qr_code IS DISTINCT FROM OLD.qr_code THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Container identity fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_container_identity_immutable
BEFORE UPDATE OF container_id, qr_code OR DELETE ON wms.containers
FOR EACH ROW EXECUTE FUNCTION wms.guard_container_identity();

CREATE FUNCTION wms.guard_container_empty_state() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF NEW.status = 'empty' AND EXISTS (
        SELECT 1 FROM wms.container_contents cc
        WHERE cc.container_id = NEW.container_id AND cc.status = 'active'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Empty container cannot have active contents';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_container_empty_state
BEFORE UPDATE OF status ON wms.containers
FOR EACH ROW EXECUTE FUNCTION wms.guard_container_empty_state();

CREATE FUNCTION wms.guard_active_content_container_state() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
DECLARE
    container_status varchar;
BEGIN
    IF NEW.status <> 'active' THEN
        RETURN NEW;
    END IF;
    SELECT c.status INTO container_status
    FROM wms.containers c
    WHERE c.container_id = NEW.container_id
    FOR UPDATE;
    IF container_status = 'empty' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Active contents cannot be added to an empty container';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_active_content_container_state
BEFORE INSERT OR UPDATE OF container_id, status ON wms.container_contents
FOR EACH ROW EXECUTE FUNCTION wms.guard_active_content_container_state();

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
    v_item jsonb;
    v_count integer := 0;
BEGIN
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
    VALUES (
        p_qr_code,
        p_container_type,
        v_location_id,
        CASE WHEN jsonb_array_length(p_contents) = 0 THEN 'empty' ELSE 'sealed' END
    ) RETURNING wms.containers.container_id INTO v_container_id;

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_contents)
    LOOP
        INSERT INTO wms.container_contents(
            container_id, product_id, quantity, batch_number, is_scanned
        ) VALUES (
            v_container_id,
            v_item->>'product_id',
            (v_item->>'quantity')::numeric,
            v_item->>'batch_number',
            COALESCE((v_item->>'is_scanned')::boolean, false)
        );
        v_count := v_count + 1;
    END LOOP;
    RETURN QUERY SELECT v_container_id, p_qr_code, v_count;
END;
$$;

CREATE OR REPLACE FUNCTION wms.unpack_from_container(
    p_qr_code varchar,
    p_product_id varchar,
    p_quantity numeric
) RETURNS TABLE(success boolean, remaining_in_container numeric, loose_quantity numeric)
LANGUAGE plpgsql
AS $$
DECLARE
    v_container_id bigint;
    v_location_id bigint;
    v_current_qty numeric;
    v_batch_number varchar(50);
BEGIN
    SELECT container_id, location_id INTO v_container_id, v_location_id
    FROM wms.containers WHERE qr_code = p_qr_code;
    IF v_container_id IS NULL THEN
        RAISE EXCEPTION 'Container % not found', p_qr_code;
    END IF;
    SELECT quantity, batch_number INTO v_current_qty, v_batch_number
    FROM wms.container_contents
    WHERE container_id = v_container_id
      AND product_id = p_product_id AND status = 'active';
    IF v_current_qty IS NULL OR v_current_qty < p_quantity THEN
        RAISE EXCEPTION 'Not enough quantity in container. Available: %, requested: %',
            COALESCE(v_current_qty, 0), p_quantity;
    END IF;

    UPDATE wms.container_contents
    SET quantity = v_current_qty - p_quantity, updated_at = now()
    WHERE container_id = v_container_id
      AND product_id = p_product_id AND status = 'active';
    UPDATE wms.container_contents SET status = 'removed'
    WHERE container_id = v_container_id
      AND product_id = p_product_id AND quantity = 0 AND status = 'active';

    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'unpack',p_product_id,v_location_id,NULL,p_quantity,
        v_batch_number,p_qr_code,'Unpacked from container'
    );
    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'unpack',p_product_id,NULL,v_location_id,p_quantity,
        v_batch_number,NULL,'Unpacked from ' || p_qr_code
    );
    UPDATE wms.containers SET status = 'open'
    WHERE container_id = v_container_id AND status = 'sealed';
    RETURN QUERY SELECT true, COALESCE(v_current_qty - p_quantity, 0), p_quantity;
END;
$$;

COMMENT ON COLUMN wms.containers.container_id IS
'Stable internal container identity for domain links.';
COMMENT ON COLUMN wms.containers.qr_code IS
'Immutable external container identity. Reuse for another container identity is prohibited.';
COMMENT ON COLUMN wms.inventory.container_code IS
'Legacy business reference to container QR; future domain links must use container_id.';
COMMENT ON CONSTRAINT chk_container_flat ON wms.containers IS
'Stage 3B supports direct-location flat containers only; nesting is deferred.';
COMMENT ON CONSTRAINT uq_container_content ON wms.container_contents IS
'At most one content row for an exact container/product/batch/status scope, including NULL batch.';

COMMIT;
