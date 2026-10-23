-- After FBS Stage 1, organization and KIZ-prefix migrations. No data corrections here.
BEGIN;
SET LOCAL lock_timeout = '10s';
LOCK TABLE wms.kiz IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE wms.kiz_product_corrections (
    correction_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiz_id bigint NOT NULL UNIQUE REFERENCES wms.kiz(kiz_id),
    old_product_id varchar NOT NULL REFERENCES public.products(id),
    new_product_id varchar NOT NULL REFERENCES public.products(id),
    source_message_id bigint NOT NULL REFERENCES wms.kiz_import_messages(message_id),
    fbs_task_id bigint NOT NULL REFERENCES wms.fbs_kiz_bindings(task_id),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    author text NOT NULL CHECK (btrim(author) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    transaction_id bigint NOT NULL DEFAULT txid_current(),
    CHECK (old_product_id <> new_product_id)
);

CREATE FUNCTION wms.validate_kiz_product_correction()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog, wms AS $$
DECLARE k wms.kiz%ROWTYPE;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION USING ERRCODE='P7501', MESSAGE='KIZ product corrections are immutable';
    END IF;
    -- Product locks before KIZ, compatible with import's lock ordering.
    PERFORM id FROM public.products
    WHERE id IN (NEW.old_product_id, NEW.new_product_id) ORDER BY id FOR SHARE;
    SELECT * INTO k FROM wms.kiz WHERE kiz_id=NEW.kiz_id FOR UPDATE;
    IF NOT FOUND OR k.product_id <> NEW.old_product_id
       OR k.lifecycle_status <> 'registered' OR k.origin_type <> 'receipt_import'
       OR k.location_id IS NOT NULL OR k.container_id IS NOT NULL OR k.closed_at IS NOT NULL
       OR EXISTS (SELECT 1 FROM wms.kiz_movement_links l WHERE l.kiz_id=k.kiz_id)
       OR EXISTS (SELECT 1 FROM wms.kiz_events e
                  WHERE e.kiz_id=k.kiz_id AND e.event_type<>'registered') THEN
        RAISE EXCEPTION USING ERRCODE='P7501', MESSAGE='Only unmoved registered receipt KIZ can be corrected';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM wms.kiz_import_message_kiz l
        JOIN wms.kiz_import_messages m USING(message_id)
        WHERE l.kiz_id=k.kiz_id AND l.message_id=NEW.source_message_id
          AND m.order_guid=k.origin_reference
    ) OR NOT EXISTS (
        SELECT 1 FROM wms.fbs_kiz_bindings b
        WHERE b.task_id=NEW.fbs_task_id AND b.kiz_code=k.kiz_code
          AND b.product_id=NEW.new_product_id AND b.state<>'shipped'
          AND (b.kiz_id IS NULL OR b.kiz_id=k.kiz_id)
    ) THEN
        RAISE EXCEPTION USING ERRCODE='P7501', MESSAGE='Correction requires matching import and FBS evidence';
    END IF;
    NEW.transaction_id=txid_current();
    NEW.created_at=now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_validate_kiz_product_correction
BEFORE INSERT OR UPDATE OR DELETE ON wms.kiz_product_corrections
FOR EACH ROW EXECUTE FUNCTION wms.validate_kiz_product_correction();

CREATE FUNCTION wms.apply_kiz_product_correction()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog, wms AS $$
BEGIN
    UPDATE wms.kiz SET product_id=NEW.new_product_id WHERE kiz_id=NEW.kiz_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_apply_kiz_product_correction
AFTER INSERT ON wms.kiz_product_corrections
FOR EACH ROW EXECUTE FUNCTION wms.apply_kiz_product_correction();

-- Preserve all existing lifecycle/holder guards; add only one narrow authorization.
-- Fail closed if the expected final immutable-identity branch has changed.
DO $migration$
DECLARE
    definition text := pg_get_functiondef('wms.guard_kiz_identity()'::regprocedure);
    marker text := '    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.location_id, NEW.container_id,';
    correction_branch text := $branch$
    IF NEW.product_id IS DISTINCT FROM OLD.product_id
       AND (to_jsonb(NEW) - 'product_id' - 'updated_at') =
           (to_jsonb(OLD) - 'product_id' - 'updated_at')
       AND OLD.lifecycle_status='registered'
       AND OLD.location_id IS NULL AND OLD.container_id IS NULL AND OLD.closed_at IS NULL
       AND EXISTS (
         SELECT 1 FROM wms.kiz_product_corrections c
         WHERE c.kiz_id=OLD.kiz_id AND c.transaction_id=txid_current()
           AND c.old_product_id=OLD.product_id AND c.new_product_id=NEW.product_id
       ) THEN
        NEW.updated_at=now();
        RETURN NEW;
    END IF;

$branch$;
BEGIN
    IF definition IS NULL OR strpos(definition, 'kiz_shipment_authorizations')=0
       OR strpos(definition, 'kiz_product_corrections')>0
       OR (length(definition)-length(replace(definition,marker,'')))<>length(marker) THEN
        RAISE EXCEPTION 'Unexpected KIZ identity guard; migration stopped';
    END IF;
    EXECUTE replace(definition,marker,correction_branch || marker);
END;
$migration$;

COMMENT ON TABLE wms.kiz_product_corrections IS
'Immutable per-KIZ product correction evidence; INSERT validates and updates registered KIZ atomically. Never a global product mapping.';
COMMIT;
