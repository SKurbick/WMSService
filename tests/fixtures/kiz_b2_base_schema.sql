CREATE SCHEMA wms;

CREATE TABLE public.products (
    id varchar PRIMARY KEY,
    name text NOT NULL DEFAULT ''
);

CREATE TABLE wms.locations (
    location_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_code varchar NOT NULL UNIQUE,
    is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE wms.receipt_items (
    receipt_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guid varchar NOT NULL,
    product_id varchar NOT NULL REFERENCES public.products(id),
    quantity numeric NOT NULL CHECK (quantity >= 0),
    UNIQUE (guid, product_id)
);

CREATE TABLE wms.inventory (
    inventory_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id varchar NOT NULL REFERENCES public.products(id),
    location_id bigint NOT NULL REFERENCES wms.locations(location_id),
    quantity numeric NOT NULL CHECK (quantity >= 0),
    status varchar NOT NULL DEFAULT 'available',
    batch_number varchar,
    container_code varchar,
    UNIQUE NULLS NOT DISTINCT (
        product_id, location_id, status, batch_number, container_code
    )
);

CREATE TABLE wms.movements (
    movement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id varchar NOT NULL,
    quantity numeric NOT NULL
);

CREATE TABLE wms.kiz (
    kiz_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_code text NOT NULL UNIQUE CHECK (
        kiz_code <> '' AND kiz_code !~ '^[[:space:]]|[[:space:]]$'
    ),
    product_id varchar NOT NULL REFERENCES public.products(id),
    location_id bigint REFERENCES wms.locations(location_id),
    lifecycle_status varchar NOT NULL DEFAULT 'active' CHECK (
        lifecycle_status IN ('active', 'error', 'deactivated', 'shipped')
    ),
    origin_type varchar NOT NULL DEFAULT 'warehouse_assignment',
    origin_reference text,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by varchar NOT NULL CHECK (created_by ~ '[^[:space:]]'),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    container_id bigint,
    CONSTRAINT kiz_origin_type_check CHECK (origin_type = 'warehouse_assignment'),
    CONSTRAINT chk_kiz_lifecycle_holder CHECK (
        lifecycle_status = 'active'
        AND closed_at IS NULL
        AND ((location_id IS NOT NULL)::integer + (container_id IS NOT NULL)::integer) = 1
        OR lifecycle_status IN ('error', 'deactivated')
        AND closed_at IS NOT NULL
        AND NOT (location_id IS NOT NULL AND container_id IS NOT NULL)
        OR lifecycle_status = 'shipped'
        AND closed_at IS NOT NULL
        AND location_id IS NULL
        AND container_id IS NULL
    )
);

CREATE TABLE wms.kiz_events (
    kiz_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_id bigint NOT NULL REFERENCES wms.kiz(kiz_id),
    event_type varchar NOT NULL CHECK (
        event_type IN ('assigned', 'marked_as_error', 'deactivated', 'shipped')
    ),
    from_status varchar,
    to_status varchar NOT NULL,
    product_id varchar NOT NULL REFERENCES public.products(id),
    location_id bigint REFERENCES wms.locations(location_id),
    author varchar NOT NULL,
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    movement_ref bigint,
    container_id bigint
);

CREATE FUNCTION wms.require_kiz_final_holder_integrity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
DECLARE
    physical numeric;
    identified bigint;
BEGIN
    IF NEW.lifecycle_status <> 'active' OR NEW.container_id IS NOT NULL THEN
        RETURN NULL;
    END IF;
    SELECT count(*) INTO identified
    FROM wms.kiz
    WHERE lifecycle_status = 'active'
      AND product_id = NEW.product_id
      AND location_id = NEW.location_id
      AND container_id IS NULL;
    SELECT COALESCE(quantity, 0) INTO physical
    FROM wms.inventory
    WHERE product_id = NEW.product_id
      AND location_id = NEW.location_id
      AND status = 'available'
      AND batch_number IS NULL
      AND container_code IS NULL;
    IF identified > COALESCE(physical, 0) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Final loose KIZ quantity exceeds physical inventory';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_kiz_final_holder_integrity
AFTER INSERT OR UPDATE ON wms.kiz
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION wms.require_kiz_final_holder_integrity();
