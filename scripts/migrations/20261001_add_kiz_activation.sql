-- Explicit registered -> active KIZ activation over the existing KIZ operation ledger.
-- Apply manually only after reviewing 20261001_kiz_activation_preflight.sql.

BEGIN;

LOCK TABLE wms.kiz IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE wms.kiz_events IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE wms.kiz_operations IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE wms.kiz_operations
    DROP CONSTRAINT chk_kiz_operations_type;

ALTER TABLE wms.kiz_operations
    ADD CONSTRAINT chk_kiz_operations_type
        CHECK (operation_type IN ('transfer', 'ship', 'activate'));

CREATE TABLE wms.kiz_activation_authorizations (
    transaction_id bigint NOT NULL,
    operation_id bigint NOT NULL
        REFERENCES wms.kiz_operations(operation_id) ON DELETE RESTRICT,
    kiz_id bigint NOT NULL REFERENCES wms.kiz(kiz_id) ON DELETE RESTRICT,
    product_id varchar NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
    location_id bigint NOT NULL REFERENCES wms.locations(location_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_kiz_activation_authorizations
        PRIMARY KEY (transaction_id, kiz_id),
    CONSTRAINT uq_kiz_activation_authorizations_operation_kiz
        UNIQUE (operation_id, kiz_id)
);

ALTER TABLE wms.kiz_events
    DROP CONSTRAINT chk_kiz_events_event_type,
    DROP CONSTRAINT chk_kiz_events_transition;

ALTER TABLE wms.kiz_events
    ADD CONSTRAINT chk_kiz_events_event_type
        CHECK (
            event_type IN (
                'registered', 'activated', 'assigned',
                'marked_as_error', 'deactivated', 'shipped'
            )
        ),
    ADD CONSTRAINT chk_kiz_events_transition
        CHECK (
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
            AND from_status = 'active'
            AND to_status = 'shipped'
            AND movement_ref IS NOT NULL
        );

CREATE OR REPLACE FUNCTION wms.guard_kiz_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;

    IF ROW(
           NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata
       ) IS NOT DISTINCT FROM ROW(
           OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata
       )
       AND OLD.lifecycle_status = 'registered'
       AND OLD.location_id IS NULL
       AND OLD.container_id IS NULL
       AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status = 'active'
       AND NEW.location_id IS NOT NULL
       AND NEW.container_id IS NULL
       AND NEW.closed_at IS NULL
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_activation_authorizations auth
           JOIN wms.kiz_operations operation
             ON operation.operation_id = auth.operation_id
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
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id, OLD.container_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND NEW.location_id IS DISTINCT FROM OLD.location_id
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_location_update_authorizations auth
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
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status = 'active'
       AND OLD.location_id IS NOT NULL
       AND OLD.container_id IS NULL
       AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status = 'shipped'
       AND NEW.location_id IS NULL
       AND NEW.container_id IS NULL
       AND NEW.closed_at IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_shipment_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND auth.from_location_id = OLD.location_id
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_container_holder_authorizations auth
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
       IS DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id, OLD.location_id, OLD.container_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
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
