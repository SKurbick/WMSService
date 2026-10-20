-- KIZ Stage 2A Phase 4: narrow, transaction-bound location change protocol.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE wms.kiz_location_update_authorizations (
    transaction_id bigint NOT NULL,
    operation_item_id bigint NOT NULL,
    kiz_id bigint NOT NULL,
    product_id varchar(50) NOT NULL,
    from_location_id bigint NOT NULL,
    to_location_id bigint NOT NULL,
    CONSTRAINT pk_kiz_location_update_authorizations
        PRIMARY KEY (transaction_id, kiz_id),
    CONSTRAINT fk_kiz_location_auth_item
        FOREIGN KEY (operation_item_id)
        REFERENCES wms.kiz_operation_items(operation_item_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_kiz_location_auth_kiz
        FOREIGN KEY (kiz_id) REFERENCES wms.kiz(kiz_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_kiz_location_auth_locations
        CHECK (from_location_id <> to_location_id)
);

REVOKE ALL ON wms.kiz_location_update_authorizations FROM PUBLIC;

CREATE OR REPLACE FUNCTION wms.guard_kiz_identity() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND NEW.location_id IS DISTINCT FROM OLD.location_id
       AND EXISTS (
           SELECT 1 FROM wms.kiz_location_update_authorizations a
           WHERE a.transaction_id = txid_current()
             AND a.kiz_id = OLD.kiz_id
             AND a.product_id = OLD.product_id
             AND a.from_location_id = OLD.location_id
             AND a.to_location_id = NEW.location_id
       ) THEN
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.location_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id, OLD.location_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       OR OLD.lifecycle_status <> 'active'
       OR NEW.lifecycle_status NOT IN ('error', 'deactivated') THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимое изменение КИЗ';
    END IF;
    NEW.closed_at := now();
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE FUNCTION wms.transfer_kiz_location(
    p_operation_item_id bigint,
    p_kiz_id bigint,
    p_expected_product_id varchar,
    p_expected_source_location_id bigint,
    p_destination_location_id bigint
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, wms
AS $$
DECLARE
    operation_is_valid boolean;
    current_kiz record;
BEGIN
    IF p_expected_source_location_id = p_destination_location_id THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source and destination must differ';
    END IF;

    SELECT true INTO operation_is_valid
    FROM wms.kiz_operation_items oi
    JOIN wms.kiz_operations o USING (operation_id)
    WHERE oi.operation_item_id = p_operation_item_id
      AND o.operation_type = 'transfer'
      AND o.result_payload IS NULL
      AND oi.movement_ref IS NULL;
    IF operation_is_valid IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимый KIZ transfer protocol';
    END IF;

    SELECT kiz_id, product_id, location_id, lifecycle_status, closed_at
    INTO current_kiz
    FROM wms.kiz WHERE kiz_id = p_kiz_id FOR UPDATE;
    IF NOT FOUND
       OR current_kiz.lifecycle_status <> 'active'
       OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id <> p_expected_product_id
       OR current_kiz.location_id <> p_expected_source_location_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'КИЗ изменился до transfer';
    END IF;

    INSERT INTO wms.kiz_location_update_authorizations(
        transaction_id, operation_item_id, kiz_id, product_id,
        from_location_id, to_location_id
    ) VALUES (
        txid_current(), p_operation_item_id, p_kiz_id, p_expected_product_id,
        p_expected_source_location_id, p_destination_location_id
    );

    UPDATE wms.kiz SET location_id = p_destination_location_id WHERE kiz_id = p_kiz_id;
END;
$$;

REVOKE ALL ON FUNCTION wms.transfer_kiz_location(bigint,bigint,varchar,bigint,bigint) FROM PUBLIC;

CREATE FUNCTION wms.require_complete_kiz_location_transfer() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM wms.kiz_operation_items oi
        JOIN wms.kiz_operations o USING (operation_id)
        JOIN wms.movement_registry r ON r.movement_ref = oi.movement_ref
        JOIN wms.movements m
          ON m.movement_id = r.movement_id AND m.created_at = r.movement_created_at
        JOIN wms.kiz_movement_links l
          ON l.kiz_id = NEW.kiz_id AND l.movement_ref = oi.movement_ref
        JOIN wms.kiz k ON k.kiz_id = NEW.kiz_id
        WHERE oi.operation_item_id = NEW.operation_item_id
          AND o.operation_type = 'transfer'
          AND o.result_payload IS NOT NULL
          AND m.movement_type = 'transfer'
          AND m.source_type = 'kiz_operation'
          AND m.source_id = o.operation_id
          AND m.source_item_id = oi.operation_item_id
          AND m.product_id = NEW.product_id
          AND m.from_location_id = NEW.from_location_id
          AND m.to_location_id = NEW.to_location_id
          AND k.product_id = NEW.product_id
          AND k.location_id = NEW.to_location_id
          AND k.lifecycle_status = 'active'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'Incomplete controlled KIZ location transfer';
    END IF;
    DELETE FROM wms.kiz_location_update_authorizations
    WHERE transaction_id = NEW.transaction_id AND kiz_id = NEW.kiz_id;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_complete_kiz_location_transfer
AFTER INSERT ON wms.kiz_location_update_authorizations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_location_transfer();

COMMENT ON TABLE wms.kiz_location_update_authorizations IS
'Transaction-local authorization consumed at commit; never a persistent transfer ledger.';
COMMENT ON FUNCTION wms.transfer_kiz_location(bigint,bigint,varchar,bigint,bigint) IS
'Controlled active KIZ location change. Commit requires the matching operation item, movement, registry mapping, KIZ link and result.';

COMMIT;
