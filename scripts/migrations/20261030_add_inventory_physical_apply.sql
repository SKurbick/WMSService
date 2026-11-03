-- Controlled partial inventory pilot. Manual deployment only, AFTER freeze migration.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';

ALTER TABLE wms.inventory_application_preparations
 DROP CONSTRAINT inventory_application_preparations_can_apply_check,
 DROP CONSTRAINT inventory_application_preparations_blockers_check,
 ADD COLUMN freeze_id bigint REFERENCES wms.inventory_freezes,
 ADD COLUMN freeze_epoch bigint,
 ADD CHECK(jsonb_typeof(blockers)='array'),
 ADD CHECK(NOT can_apply OR (freeze_id IS NOT NULL AND freeze_epoch>0 AND blockers='[]'::jsonb));
ALTER TABLE wms.inventory_application_components
 ADD COLUMN adjustment_quantity numeric(10,2),
 ADD COLUMN target_quantity numeric(10,2) CHECK(target_quantity>=0);

CREATE TABLE wms.inventory_applications (
 application_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 document_id bigint NOT NULL UNIQUE REFERENCES wms.inventory_documents,
 preparation_id bigint NOT NULL UNIQUE,
 approved_revision integer NOT NULL,
 freeze_id bigint NOT NULL REFERENCES wms.inventory_freezes,
 freeze_epoch bigint NOT NULL,
 transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
 status text NOT NULL CHECK(status IN ('applying','applied')),
 applied_by varchar(100) NOT NULL,
 applied_at timestamptz,
 FOREIGN KEY(document_id,approved_revision) REFERENCES wms.inventory_document_revisions(document_id,revision_no),
 FOREIGN KEY(preparation_id,document_id) REFERENCES wms.inventory_application_preparations(preparation_id,document_id),
 CHECK((status='applied')=(applied_at IS NOT NULL))
);
CREATE TABLE wms.inventory_application_movements (
 application_id bigint NOT NULL REFERENCES wms.inventory_applications,
 component_id bigint NOT NULL UNIQUE REFERENCES wms.inventory_application_components,
 movement_ref bigint NOT NULL UNIQUE REFERENCES wms.movement_registry,
 PRIMARY KEY(application_id,component_id)
);
CREATE TRIGGER inventory_application_movements_immutable BEFORE UPDATE OR DELETE ON wms.inventory_application_movements
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable();

CREATE OR REPLACE FUNCTION wms.inventory_application_can_write(p_freeze bigint) RETURNS boolean
 LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM wms.inventory_applications a
   WHERE a.application_id=NULLIF(current_setting('wms.inventory_application_id',true),'')::bigint
     AND a.freeze_id=p_freeze AND a.status='applying' AND a.transaction_id=pg_current_xact_id())
 $$;

CREATE FUNCTION wms.apply_strict_inventory_source_adjustment(m wms.movements) RETURNS void
 LANGUAGE plpgsql AS $$
