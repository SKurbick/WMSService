-- KIZ Stage 2A Phase 3: idempotent operation infrastructure only.
-- No movement, inventory, KIZ state or KIZ movement link is written by this migration.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE wms.kiz_operations (
    operation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_type varchar(32) NOT NULL,
    source_system varchar(100) NOT NULL,
    external_operation_id varchar(200) NOT NULL,
    request_fingerprint varchar(64) NOT NULL,
    author varchar(100) NOT NULL,
    result_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_kiz_operations_type
        CHECK (operation_type IN ('transfer', 'ship')),
    CONSTRAINT chk_kiz_operations_source
        CHECK (btrim(source_system) <> ''),
    CONSTRAINT chk_kiz_operations_external_id
        CHECK (btrim(external_operation_id) <> ''),
    CONSTRAINT chk_kiz_operations_fingerprint
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_kiz_operations_author
        CHECK (btrim(author) <> ''),
    CONSTRAINT chk_kiz_operations_result
        CHECK (result_payload IS NULL OR jsonb_typeof(result_payload) = 'object'),
    CONSTRAINT uq_kiz_operations_external_identity
        UNIQUE (source_system, operation_type, external_operation_id)
);

CREATE TABLE wms.kiz_operation_items (
    operation_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id bigint NOT NULL,
    external_line_id varchar(200) NOT NULL,
    movement_ref bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_kiz_operation_items_external_line
        CHECK (btrim(external_line_id) <> ''),
    CONSTRAINT fk_kiz_operation_items_operation
        FOREIGN KEY (operation_id) REFERENCES wms.kiz_operations(operation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_kiz_operation_items_movement
        FOREIGN KEY (movement_ref) REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT uq_kiz_operation_items_external_line
        UNIQUE (operation_id, external_line_id),
    CONSTRAINT uq_kiz_operation_items_movement
        UNIQUE (movement_ref)
);

CREATE FUNCTION wms.guard_kiz_operation_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR ROW(NEW.operation_id, NEW.operation_type, NEW.source_system,
              NEW.external_operation_id, NEW.request_fingerprint, NEW.author,
              NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_id, OLD.operation_type, OLD.source_system,
              OLD.external_operation_id, OLD.request_fingerprint, OLD.author,
              OLD.created_at)
       OR OLD.result_payload IS NOT NULL
       OR NEW.result_payload IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'KIZ operation identity/result is immutable';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_kiz_operations_guard
BEFORE UPDATE OR DELETE ON wms.kiz_operations
FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_operation_update();

CREATE FUNCTION wms.require_kiz_operation_result_at_commit() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM wms.kiz_operations
        WHERE operation_id = NEW.operation_id
          AND result_payload IS NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'KIZ operation must store its result before commit';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_kiz_operations_result_at_commit
AFTER INSERT OR UPDATE OF result_payload ON wms.kiz_operations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_kiz_operation_result_at_commit();

CREATE FUNCTION wms.guard_kiz_operation_item_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR ROW(NEW.operation_item_id, NEW.operation_id, NEW.external_line_id,
              NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_item_id, OLD.operation_id, OLD.external_line_id,
              OLD.created_at)
       OR OLD.movement_ref IS NOT NULL
       OR NEW.movement_ref IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'KIZ operation item identity/movement link is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_kiz_operation_items_guard
BEFORE UPDATE OR DELETE ON wms.kiz_operation_items
FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_operation_item_update();

COMMENT ON TABLE wms.kiz_operations IS
'Idempotency boundary for future KIZ transfer/ship. A NULL result exists only inside the owner transaction.';
COMMENT ON COLUMN wms.kiz_operations.result_payload IS
'Stored response snapshot for exact replay; required before transaction commit and never an inventory source of truth.';
COMMENT ON TABLE wms.kiz_operation_items IS
'Stable external operation lines with a future one-time movement_ref attachment.';
COMMENT ON CONSTRAINT uq_kiz_operation_items_movement ON wms.kiz_operation_items IS
'A physical movement can belong to at most one KIZ operation line.';

COMMIT;
