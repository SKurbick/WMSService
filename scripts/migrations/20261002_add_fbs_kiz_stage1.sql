-- Canonical FBS task <-> KIZ bindings and per-task physical source identity.
-- Apply manually only after 20261002_fbs_kiz_stage1_preflight.sql is clean.

BEGIN;

CREATE TABLE wms.fbs_kiz_bindings (
    binding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id bigint NOT NULL,
    kiz_code text NOT NULL CHECK (btrim(kiz_code) <> ''),
    kiz_id bigint REFERENCES wms.kiz(kiz_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    product_id varchar NOT NULL REFERENCES public.products(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    first_shipment_id integer REFERENCES wms.fbs_shipments(shipment_id) ON DELETE SET NULL,
    first_item_id integer REFERENCES wms.fbs_shipment_items(item_id) ON DELETE SET NULL,
    state varchar(40) NOT NULL CHECK (
        state IN ('pending_resolution', 'resolved', 'shipped', 'failed')
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_fbs_kiz_bindings_task UNIQUE (task_id),
    CONSTRAINT uq_fbs_kiz_bindings_code UNIQUE (kiz_code)
);

CREATE UNIQUE INDEX uq_fbs_kiz_bindings_kiz
    ON wms.fbs_kiz_bindings(kiz_id)
    WHERE kiz_id IS NOT NULL;

CREATE INDEX idx_fbs_kiz_bindings_product_state
    ON wms.fbs_kiz_bindings(product_id, state);

CREATE OR REPLACE FUNCTION wms.guard_fbs_kiz_binding_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR ROW(NEW.binding_id, NEW.task_id, NEW.kiz_code, NEW.product_id,
              NEW.first_shipment_id, NEW.first_item_id, NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.binding_id, OLD.task_id, OLD.kiz_code, OLD.product_id,
              OLD.first_shipment_id, OLD.first_item_id, OLD.created_at)
       OR OLD.kiz_id IS NOT NULL AND NEW.kiz_id IS DISTINCT FROM OLD.kiz_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'FBS KIZ binding identity is immutable';
    END IF;
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_fbs_kiz_bindings_identity
BEFORE UPDATE OR DELETE ON wms.fbs_kiz_bindings
FOR EACH ROW EXECUTE FUNCTION wms.guard_fbs_kiz_binding_identity();

ALTER TABLE wms.fbs_shipment_task_results
    ADD COLUMN kiz_binding_id bigint,
    ADD COLUMN movement_ref bigint,
    ADD COLUMN source_kind varchar(40),
    ADD COLUMN source_location_id bigint,
    ADD COLUMN source_container_id bigint;

UPDATE wms.fbs_shipment_task_results AS result
SET movement_ref = registry.movement_ref,
    source_kind = 'legacy_fbs',
    source_location_id = movement.from_location_id
FROM wms.movement_registry AS registry
JOIN wms.movements AS movement
  ON movement.movement_id = registry.movement_id
 AND movement.created_at = registry.movement_created_at
WHERE result.outcome = 'written_off'
  AND registry.movement_id = result.movement_id
  AND registry.movement_created_at = result.movement_created_at;

ALTER TABLE wms.fbs_shipment_task_results
    ADD CONSTRAINT fk_fbs_task_result_kiz_binding
        FOREIGN KEY (kiz_binding_id)
        REFERENCES wms.fbs_kiz_bindings(binding_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT fk_fbs_task_result_movement_ref
        FOREIGN KEY (movement_ref)
        REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT fk_fbs_task_result_source_location
        FOREIGN KEY (source_location_id)
        REFERENCES wms.locations(location_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT chk_fbs_task_result_source_kind
        CHECK (
            source_kind IS NULL
            OR source_kind IN ('legacy_fbs', 'fbs_zone', 'active_loose')
        ),
    ADD CONSTRAINT chk_fbs_task_result_stage1_no_container
        CHECK (source_container_id IS NULL),
    ADD CONSTRAINT chk_fbs_task_result_written_off_source
        CHECK (
            outcome <> 'written_off'
            OR movement_ref IS NULL
               AND source_kind IS NULL
               AND source_location_id IS NULL
               AND source_container_id IS NULL
            OR movement_ref IS NOT NULL
               AND source_kind IS NOT NULL
               AND source_location_id IS NOT NULL
               AND source_container_id IS NULL
        );

CREATE INDEX idx_fbs_task_results_movement_ref
    ON wms.fbs_shipment_task_results(movement_ref)
    WHERE movement_ref IS NOT NULL;

CREATE INDEX idx_fbs_task_results_kiz_binding
    ON wms.fbs_shipment_task_results(kiz_binding_id)
    WHERE kiz_binding_id IS NOT NULL;

COMMIT;
