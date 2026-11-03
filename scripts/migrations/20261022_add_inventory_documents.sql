-- Inventory stage 1: document/counting only. Apply manually after stage verification.
-- No existing table, function or trigger is modified. No physical writes/backfill.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE wms.inventory_documents (
    document_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title varchar(200) NOT NULL CHECK (btrim(title) <> ''),
    mode varchar(10) NOT NULL CHECK (mode IN ('full','partial')),
    warehouse_location_id bigint NOT NULL REFERENCES wms.locations(location_id),
    status varchar(32) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','counting','pending_approval','closed_without_adjustment')),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    current_revision integer,
    submitted_revision integer,
    created_by varchar(100) NOT NULL CHECK (btrim(created_by) <> ''),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    closed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status='draft' AND started_at IS NULL AND current_revision IS NULL)
        OR status='closed_without_adjustment'
        OR (started_at IS NOT NULL AND current_revision IS NOT NULL)),
    CHECK ((status='closed_without_adjustment') = (closed_at IS NOT NULL)),
    CHECK (status <> 'pending_approval' OR submitted_revision IS NOT NULL)
);
CREATE INDEX idx_inventory_documents_list ON wms.inventory_documents(status,document_id DESC);

CREATE TABLE wms.inventory_document_filters (
    document_id bigint PRIMARY KEY REFERENCES wms.inventory_documents(document_id),
    location_selections jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(location_selections)='array'),
    product_ids varchar(50)[] NOT NULL DEFAULT '{}'
);
CREATE TABLE wms.inventory_document_locations (
    document_id bigint NOT NULL REFERENCES wms.inventory_documents(document_id),
    location_id bigint NOT NULL REFERENCES wms.locations(location_id),
    location_code varchar(100) NOT NULL,
    path text NOT NULL,
    was_active boolean NOT NULL,
    PRIMARY KEY(document_id,location_id)
);
CREATE TABLE wms.inventory_location_claims (
    location_id bigint PRIMARY KEY REFERENCES wms.locations(location_id),
    document_id bigint NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(document_id,location_id)
        REFERENCES wms.inventory_document_locations(document_id,location_id)
);
CREATE INDEX idx_inventory_claims_document ON wms.inventory_location_claims(document_id);

CREATE TABLE wms.inventory_document_revisions (
    document_id bigint NOT NULL REFERENCES wms.inventory_documents(document_id),
    revision_no integer NOT NULL CHECK (revision_no > 0),
    kind varchar(20) NOT NULL CHECK (kind IN ('start','reconciliation')),
    observed_at timestamptz NOT NULL,
    author varchar(100) NOT NULL,
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence)='object'),
    preliminary_only boolean NOT NULL DEFAULT true CHECK (preliminary_only),
    PRIMARY KEY(document_id,revision_no)
);
ALTER TABLE wms.inventory_documents
    ADD FOREIGN KEY(document_id,current_revision)
        REFERENCES wms.inventory_document_revisions(document_id,revision_no),
    ADD FOREIGN KEY(document_id,submitted_revision)
        REFERENCES wms.inventory_document_revisions(document_id,revision_no);

-- inventory_id is evidence, NOT a FK: ordinary movements may delete physical rows.
CREATE TABLE wms.inventory_document_stock_components (
    document_id bigint NOT NULL,
    inventory_id bigint NOT NULL,
    product_id varchar(50) NOT NULL REFERENCES public.products(id),
    location_id bigint NOT NULL,
    status varchar(50) NOT NULL CHECK (status='available'),
    batch_number varchar(50),
    container_code varchar(100) CHECK (container_code IS NULL),
    physical_quantity numeric(10,2) NOT NULL CHECK (physical_quantity >= 0),
    active_kiz_quantity bigint NOT NULL CHECK (active_kiz_quantity >= 0),
    unidentified_quantity numeric(12,2) NOT NULL,
    source_updated_at timestamptz,
    PRIMARY KEY(document_id,inventory_id),
    FOREIGN KEY(document_id,location_id)
        REFERENCES wms.inventory_document_locations(document_id,location_id)
);
CREATE INDEX idx_inventory_components_pair
    ON wms.inventory_document_stock_components(document_id,product_id,location_id);
