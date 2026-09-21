-- KIZ Stage 2A Phase 5: controlled explicit shipment of active KIZ.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE wms.kiz_shipment_authorizations (
    transaction_id bigint NOT NULL,
    operation_item_id bigint NOT NULL,
    kiz_id bigint NOT NULL,
    product_id varchar(50) NOT NULL,
    from_location_id bigint NOT NULL,
    quantity numeric(10,2) NOT NULL,
    CONSTRAINT pk_kiz_shipment_authorizations PRIMARY KEY (transaction_id, kiz_id),
    CONSTRAINT fk_kiz_shipment_auth_item FOREIGN KEY (operation_item_id)
        REFERENCES wms.kiz_operation_items(operation_item_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_kiz_shipment_auth_kiz FOREIGN KEY (kiz_id)
        REFERENCES wms.kiz(kiz_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_kiz_shipment_auth_quantity
        CHECK (quantity > 0 AND quantity = trunc(quantity))
);

REVOKE ALL ON wms.kiz_shipment_authorizations FROM PUBLIC;

CREATE OR REPLACE FUNCTION wms.guard_kiz_identity() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;

    -- Phase 4: exact active location-only transfer.
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

    -- Phase 5: exact active/source -> shipped/NULL controlled transition.
    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status = 'active'
       AND OLD.location_id IS NOT NULL
       AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status = 'shipped'
       AND NEW.location_id IS NULL
       AND NEW.closed_at IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM wms.kiz_shipment_authorizations a
           WHERE a.transaction_id = txid_current()
             AND a.kiz_id = OLD.kiz_id
             AND a.product_id = OLD.product_id
             AND a.from_location_id = OLD.location_id
       ) THEN
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    -- Existing KIZ v1 terminal transitions.
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

CREATE FUNCTION wms.ship_kiz(
    p_operation_item_id bigint,
    p_kiz_id bigint,
    p_expected_product_id varchar,
    p_expected_source_location_id bigint,
    p_quantity numeric
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, wms
AS $$
DECLARE
    operation_is_valid boolean;
    current_kiz record;
BEGIN
    IF p_quantity IS NULL OR p_quantity <= 0 OR p_quantity <> trunc(p_quantity) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Ship quantity must be a positive integer';
    END IF;

    SELECT true INTO operation_is_valid
    FROM wms.kiz_operation_items oi
    JOIN wms.kiz_operations o USING (operation_id)
    WHERE oi.operation_item_id = p_operation_item_id
      AND o.operation_type = 'ship'
      AND o.result_payload IS NULL
      AND oi.movement_ref IS NULL;
    IF operation_is_valid IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимый KIZ ship protocol';
    END IF;

    SELECT kiz_id, product_id, location_id, lifecycle_status, closed_at
    INTO current_kiz FROM wms.kiz WHERE kiz_id = p_kiz_id FOR UPDATE;
    IF NOT FOUND
       OR current_kiz.lifecycle_status <> 'active'
       OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id <> p_expected_product_id
       OR current_kiz.location_id <> p_expected_source_location_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'КИЗ изменился до shipment';
    END IF;

    INSERT INTO wms.kiz_shipment_authorizations(
        transaction_id, operation_item_id, kiz_id, product_id, from_location_id, quantity
    ) VALUES (
        txid_current(), p_operation_item_id, p_kiz_id, p_expected_product_id,
        p_expected_source_location_id, p_quantity
    );

    UPDATE wms.kiz
    SET lifecycle_status='shipped', location_id=NULL, closed_at=now()
    WHERE kiz_id=p_kiz_id;
END;
$$;

REVOKE ALL ON FUNCTION wms.ship_kiz(bigint,bigint,varchar,bigint,numeric) FROM PUBLIC;

CREATE FUNCTION wms.require_complete_kiz_shipment() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM wms.kiz_operation_items oi
        JOIN wms.kiz_operations o USING (operation_id)
        JOIN wms.movement_registry r ON r.movement_ref=oi.movement_ref
        JOIN wms.movements m
          ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
        JOIN wms.kiz_movement_links l
          ON l.kiz_id=NEW.kiz_id AND l.movement_ref=oi.movement_ref
        JOIN wms.kiz k ON k.kiz_id=NEW.kiz_id
        JOIN wms.kiz_events e
          ON e.kiz_id=NEW.kiz_id AND e.movement_ref=oi.movement_ref
         AND e.event_type='shipped' AND e.from_status='active' AND e.to_status='shipped'
        JOIN wms.locations source_location ON source_location.location_id=NEW.from_location_id
        CROSS JOIN LATERAL jsonb_array_elements(o.result_payload->'items') result_item
        WHERE oi.operation_item_id=NEW.operation_item_id
          AND o.operation_type='ship'
          AND o.result_payload IS NOT NULL
          AND m.movement_type='ship'
          AND m.source_type='kiz_operation'
          AND m.source_id=o.operation_id
          AND m.source_item_id=oi.operation_item_id
          AND m.product_id=NEW.product_id
          AND m.from_location_id=NEW.from_location_id
          AND m.to_location_id IS NULL
          AND m.quantity=NEW.quantity
          AND k.product_id=NEW.product_id
          AND k.lifecycle_status='shipped'
          AND k.location_id IS NULL
          AND k.closed_at IS NOT NULL
          AND e.product_id=NEW.product_id
          AND e.location_id=NEW.from_location_id
          AND result_item->>'external_line_id'=oi.external_line_id
          AND result_item->>'product_id'=NEW.product_id
          AND result_item->>'from_location_code'=source_location.location_code
          AND (result_item->>'quantity')::numeric=NEW.quantity
          AND (result_item->>'movement_ref')::bigint=oi.movement_ref
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Incomplete controlled KIZ shipment';
    END IF;
    DELETE FROM wms.kiz_shipment_authorizations
    WHERE transaction_id=NEW.transaction_id AND kiz_id=NEW.kiz_id;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_complete_kiz_shipment
AFTER INSERT ON wms.kiz_shipment_authorizations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_shipment();

COMMENT ON TABLE wms.kiz_shipment_authorizations IS
'Transaction-local authorization consumed at commit; never a shipment ledger.';
COMMENT ON FUNCTION wms.ship_kiz(bigint,bigint,varchar,bigint,numeric) IS
'Controlled active KIZ shipment. Commit requires matching operation, movement, link, shipped event and result.';

COMMIT;
