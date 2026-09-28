CREATE SCHEMA wms;

CREATE TABLE public.products (
    id varchar PRIMARY KEY,
    name text NOT NULL DEFAULT ''
);

CREATE TABLE wms.locations (
    location_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_location_id bigint,
    location_code varchar NOT NULL UNIQUE,
    path text NOT NULL DEFAULT 'test',
    name varchar NOT NULL DEFAULT 'Test',
    zone_type varchar DEFAULT 'storage',
    level integer NOT NULL DEFAULT 1,
    max_weight numeric,
    max_volume numeric,
    is_active boolean NOT NULL DEFAULT true,
    is_pickable boolean NOT NULL DEFAULT true,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
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
    movement_type varchar NOT NULL,
    product_id varchar NOT NULL REFERENCES public.products(id),
    from_location_id bigint REFERENCES wms.locations(location_id),
    to_location_id bigint REFERENCES wms.locations(location_id),
    quantity numeric NOT NULL,
    batch_number varchar,
    container_code varchar,
    user_name varchar,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wms.kiz (
    kiz_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_code text NOT NULL UNIQUE CHECK (
        kiz_code <> '' AND kiz_code !~ '^[[:space:]]|[[:space:]]$'
    ),
    product_id varchar NOT NULL REFERENCES public.products(id),
    location_id bigint REFERENCES wms.locations(location_id),
    lifecycle_status varchar NOT NULL DEFAULT 'active',
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
    CONSTRAINT chk_kiz_lifecycle_status CHECK (
        lifecycle_status IN ('active', 'error', 'deactivated', 'shipped')
    ),
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
    event_type varchar NOT NULL,
    from_status varchar,
    to_status varchar NOT NULL,
    product_id varchar NOT NULL REFERENCES public.products(id),
    location_id bigint REFERENCES wms.locations(location_id),
    author varchar NOT NULL,
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    movement_ref bigint,
    container_id bigint,
    CONSTRAINT chk_kiz_events_event_type CHECK (
        event_type IN ('assigned', 'marked_as_error', 'deactivated', 'shipped')
    ),
    CONSTRAINT chk_kiz_events_from_status CHECK (
        from_status IN ('active', 'error', 'deactivated', 'shipped')
    ),
    CONSTRAINT chk_kiz_events_to_status CHECK (
        to_status IN ('active', 'error', 'deactivated', 'shipped')
    ),
    CONSTRAINT chk_kiz_events_transition CHECK (
        event_type = 'assigned'
        AND from_status IS NULL
        AND to_status = 'active'
        AND movement_ref IS NULL
        OR event_type = 'marked_as_error'
        AND from_status = 'active'
        AND to_status = 'error'
        AND movement_ref IS NULL
        AND reason IS NOT NULL
        OR event_type = 'deactivated'
        AND from_status = 'active'
        AND to_status = 'deactivated'
        AND movement_ref IS NULL
        AND reason IS NOT NULL
        OR event_type = 'shipped'
        AND from_status = 'active'
        AND to_status = 'shipped'
        AND movement_ref IS NOT NULL
    )
);

CREATE TABLE wms.kiz_movement_links (
    kiz_id bigint NOT NULL REFERENCES wms.kiz(kiz_id),
    movement_ref bigint NOT NULL,
    PRIMARY KEY (kiz_id, movement_ref)
);

CREATE FUNCTION wms.guard_kiz_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;
    RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимое изменение КИЗ';
END;
$$;

CREATE TRIGGER trg_kiz_identity_guard
BEFORE UPDATE OR DELETE ON wms.kiz
FOR EACH ROW
EXECUTE FUNCTION wms.guard_kiz_identity();

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

CREATE FUNCTION wms.guard_kiz_inventory()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
DECLARE
    identified bigint;
    remaining numeric := 0;
BEGIN
    IF OLD.status <> 'available'
       OR OLD.batch_number IS NOT NULL
       OR OLD.container_code IS NOT NULL THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.quantity >= OLD.quantity THEN
            RETURN NEW;
        END IF;
        remaining := NEW.quantity;
    END IF;
    SELECT count(*) INTO identified
    FROM wms.kiz
    WHERE product_id = OLD.product_id
      AND location_id = OLD.location_id
      AND container_id IS NULL
      AND lifecycle_status = 'active';
    IF identified > 0 AND remaining < identified THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P7501',
            MESSAGE = 'Недостаточно неидентифицированного остатка';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

CREATE TRIGGER trg_guard_kiz_inventory
BEFORE UPDATE OR DELETE ON wms.inventory
FOR EACH ROW
EXECUTE FUNCTION wms.guard_kiz_inventory();

CREATE FUNCTION wms.update_inventory_from_movement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF NEW.from_location_id IS NOT NULL THEN
        UPDATE wms.inventory
        SET quantity = quantity - NEW.quantity
        WHERE product_id = NEW.product_id
          AND location_id = NEW.from_location_id
          AND status = 'available'
          AND batch_number IS NOT DISTINCT FROM NEW.batch_number
          AND container_code IS NOT DISTINCT FROM NEW.container_code;
        DELETE FROM wms.inventory
        WHERE product_id = NEW.product_id
          AND location_id = NEW.from_location_id
          AND status = 'available'
          AND batch_number IS NOT DISTINCT FROM NEW.batch_number
          AND container_code IS NOT DISTINCT FROM NEW.container_code
          AND quantity <= 0;
    END IF;
    IF NEW.to_location_id IS NOT NULL THEN
        INSERT INTO wms.inventory (
            product_id, location_id, quantity, status, batch_number, container_code
        ) VALUES (
            NEW.product_id, NEW.to_location_id, NEW.quantity,
            'available', NEW.batch_number, NEW.container_code
        )
        ON CONFLICT (product_id, location_id, status, batch_number, container_code)
        DO UPDATE SET quantity = wms.inventory.quantity + EXCLUDED.quantity;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_update_inventory_from_movement
AFTER INSERT ON wms.movements
FOR EACH ROW
EXECUTE FUNCTION wms.update_inventory_from_movement();
