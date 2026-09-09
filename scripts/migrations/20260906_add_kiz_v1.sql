-- Apply manually, before deploying KIZ v1. PostgreSQL >= 15.
-- No backfill. No automatic/down deletion of identity or audit data.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE wms.kiz (
    kiz_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_code text NOT NULL CONSTRAINT uq_kiz_code UNIQUE,
    product_id varchar(50) NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
    location_id bigint NOT NULL REFERENCES wms.locations(location_id) ON DELETE RESTRICT,
    lifecycle_status varchar NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'error', 'deactivated')),
    origin_type varchar NOT NULL DEFAULT 'warehouse_assignment'
        CHECK (origin_type = 'warehouse_assignment'),
    origin_reference text,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by varchar(100) NOT NULL CHECK (created_by ~ '[^[:space:]]'),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    CHECK (kiz_code <> '' AND kiz_code !~ '^[[:space:]]|[[:space:]]$'),
    CHECK ((lifecycle_status = 'active' AND closed_at IS NULL)
        OR (lifecycle_status IN ('error', 'deactivated') AND closed_at IS NOT NULL))
);

CREATE INDEX idx_kiz_active_scope ON wms.kiz(product_id, location_id)
    WHERE lifecycle_status = 'active';
CREATE INDEX idx_kiz_product_status_id ON wms.kiz(product_id, lifecycle_status, kiz_id);
CREATE INDEX idx_kiz_location_status_id ON wms.kiz(location_id, lifecycle_status, kiz_id);

CREATE TABLE wms.kiz_events (
    kiz_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_id bigint NOT NULL REFERENCES wms.kiz(kiz_id) ON DELETE RESTRICT,
    event_type varchar NOT NULL CHECK (event_type IN ('assigned', 'marked_as_error', 'deactivated')),
    from_status varchar CHECK (from_status IN ('active', 'error', 'deactivated')),
    to_status varchar NOT NULL CHECK (to_status IN ('active', 'error', 'deactivated')),
    product_id varchar(50) NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
    location_id bigint NOT NULL REFERENCES wms.locations(location_id) ON DELETE RESTRICT,
    author varchar(100) NOT NULL CHECK (author ~ '[^[:space:]]'),
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (event_type = 'assigned' AND from_status IS NULL AND to_status = 'active')
        OR (event_type = 'marked_as_error' AND from_status IS NOT NULL
            AND from_status = 'active' AND to_status = 'error'
            AND reason IS NOT NULL AND reason ~ '[^[:space:]]')
        OR (event_type = 'deactivated' AND from_status IS NOT NULL
            AND from_status = 'active' AND to_status = 'deactivated'
            AND reason IS NOT NULL AND reason ~ '[^[:space:]]')
    )
);
CREATE INDEX idx_kiz_events_history ON wms.kiz_events(kiz_id, occurred_at, kiz_event_id);

CREATE FUNCTION wms.guard_kiz_inventory() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, wms AS $$
DECLARE
    identified bigint;
    remaining numeric;
BEGIN
    IF OLD.status <> 'available' OR OLD.batch_number IS NOT NULL
        OR OLD.container_code IS NOT NULL THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    remaining := 0;
    IF TG_OP = 'UPDATE' THEN
        IF ROW(OLD.product_id, OLD.location_id, OLD.status, OLD.batch_number, OLD.container_code)
           IS NOT DISTINCT FROM
           ROW(NEW.product_id, NEW.location_id, NEW.status, NEW.batch_number, NEW.container_code)
        THEN
            -- Includes assignment's MVCC touch; no count on increases/no-op updates.
            IF NEW.quantity >= OLD.quantity THEN RETURN NEW; END IF;
            remaining := NEW.quantity;
        END IF;
    END IF;

    SELECT count(*) INTO identified FROM wms.kiz
    WHERE product_id = OLD.product_id AND location_id = OLD.location_id
      AND lifecycle_status = 'active';
    -- Preserve the pre-KIZ negative quantity constraint/error when no active KIZ exist.
    IF identified > 0 AND remaining < identified THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501',
            MESSAGE = format('Недостаточно неидентифицированного остатка: physical=%s, identified=%s, requested_remaining=%s',
                             OLD.quantity, identified, remaining),
            DETAIL = jsonb_build_object('product_id', OLD.product_id,
                'location_id', OLD.location_id, 'physical_quantity', OLD.quantity,
                'identified_quantity', identified, 'requested_remaining', remaining)::text;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_kiz_inventory_guard BEFORE UPDATE OR DELETE ON wms.inventory
    FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_inventory();

CREATE FUNCTION wms.guard_kiz_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, wms AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
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
CREATE TRIGGER trg_kiz_identity_guard BEFORE UPDATE OR DELETE ON wms.kiz
    FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_identity();

CREATE FUNCTION wms.guard_kiz_event_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, wms AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Изменение или удаление KIZ audit запрещено';
END;
$$;
CREATE TRIGGER trg_kiz_events_immutable BEFORE UPDATE OR DELETE ON wms.kiz_events
    FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_event_immutable();

COMMENT ON TABLE wms.kiz IS 'KIZ v1: exact available loose scope, no batch/container. Write via transactional KIZ service with inventory lock and MVCC touch.';
COMMENT ON FUNCTION wms.guard_kiz_inventory() IS 'KIZ v1 authoritative inventory decrease/delete/identity guard. Assignment must create a new inventory row version under FOR UPDATE.';
COMMIT;
