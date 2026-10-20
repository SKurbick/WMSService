-- Extend the controlled KIZ shipment protocol for holder-less registered KIZ.

BEGIN;

LOCK TABLE wms.kiz IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE wms.kiz_events IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE wms.kiz_events
    DROP CONSTRAINT chk_kiz_events_transition;

ALTER TABLE wms.kiz_events
    ADD CONSTRAINT chk_kiz_events_transition CHECK (
        event_type = 'registered'
        AND from_status IS NULL
        AND to_status = 'registered'
        AND location_id IS NULL
        AND container_id IS NULL
        AND movement_ref IS NULL
        OR event_type = 'activated'
        AND from_status = 'registered'
        AND to_status = 'active'
        AND location_id IS NOT NULL
        AND container_id IS NULL
        AND movement_ref IS NULL
        OR event_type = 'assigned'
        AND from_status IS NULL
        AND to_status = 'active'
        AND movement_ref IS NULL
        OR event_type = 'marked_as_error'
        AND from_status = 'active'
        AND to_status = 'error'
        AND movement_ref IS NULL
        AND reason IS NOT NULL
        AND reason ~ '[^[:space:]]'
        OR event_type = 'deactivated'
        AND from_status = 'active'
        AND to_status = 'deactivated'
        AND movement_ref IS NULL
        AND reason IS NOT NULL
        AND reason ~ '[^[:space:]]'
        OR event_type = 'shipped'
        AND from_status IN ('registered', 'active')
        AND to_status = 'shipped'
        AND location_id IS NOT NULL
        AND container_id IS NULL
        AND movement_ref IS NOT NULL
    );