CREATE TABLE wms.inventory_document_kiz_snapshot (
    document_id bigint NOT NULL REFERENCES wms.inventory_documents(document_id),
    -- Historical evidence, not a live FK: do not key-lock KIZ writers during capture.
    -- The existing KIZ lifecycle contract already forbids hard deletion.
    kiz_id bigint NOT NULL,
    product_id varchar(50) NOT NULL REFERENCES public.products(id),
    location_id bigint REFERENCES wms.locations(location_id),
    container_id bigint,
    lifecycle_status varchar(32) NOT NULL,
    PRIMARY KEY(document_id,kiz_id)
);

CREATE TABLE wms.inventory_document_items (
    item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL,
    product_id varchar(50) NOT NULL REFERENCES public.products(id),
    location_id bigint NOT NULL,
    initial_expected_quantity numeric(12,2) NOT NULL CHECK (initial_expected_quantity >= 0),
    discovered boolean NOT NULL DEFAULT false,
    initial_conflicts jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(initial_conflicts)='array'),
    needs_recount boolean NOT NULL DEFAULT false,
    latest_attempt_id bigint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (NOT discovered OR initial_expected_quantity=0),
    UNIQUE(document_id,product_id,location_id),
    UNIQUE(document_id,item_id),
    FOREIGN KEY(document_id,location_id)
        REFERENCES wms.inventory_document_locations(document_id,location_id)
);
CREATE INDEX idx_inventory_items_page ON wms.inventory_document_items(document_id,item_id);
CREATE TABLE wms.inventory_count_attempts (
    attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL,
    item_id bigint NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    revision_no integer NOT NULL,
    author varchar(100) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress','completed')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    actual_quantity numeric(12,2) CHECK (actual_quantity >= 0),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence)='object'),
    conflicts jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conflicts)='array'),
    UNIQUE(item_id,attempt_number),
    UNIQUE(document_id,item_id,attempt_id),
    FOREIGN KEY(document_id,item_id)
        REFERENCES wms.inventory_document_items(document_id,item_id),
    FOREIGN KEY(document_id,revision_no)
        REFERENCES wms.inventory_document_revisions(document_id,revision_no),
    CHECK ((status='completed') = (completed_at IS NOT NULL)),
    CHECK (status <> 'completed' OR actual_quantity IS NOT NULL),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);
CREATE UNIQUE INDEX uq_inventory_open_attempt
    ON wms.inventory_count_attempts(item_id) WHERE status='in_progress';
ALTER TABLE wms.inventory_document_items ADD FOREIGN KEY(document_id,item_id,latest_attempt_id)
    REFERENCES wms.inventory_count_attempts(document_id,item_id,attempt_id);

CREATE TABLE wms.inventory_document_history (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES wms.inventory_documents(document_id),
    document_version bigint NOT NULL,
    action varchar(40) NOT NULL,
    author varchar(100) NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    details jsonb NOT NULL CHECK (jsonb_typeof(details)='object')
);
CREATE INDEX idx_inventory_history_page
    ON wms.inventory_document_history(document_id,event_id);