DECLARE c record; physical record; actual numeric; previous numeric; rows_left bigint;
BEGIN
 SELECT x.*,i.product_id,i.location_id,a.application_id,a.freeze_id,a.freeze_epoch,
        p.can_apply,p.document_id,p.submitted_revision
 INTO c FROM wms.inventory_application_components x
 JOIN wms.inventory_application_preparations p ON p.preparation_id=x.preparation_id
 JOIN wms.inventory_document_items i ON i.item_id=x.item_id AND i.document_id=x.document_id
 JOIN wms.inventory_applications a ON a.preparation_id=p.preparation_id AND a.document_id=p.document_id
 JOIN wms.inventory_freezes f ON f.freeze_id=a.freeze_id
 JOIN wms.inventory_documents d ON d.document_id=a.document_id
 JOIN wms.inventory_count_attempts t ON t.attempt_id=i.latest_attempt_id AND t.document_id=i.document_id
 WHERE x.component_id=m.source_item_id AND a.document_id=m.source_id
   AND a.status='applying' AND a.transaction_id=pg_current_xact_id()
   AND a.application_id=NULLIF(current_setting('wms.inventory_application_id',true),'')::bigint
   AND f.state='ready_to_apply' AND f.epoch=a.freeze_epoch
   AND p.freeze_id=f.freeze_id AND p.freeze_epoch=f.epoch AND p.can_apply
   AND a.approved_revision=p.submitted_revision AND d.current_revision=a.approved_revision
   AND d.submitted_revision=a.approved_revision AND d.status='pending_approval'
   AND t.status='completed' AND NOT i.needs_recount AND t.started_at>=f.confirmed_at
   AND t.evidence->'freeze'=jsonb_build_object('freeze_id',f.freeze_id,'epoch',f.epoch)
   AND x.target_quantity=t.actual_quantity AND x.component_number=1
   AND (SELECT count(*) FROM wms.inventory_application_components other
        WHERE other.preparation_id=x.preparation_id AND other.item_id=x.item_id)=1;
 IF NOT FOUND OR m.movement_type<>'adjust' OR m.batch_number IS NOT NULL OR m.container_code IS NOT NULL
    OR m.product_id IS DISTINCT FROM c.product_id OR m.quantity<=0
    OR m.quantity IS DISTINCT FROM abs(c.adjustment_quantity)
    OR (c.adjustment_quantity<0 AND (m.from_location_id IS DISTINCT FROM c.location_id OR m.to_location_id IS NOT NULL))
    OR (c.adjustment_quantity>0 AND (m.to_location_id IS DISTINCT FROM c.location_id OR m.from_location_id IS NOT NULL))
    OR c.adjustment_quantity IS NULL OR c.adjustment_quantity=0
    OR NOT wms.inventory_application_can_write(c.freeze_id) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Invalid inventory source adjustment provenance';
 END IF;
 PERFORM 1 FROM wms.inventory_location_fences
   WHERE location_id=c.location_id AND freeze_id=c.freeze_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Inventory freeze is not held'; END IF;
 IF EXISTS(SELECT 1 FROM wms.inventory WHERE product_id=c.product_id AND location_id=c.location_id
      AND status='available' AND coalesce(batch_number,'')='' AND coalesce(container_code,'')=''
      AND (batch_number IS NOT NULL OR container_code IS NOT NULL)) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='NULL/empty inventory key ambiguity';
 END IF;
 IF EXISTS(SELECT 1 FROM wms.kiz WHERE product_id=c.product_id AND location_id=c.location_id
      AND container_id IS NULL AND lifecycle_status='active') THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Active KIZ cannot be adjusted by inventory pilot';
 END IF;
 IF EXISTS(SELECT 1 FROM wms.inventory WHERE product_id=c.product_id AND location_id=c.location_id
     AND status='available' AND (batch_number IS NOT NULL OR container_code IS NOT NULL)) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Inventory pilot cannot allocate batch/container components';
 END IF;
 SELECT inventory_id,quantity INTO physical FROM wms.inventory
  WHERE product_id=c.product_id AND location_id=c.location_id AND status='available'
    AND batch_number IS NULL AND container_code IS NULL FOR UPDATE NOWAIT;
 previous=COALESCE(physical.quantity,0);
 IF previous IS DISTINCT FROM (c.stock_evidence->>'physical_quantity')::numeric
    OR physical.inventory_id IS DISTINCT FROM (c.stock_evidence->>'inventory_id')::bigint
    OR c.target_quantity IS DISTINCT FROM previous+c.adjustment_quantity THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Physical component changed since preparation';
 END IF;
 IF c.adjustment_quantity<0 THEN
   IF physical.inventory_id IS NULL OR previous<m.quantity THEN
     RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Missing or insufficient physical stock';
   END IF;
   UPDATE wms.inventory SET quantity=quantity-m.quantity,updated_at=clock_timestamp()
     WHERE inventory_id=physical.inventory_id AND quantity>=m.quantity RETURNING quantity INTO actual;
   IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Inventory outgoing adjustment had no effect'; END IF;
   IF actual=0 THEN DELETE FROM wms.inventory WHERE inventory_id=physical.inventory_id; END IF;
 ELSE
   INSERT INTO wms.inventory(product_id,location_id,status,batch_number,container_code,quantity)
    VALUES(c.product_id,c.location_id,'available',NULL,NULL,m.quantity)
   ON CONFLICT(product_id,location_id,status,batch_number,container_code)
    DO UPDATE SET quantity=wms.inventory.quantity+excluded.quantity,updated_at=clock_timestamp()
   RETURNING quantity INTO actual;
 END IF;
 IF actual IS DISTINCT FROM c.target_quantity THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Unexpected inventory projection effect';
 END IF;
 SELECT count(*),COALESCE(sum(quantity),0) INTO rows_left,actual FROM wms.inventory
  WHERE product_id=c.product_id AND location_id=c.location_id AND status='available'
    AND batch_number IS NULL AND container_code IS NULL;
 IF actual IS DISTINCT FROM c.target_quantity OR rows_left<>(CASE WHEN c.target_quantity=0 THEN 0 ELSE 1 END) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Final inventory projection effect differs from target';
 END IF;
END $$;

-- Recheck the expanded invoker dependency contract before changing projection.
SELECT wms.inventory_runtime_privileges('apply');

