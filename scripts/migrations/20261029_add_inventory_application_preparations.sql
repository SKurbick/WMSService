-- Preparatory fallback ONLY: no physical writer or successful application state.
-- Apply manually, after Stage 1. Does not replace existing functions or triggers.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE wms.inventory_application_preparations (
    preparation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL,
    submitted_revision integer NOT NULL,
    observed_document_version bigint NOT NULL CHECK (observed_document_version > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by varchar(100) NOT NULL CHECK (btrim(created_by) <> ''),
    source_type varchar(50) NOT NULL DEFAULT 'inventory_document'
        CHECK (source_type='inventory_document'),
    can_apply boolean NOT NULL DEFAULT false CHECK (NOT can_apply),
    blockers jsonb NOT NULL CHECK (jsonb_typeof(blockers)='array' AND jsonb_array_length(blockers)>0),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence)='object'),
    lines jsonb NOT NULL CHECK (jsonb_typeof(lines)='array'),
    UNIQUE(document_id,submitted_revision),
    UNIQUE(preparation_id,document_id),
    FOREIGN KEY(document_id,submitted_revision)
        REFERENCES wms.inventory_document_revisions(document_id,revision_no)
);

-- Stable technical component identities, without allocating an aggregate shortage
-- to batches. inventory_id inside evidence is historical, never a live physical FK.
-- Future movement_registry links require a separate reviewed writer migration.
CREATE TABLE wms.inventory_application_components (
    component_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preparation_id bigint NOT NULL,
    document_id bigint NOT NULL,
    item_id bigint NOT NULL,
    component_number integer NOT NULL CHECK (component_number > 0),
    stock_evidence jsonb NOT NULL CHECK (jsonb_typeof(stock_evidence)='object'),
    UNIQUE(preparation_id,item_id,component_number),
    FOREIGN KEY(preparation_id,document_id)
        REFERENCES wms.inventory_application_preparations(preparation_id,document_id),
    FOREIGN KEY(document_id,item_id)
        REFERENCES wms.inventory_document_items(document_id,item_id)
);

CREATE FUNCTION wms.guard_inventory_application_preparation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE d wms.inventory_documents%ROWTYPE;
BEGIN
    SELECT * INTO STRICT d FROM wms.inventory_documents
        WHERE document_id=NEW.document_id FOR UPDATE;
    IF d.status<>'pending_approval'
       OR d.submitted_revision IS DISTINCT FROM NEW.submitted_revision
       OR d.current_revision IS DISTINCT FROM NEW.submitted_revision
       OR d.version<>NEW.observed_document_version THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Stale inventory preparation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_inventory_application_preparation_state
    BEFORE INSERT ON wms.inventory_application_preparations
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_application_preparation();
CREATE TRIGGER trg_inventory_application_preparation_immutable
    BEFORE UPDATE OR DELETE ON wms.inventory_application_preparations
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable();
CREATE TRIGGER trg_inventory_application_component_immutable
    BEFORE UPDATE OR DELETE ON wms.inventory_application_components
    FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable();

COMMIT;