CREATE TABLE wms.inventory_document_commands (
    idempotency_key uuid PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES wms.inventory_documents(document_id),
    action varchar(40) NOT NULL,
    request_hash varchar(64) NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Only NEW module tables use these guards; existing writers/triggers are untouched.
CREATE FUNCTION wms.guard_inventory_document_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,wms AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Inventory audit/snapshot is immutable';
END $$;
CREATE FUNCTION wms.guard_inventory_document_draft() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,wms AS $$
BEGIN
    IF (SELECT status FROM wms.inventory_documents WHERE document_id=NEW.document_id) <> 'draft' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Inventory scope/snapshot is frozen';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION wms.guard_inventory_document_lifecycle() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,wms AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Inventory documents cannot be deleted';
    END IF;
    IF OLD.status='closed_without_adjustment'
       OR (OLD.status<>'draft' AND ROW(NEW.title,NEW.mode,NEW.warehouse_location_id,NEW.started_at)
           IS DISTINCT FROM ROW(OLD.title,OLD.mode,OLD.warehouse_location_id,OLD.started_at))
       OR ROW(NEW.created_at,NEW.created_by) IS DISTINCT FROM ROW(OLD.created_at,OLD.created_by)
       OR NEW.version<>OLD.version+1
       OR (NEW.status<>OLD.status AND NOT (
           (OLD.status='draft' AND NEW.status IN ('counting','closed_without_adjustment'))
           OR (OLD.status='counting' AND NEW.status IN ('pending_approval','closed_without_adjustment'))
           OR (OLD.status='pending_approval' AND NEW.status IN ('counting','closed_without_adjustment'))
       )) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid inventory document transition';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_inventory_document_lifecycle BEFORE UPDATE OR DELETE ON wms.inventory_documents
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_lifecycle();
CREATE TRIGGER trg_inventory_filters_draft BEFORE INSERT OR UPDATE ON wms.inventory_document_filters
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_draft();
CREATE TRIGGER trg_inventory_filters_delete BEFORE DELETE ON wms.inventory_document_filters
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable();
CREATE TRIGGER trg_inventory_locations_draft BEFORE INSERT ON wms.inventory_document_locations
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_draft();
CREATE TRIGGER trg_inventory_components_draft BEFORE INSERT ON wms.inventory_document_stock_components
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_draft();
CREATE TRIGGER trg_inventory_kiz_snapshot_draft BEFORE INSERT ON wms.inventory_document_kiz_snapshot
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_draft();

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'inventory_document_locations','inventory_document_stock_components',
        'inventory_document_kiz_snapshot','inventory_document_revisions',
        'inventory_document_history','inventory_document_commands'
    ] LOOP
        EXECUTE format('CREATE TRIGGER trg_inventory_immutable BEFORE UPDATE OR DELETE ON wms.%I '
            'FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable()',relation);
    END LOOP;
END $$;
CREATE FUNCTION wms.guard_inventory_item_history() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,wms AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Inventory count history cannot be deleted';
    END IF;
    IF TG_TABLE_NAME='inventory_document_items' THEN
        IF ROW(NEW.document_id,NEW.product_id,NEW.location_id,NEW.initial_expected_quantity,
               NEW.discovered,NEW.initial_conflicts,NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.document_id,OLD.product_id,OLD.location_id,OLD.initial_expected_quantity,
               OLD.discovered,OLD.initial_conflicts,OLD.created_at) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Initial inventory line is immutable';
        END IF;
    ELSE
        IF OLD.status='completed'
           OR ROW(NEW.document_id,NEW.item_id,NEW.attempt_number,NEW.revision_no,NEW.author,NEW.started_at)
              IS DISTINCT FROM
              ROW(OLD.document_id,OLD.item_id,OLD.attempt_number,OLD.revision_no,OLD.author,OLD.started_at)
           OR NEW.evidence->'start' IS DISTINCT FROM OLD.evidence->'start' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Completed attempt/baseline is immutable';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_inventory_item_history BEFORE UPDATE OR DELETE ON wms.inventory_document_items
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_item_history();
CREATE TRIGGER trg_inventory_attempt_history BEFORE UPDATE OR DELETE ON wms.inventory_count_attempts
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_item_history();
COMMENT ON TABLE wms.inventory_documents IS
    'Inventory stage 1: no physical apply. Future adjustment links must reference existing movement_registry.';
COMMIT;