-- Supported contract: runtime 2026-10-19 / archived 2026-08-08 body, or its
-- explicitly reviewed comment-free form. Only outer whitespace may differ.
-- No parser/BEGIN search: place the verified legacy block INSIDE a fixed wrapper.
SET LOCAL check_function_bodies=on;
DO $install$ DECLARE
 original text; definition text; wrapped text; installed text; metadata record;
BEGIN
 SELECT p.*, l.lanname INTO metadata FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
 WHERE p.oid='wms.update_inventory_from_movement()'::regprocedure;
 original=metadata.prosrc;
 IF metadata.lanname<>'plpgsql' OR metadata.prorettype<>'trigger'::regtype
    OR metadata.pronargs<>0 OR metadata.prokind<>'f' OR metadata.prosecdef
    OR metadata.proleakproof OR metadata.provolatile<>'v' OR metadata.proparallel<>'u'
    OR metadata.proconfig IS NOT NULL
    OR md5(btrim(original,E' \t\r\n')) NOT IN ('59757ad997db9dd81274bd13dadc9e4f','e20d6d1d99ebfea866825b07f0644009') THEN
   RAISE EXCEPTION USING ERRCODE='P7604',
     MESSAGE='Unsupported installed inventory projection function contract',
     DETAIL='Observed body md5='||md5(original)||'; trimmed md5='||md5(btrim(original,E' \t\r\n')),
     HINT='Run 20261102_inventory_projection_preflight.sql; review runtime drift. Do not restore archived DDL blindly.';
 END IF;
 definition=pg_get_functiondef(metadata.oid);
 -- Replace only an unambiguous complete body, preserving owner/ACL/attributes.
 IF length(definition)-length(replace(definition,original,''))<>length(original) THEN
   RAISE EXCEPTION USING ERRCODE='P7604',MESSAGE='Ambiguous projection function definition';
 END IF;
 wrapped=E'BEGIN\n    IF NEW.source_type = ''inventory_document'' THEN\n'
      ||E'        PERFORM wms.apply_strict_inventory_source_adjustment(NEW);\n        RETURN NEW;\n    END IF;\n'
      ||original||E'\nEND;\n';
 EXECUTE replace(definition,original,wrapped);
 SELECT prosrc INTO installed FROM pg_proc WHERE oid=metadata.oid;
 IF installed IS DISTINCT FROM wrapped THEN
   RAISE EXCEPTION USING ERRCODE='P7604',MESSAGE='Inventory projection wrapper verification failed';
 END IF;
 -- Exact fixed executable outer block + verified nested legacy block. Mere
 -- occurrence of a helper name (possibly in a comment) is never accepted.
END $install$;

ALTER TABLE wms.inventory_documents DROP CONSTRAINT inventory_documents_status_check;
ALTER TABLE wms.inventory_documents ADD CHECK(status IN ('draft','counting','pending_approval','closed_without_adjustment','applied'));
ALTER TABLE wms.inventory_documents DROP CONSTRAINT inventory_documents_check1;
ALTER TABLE wms.inventory_documents ADD CHECK((status IN ('closed_without_adjustment','applied'))=(closed_at IS NOT NULL));
CREATE OR REPLACE FUNCTION wms.guard_inventory_document_lifecycle() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,wms AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Inventory documents cannot be deleted'; END IF;
 IF OLD.status IN ('closed_without_adjustment','applied')
 OR (OLD.status<>'draft' AND ROW(NEW.title,NEW.mode,NEW.warehouse_location_id,NEW.started_at)
     IS DISTINCT FROM ROW(OLD.title,OLD.mode,OLD.warehouse_location_id,OLD.started_at))
 OR ROW(NEW.created_at,NEW.created_by) IS DISTINCT FROM ROW(OLD.created_at,OLD.created_by)
 OR NEW.version<>OLD.version+1
 OR (NEW.status<>OLD.status AND NOT (
    (OLD.status='draft' AND NEW.status IN ('counting','closed_without_adjustment')) OR
    (OLD.status='counting' AND NEW.status IN ('pending_approval','closed_without_adjustment')) OR
    (OLD.status='pending_approval' AND NEW.status IN ('counting','closed_without_adjustment','applied')))) THEN
   RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid inventory document transition';
 END IF;
 IF NEW.status='applied' AND NOT EXISTS(SELECT 1 FROM wms.inventory_applications a
    WHERE a.document_id=NEW.document_id AND a.status='applied' AND a.transaction_id=pg_current_xact_id()
      AND a.approved_revision=NEW.submitted_revision AND NEW.current_revision=NEW.submitted_revision) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Applied document requires an atomic application';
 END IF;
 IF NEW.status='closed_without_adjustment' AND EXISTS(SELECT 1 FROM wms.inventory_location_fences f
   JOIN wms.inventory_freezes w USING(freeze_id) WHERE w.document_id=NEW.document_id) THEN
   RAISE EXCEPTION USING ERRCODE='P7601',MESSAGE='Explicit freeze release required';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION wms.guard_inventory_application_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status='applied' OR NEW.status<>'applied'
   OR ROW(NEW.application_id,NEW.document_id,NEW.preparation_id,NEW.approved_revision,NEW.freeze_id,NEW.freeze_epoch,NEW.transaction_id,NEW.applied_by)
      IS DISTINCT FROM ROW(OLD.application_id,OLD.document_id,OLD.preparation_id,OLD.approved_revision,OLD.freeze_id,OLD.freeze_epoch,OLD.transaction_id,OLD.applied_by)
 THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Inventory application history is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_application_history BEFORE UPDATE OR DELETE ON wms.inventory_applications
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_application_history();