CREATE OR REPLACE FUNCTION wms.ship_kiz(
    p_operation_item_id bigint,
    p_kiz_id bigint,
    p_expected_product_id varchar,
    p_expected_source_location_id bigint,
    p_quantity numeric
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, wms
AS $$
DECLARE
    operation_is_valid boolean;
    current_kiz record;
BEGIN
    IF p_quantity IS NULL OR p_quantity <= 0 OR p_quantity <> trunc(p_quantity) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Ship quantity must be a positive integer';
    END IF;

    SELECT true INTO operation_is_valid
    FROM wms.kiz_operation_items item
    JOIN wms.kiz_operations operation USING (operation_id)
    WHERE item.operation_item_id = p_operation_item_id
      AND operation.operation_type = 'ship'
      AND operation.result_payload IS NULL
      AND item.movement_ref IS NULL;
    IF operation_is_valid IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимый KIZ ship protocol';
    END IF;

    SELECT kiz_id, product_id, location_id, container_id, lifecycle_status, closed_at
    INTO current_kiz
    FROM wms.kiz
    WHERE kiz_id = p_kiz_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_kiz.lifecycle_status NOT IN ('registered', 'active')
       OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id <> p_expected_product_id
       OR current_kiz.container_id IS NOT NULL
       OR current_kiz.lifecycle_status = 'registered' AND current_kiz.location_id IS NOT NULL
       OR current_kiz.lifecycle_status = 'active'
          AND current_kiz.location_id IS DISTINCT FROM p_expected_source_location_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'КИЗ изменился до shipment';
    END IF;

    INSERT INTO wms.kiz_shipment_authorizations(
        transaction_id, operation_item_id, kiz_id, product_id, from_location_id, quantity
    ) VALUES (
        txid_current(), p_operation_item_id, p_kiz_id, p_expected_product_id,
        p_expected_source_location_id, p_quantity
    );

    UPDATE wms.kiz
    SET lifecycle_status = 'shipped',
        location_id = NULL,
        container_id = NULL,
        closed_at = now()
    WHERE kiz_id = p_kiz_id;
END;
$$;

CREATE OR REPLACE FUNCTION wms.require_complete_kiz_shipment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM wms.kiz_operation_items item
        JOIN wms.kiz_operations operation USING (operation_id)
        JOIN wms.movement_registry registry ON registry.movement_ref = item.movement_ref
        JOIN wms.movements movement
          ON movement.movement_id = registry.movement_id
         AND movement.created_at = registry.movement_created_at
        JOIN wms.kiz_movement_links link
          ON link.kiz_id = NEW.kiz_id AND link.movement_ref = item.movement_ref
        JOIN wms.kiz kiz ON kiz.kiz_id = NEW.kiz_id
        JOIN wms.kiz_events event
          ON event.kiz_id = NEW.kiz_id
         AND event.movement_ref = item.movement_ref
         AND event.event_type = 'shipped'
         AND event.from_status IN ('registered', 'active')
         AND event.to_status = 'shipped'
        JOIN wms.locations source_location
          ON source_location.location_id = NEW.from_location_id
        CROSS JOIN LATERAL jsonb_array_elements(operation.result_payload->'items') result_item
        WHERE item.operation_item_id = NEW.operation_item_id
          AND operation.operation_type = 'ship'
          AND operation.result_payload IS NOT NULL
          AND movement.movement_type = 'ship'
          AND movement.source_type = 'kiz_operation'
          AND movement.source_id = operation.operation_id
          AND movement.source_item_id = item.operation_item_id
          AND movement.product_id = NEW.product_id
          AND movement.from_location_id = NEW.from_location_id
          AND movement.to_location_id IS NULL
          AND movement.quantity = NEW.quantity
          AND kiz.product_id = NEW.product_id
          AND kiz.lifecycle_status = 'shipped'
          AND kiz.location_id IS NULL
          AND kiz.container_id IS NULL
          AND kiz.closed_at IS NOT NULL
          AND event.product_id = NEW.product_id
          AND event.location_id = NEW.from_location_id
          AND result_item->>'external_line_id' = item.external_line_id
          AND result_item->>'product_id' = NEW.product_id
          AND result_item->>'from_location_code' = source_location.location_code
          AND (result_item->>'quantity')::numeric = NEW.quantity
          AND (result_item->>'movement_ref')::bigint = item.movement_ref
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Incomplete controlled KIZ shipment';
    END IF;
    DELETE FROM wms.kiz_shipment_authorizations
    WHERE transaction_id = NEW.transaction_id AND kiz_id = NEW.kiz_id;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION wms.guard_kiz_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status IN ('registered', 'active')
       AND OLD.closed_at IS NULL
       AND OLD.container_id IS NULL
       AND (
           OLD.lifecycle_status = 'registered' AND OLD.location_id IS NULL
           OR OLD.lifecycle_status = 'active' AND OLD.location_id IS NOT NULL
       )
       AND NEW.lifecycle_status = 'shipped'
       AND NEW.location_id IS NULL
       AND NEW.container_id IS NULL
       AND NEW.closed_at IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM wms.kiz_shipment_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND (
                 OLD.lifecycle_status = 'registered'
                 OR auth.from_location_id = OLD.location_id
             )
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status = 'registered'
       AND OLD.location_id IS NULL AND OLD.container_id IS NULL AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status = 'active'
       AND NEW.location_id IS NOT NULL AND NEW.container_id IS NULL AND NEW.closed_at IS NULL
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_activation_authorizations auth
           JOIN wms.kiz_operations operation USING (operation_id)
           JOIN wms.kiz_operation_items item
             ON item.operation_id = operation.operation_id
            AND item.external_line_id = OLD.kiz_code
            AND item.movement_ref IS NULL
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND auth.location_id = NEW.location_id
             AND operation.operation_type = 'activate'
             AND operation.source_system = 'api'
             AND operation.result_payload IS NULL
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.container_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id, OLD.container_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND NEW.location_id IS DISTINCT FROM OLD.location_id
       AND EXISTS (
           SELECT 1 FROM wms.kiz_location_update_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND auth.from_location_id = OLD.location_id
             AND auth.to_location_id = NEW.location_id
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND EXISTS (
           SELECT 1 FROM wms.kiz_container_holder_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND (
                 auth.direction = 'fill'
                 AND OLD.location_id = auth.location_id
                 AND OLD.container_id IS NULL
                 AND NEW.location_id IS NULL
                 AND NEW.container_id = auth.container_id
                 OR auth.direction = 'extract'
                 AND OLD.location_id IS NULL
                 AND OLD.container_id = auth.container_id
                 AND NEW.location_id = auth.location_id
                 AND NEW.container_id IS NULL
             )
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.location_id, NEW.container_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.location_id, OLD.container_id, OLD.origin_type, OLD.origin_reference,
           OLD.assigned_at, OLD.created_at, OLD.created_by, OLD.metadata)
       OR OLD.lifecycle_status <> 'active'
       OR NEW.lifecycle_status NOT IN ('error', 'deactivated') THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимое изменение КИЗ';
    END IF;

    NEW.closed_at = now();
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

COMMIT;