CREATE FUNCTION wms.guard_inventory_application_start() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status<>'applying' OR NEW.transaction_id<>pg_current_xact_id() OR NOT EXISTS(
  SELECT 1 FROM wms.inventory_application_preparations p
   JOIN wms.inventory_freezes f ON f.freeze_id=p.freeze_id
   JOIN wms.inventory_documents d ON d.document_id=p.document_id
  WHERE p.preparation_id=NEW.preparation_id AND p.document_id=NEW.document_id AND p.can_apply
   AND p.submitted_revision=NEW.approved_revision AND f.freeze_id=NEW.freeze_id
   AND f.epoch=NEW.freeze_epoch AND p.freeze_epoch=f.epoch AND f.state='ready_to_apply'
   AND d.status='pending_approval' AND d.current_revision=NEW.approved_revision
   AND d.submitted_revision=NEW.approved_revision
 ) THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Application requires a ready current preparation'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_application_start BEFORE INSERT ON wms.inventory_applications
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_application_start();

CREATE FUNCTION wms.require_complete_inventory_application() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a wms.inventory_applications%ROWTYPE; expected bigint; written bigint;
BEGIN
 SELECT * INTO a FROM wms.inventory_applications WHERE application_id=NEW.application_id;
 SELECT count(*) INTO expected FROM wms.inventory_application_components
    WHERE preparation_id=a.preparation_id AND adjustment_quantity<>0;
 SELECT count(*) INTO written FROM wms.inventory_application_movements WHERE application_id=a.application_id;
 IF a.status<>'applied' OR expected<>written
  OR NOT EXISTS(SELECT 1 FROM wms.inventory_freezes WHERE freeze_id=a.freeze_id AND state='released' AND epoch=a.freeze_epoch)
  OR NOT EXISTS(SELECT 1 FROM wms.inventory_documents WHERE document_id=a.document_id AND status='applied')
  OR EXISTS(SELECT 1 FROM wms.inventory_location_fences WHERE freeze_id=a.freeze_id)
  OR EXISTS(SELECT 1 FROM wms.inventory_location_claims WHERE document_id=a.document_id)
  OR EXISTS(SELECT 1 FROM wms.inventory_application_movements l
    JOIN wms.movement_registry r USING(movement_ref)
    JOIN wms.movements m ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
    JOIN wms.inventory_application_components c ON c.component_id=l.component_id
    WHERE l.application_id=a.application_id AND (c.preparation_id<>a.preparation_id OR m.source_type IS DISTINCT FROM 'inventory_document'
      OR m.source_id IS DISTINCT FROM a.document_id OR m.source_item_id IS DISTINCT FROM c.component_id
      OR m.quantity IS DISTINCT FROM abs(c.adjustment_quantity))) THEN
    RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Incomplete inventory application';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER complete_inventory_application AFTER INSERT OR UPDATE ON wms.inventory_applications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_inventory_application();

CREATE FUNCTION wms.require_inventory_adjustment_link() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.source_type='inventory_document' AND NOT EXISTS(
   SELECT 1 FROM wms.inventory_application_movements l JOIN wms.movement_registry r USING(movement_ref)
   JOIN wms.inventory_applications a USING(application_id)
   WHERE r.movement_id=NEW.movement_id AND r.movement_created_at=NEW.created_at
    AND a.document_id=NEW.source_id AND l.component_id=NEW.source_item_id AND a.status='applied'
 ) THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Inventory movement lacks application link'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER inventory_adjustment_link AFTER INSERT ON wms.movements
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.source_type='inventory_document')
 EXECUTE FUNCTION wms.require_inventory_adjustment_link();
COMMIT;
