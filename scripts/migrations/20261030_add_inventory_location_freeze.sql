-- Address-scoped persistent freeze. No physical adjustment writer in this migration.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';

CREATE TABLE wms.inventory_freezes (
 freeze_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 document_id bigint NOT NULL REFERENCES wms.inventory_documents,
 state text NOT NULL CHECK(state IN ('freezing','frozen','ready_to_apply','invalidated','released')),
 epoch bigint NOT NULL DEFAULT 0 CHECK(epoch>=0),
 created_by varchar(100) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 confirmed_by varchar(100), confirmed_at timestamptz, confirmation text,
 baseline jsonb, released_at timestamptz,
 CHECK ((state='released')=(released_at IS NOT NULL)),
 CHECK (state NOT IN ('frozen','ready_to_apply') OR
        (epoch>0 AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND confirmation IS NOT NULL AND baseline IS NOT NULL))
);
CREATE UNIQUE INDEX inventory_one_active_freeze ON wms.inventory_freezes(document_id)
 WHERE state<>'released';
CREATE INDEX inventory_freeze_document_history ON wms.inventory_freezes(document_id,freeze_id DESC);
CREATE TABLE wms.inventory_location_fences (
 location_id bigint PRIMARY KEY REFERENCES wms.locations ON DELETE CASCADE,
 freeze_id bigint REFERENCES wms.inventory_freezes,
 generation bigint NOT NULL DEFAULT 0
);
INSERT INTO wms.inventory_location_fences(location_id) SELECT location_id FROM wms.locations;
CREATE INDEX inventory_fence_freeze ON wms.inventory_location_fences(freeze_id) WHERE freeze_id IS NOT NULL;
CREATE TABLE wms.inventory_freeze_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 freeze_id bigint NOT NULL REFERENCES wms.inventory_freezes,
 action text NOT NULL, author text NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
 location_id bigint, product_id varchar(50), fbs_shipment_id bigint,
 movement_id bigint, movement_created_at timestamptz,
 details jsonb NOT NULL DEFAULT '{}',
 CHECK((movement_id IS NULL)=(movement_created_at IS NULL)),
 FOREIGN KEY(movement_id,movement_created_at) REFERENCES wms.movements(movement_id,created_at)
   DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX inventory_freeze_event_page ON wms.inventory_freeze_events(freeze_id,event_id);
CREATE INDEX inventory_freeze_event_effect ON wms.inventory_freeze_events(freeze_id,transaction_id)
 WHERE movement_id IS NOT NULL;
CREATE TRIGGER inventory_freeze_event_immutable BEFORE UPDATE OR DELETE ON wms.inventory_freeze_events
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_document_immutable();

CREATE FUNCTION wms.inventory_application_can_write(p_freeze bigint) RETURNS boolean
 LANGUAGE sql STABLE AS $$ SELECT false $$;

CREATE FUNCTION wms.inventory_fbs_context() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE sid bigint; ids bigint[];
BEGIN
 sid=NULLIF(current_setting('wms.inventory_fbs_shipment_id',true),'')::bigint;
 IF sid IS NULL THEN RETURN NULL; END IF;
 ids=ARRAY(SELECT jsonb_array_elements_text(current_setting('wms.inventory_fbs_items',true)::jsonb)::bigint);
 IF cardinality(ids)=0 OR EXISTS(SELECT 1 FROM unnest(ids) i
     WHERE NOT EXISTS(SELECT 1 FROM wms.fbs_shipment_items s WHERE s.item_id=i AND s.shipment_id=sid)) THEN
   RAISE EXCEPTION USING ERRCODE='P7602', MESSAGE='Invalid FBS freeze context';
 END IF;
 RETURN sid;
END $$;

CREATE FUNCTION wms.inventory_lock_fbs_fence(p_location bigint)
 RETURNS wms.inventory_location_fences LANGUAGE plpgsql AS $$
DECLARE fence wms.inventory_location_fences%ROWTYPE;
BEGIN
 -- Keep independent FBS SKUs concurrent at an unfrozen address. The inner
 -- savepoint releases a speculative SHARE lock if freeze won the race; no
 -- SHARE-to-UPDATE upgrade is retained across that race.
 BEGIN
  SELECT * INTO fence FROM wms.inventory_location_fences
   WHERE location_id=p_location AND freeze_id IS NULL FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7609',MESSAGE='Use active FBS fence'; END IF;
  RETURN fence;
 EXCEPTION WHEN SQLSTATE 'P7609' THEN NULL;
 END;
 SELECT * INTO fence FROM wms.inventory_location_fences WHERE location_id=p_location FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7601',MESSAGE='Location fence missing'; END IF;
 RETURN fence;
END $$;

CREATE FUNCTION wms.inventory_check_location_write(p_location bigint,p_product varchar,p_fbs boolean,p_apply boolean DEFAULT false,
 p_movement bigint DEFAULT NULL,p_created_at timestamptz DEFAULT NULL)
 RETURNS void LANGUAGE plpgsql AS $$
DECLARE fence wms.inventory_location_fences%ROWTYPE; sid bigint;
BEGIN
 IF p_location IS NULL THEN RETURN; END IF;
 IF p_fbs THEN sid=wms.inventory_fbs_context(); END IF;
 IF sid IS NOT NULL THEN
   fence=wms.inventory_lock_fbs_fence(p_location);
 ELSE
   -- NOWAIT avoids reversing legacy inventory/location/KIZ lock orders.
   SELECT * INTO fence FROM wms.inventory_location_fences WHERE location_id=p_location FOR SHARE NOWAIT;
 END IF;
 IF fence.location_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7601', MESSAGE='Location fence missing'; END IF;
 IF fence.freeze_id IS NULL OR (p_apply AND wms.inventory_application_can_write(fence.freeze_id)) THEN RETURN; END IF;
 IF sid IS NULL THEN
   RAISE LOG 'inventory freeze rejected: location=%, freeze=%, user=%',p_location,fence.freeze_id,session_user;
   RAISE EXCEPTION USING ERRCODE='P7601', MESSAGE='Location frozen for inventory',
     DETAIL=jsonb_build_object('location_id',p_location,'freeze_id',fence.freeze_id)::text;
 END IF;
 -- Same transaction as FBS effects. No document row lock is taken by the writer.
 UPDATE wms.inventory_location_fences SET generation=generation+1 WHERE location_id=p_location;
 UPDATE wms.inventory_freezes SET state='invalidated' WHERE freeze_id=fence.freeze_id;
 INSERT INTO wms.inventory_freeze_events(freeze_id,action,author,location_id,product_id,fbs_shipment_id,movement_id,movement_created_at,details)
 VALUES(fence.freeze_id,'fbs_invalidated','fbs',p_location,p_product,sid,p_movement,p_created_at,
        jsonb_build_object('physical_time_proven',false));
END $$;

CREATE FUNCTION wms.inventory_freeze_fbs_lock_locations(p_locations bigint[]) RETURNS void
 LANGUAGE plpgsql AS $$
DECLARE loc bigint;
BEGIN
 IF wms.inventory_fbs_context() IS NULL THEN
   RAISE EXCEPTION USING ERRCODE='P7602',MESSAGE='FBS context required';
 END IF;
 FOR loc IN SELECT DISTINCT unnest(p_locations) ORDER BY 1 LOOP
   PERFORM wms.inventory_lock_fbs_fence(loc);
 END LOOP;
 -- Disjoint source addresses can still share two freeze headers. Lock those
 -- headers by ID before any invalidation to avoid an X/Y versus Y/X cycle.
 PERFORM 1 FROM wms.inventory_freezes f WHERE EXISTS(
   SELECT 1 FROM wms.inventory_location_fences l WHERE l.freeze_id=f.freeze_id
     AND l.location_id=ANY(p_locations)
 ) ORDER BY f.freeze_id FOR UPDATE;
END $$;

CREATE FUNCTION wms.inventory_task_locations(p_tasks bigint[]) RETURNS bigint[] LANGUAGE sql STABLE AS $$
 WITH RECURSIVE family AS (
  SELECT task_id,parent_task_id,from_location_id,to_location_id FROM wms.tasks WHERE task_id=ANY(p_tasks)
  UNION
  SELECT p.task_id,p.parent_task_id,p.from_location_id,p.to_location_id
   FROM wms.tasks p JOIN family child ON child.parent_task_id=p.task_id
 ), addresses AS (
  SELECT unnest(ARRAY[from_location_id,to_location_id]) AS loc FROM family
  UNION SELECT i.from_location_id FROM wms.task_items i JOIN family f USING(task_id)
 ) SELECT COALESCE(array_agg(loc) FILTER(WHERE loc IS NOT NULL),'{}'::bigint[]) FROM addresses
$$;

CREATE FUNCTION wms.guard_inventory_location_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a jsonb='{}'; b jsonb='{}'; ids bigint[]; loc bigint; pid varchar; fbs boolean=false; app boolean=false;
 movement bigint; movement_time timestamptz;
 relation_name text=COALESCE(TG_ARGV[0],TG_TABLE_NAME);
BEGIN
 IF TG_OP<>'INSERT' THEN a=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN b=to_jsonb(NEW); END IF;
 IF relation_name IN ('tasks','task_items') THEN
   -- Short admission latch for unbounded task intent. Physical inventory/FBS
   -- writers do not use this latch. No lock is held over the physical count.
   PERFORM pg_advisory_xact_lock_shared(hashtextextended('inventory-task-admission',0));
 END IF;
 pid=COALESCE(b->>'product_id',a->>'product_id');
 IF relation_name='movements' THEN
   ids=ARRAY[(a->>'from_location_id')::bigint,(a->>'to_location_id')::bigint,
             (b->>'from_location_id')::bigint,(b->>'to_location_id')::bigint];
   fbs=(TG_OP='INSERT' AND b->>'movement_type'='ship' AND b->>'to_location_id' IS NULL);
   app=(TG_OP='INSERT' AND b->>'source_type'='inventory_document');
   IF fbs THEN movement=(b->>'movement_id')::bigint; movement_time=(b->>'created_at')::timestamptz; END IF;
 ELSIF relation_name='locations' THEN
   ids=ARRAY[(a->>'location_id')::bigint,(b->>'location_id')::bigint];
   IF TG_OP='DELETE' OR ROW(a->>'parent_location_id',a->>'path',a->>'location_code')
        IS DISTINCT FROM ROW(b->>'parent_location_id',b->>'path',b->>'location_code') THEN
     ids=ids || ARRAY(WITH RECURSIVE descendants(location_id) AS (
       SELECT location_id FROM wms.locations WHERE location_id=(a->>'location_id')::bigint
       UNION SELECT l.location_id FROM wms.locations l JOIN descendants d ON l.parent_location_id=d.location_id
     ) SELECT location_id FROM descendants UNION
       SELECT location_id FROM wms.locations WHERE path OPERATOR(wms.<@) (a->>'path')::wms.ltree);
   END IF;
 ELSIF relation_name='tasks' THEN
   IF TG_OP='UPDATE' AND b->>'status'='cancelled' AND a->>'status' IN ('pending','assigned')
      AND ROW(a->>'from_location_id',a->>'to_location_id') IS NOT DISTINCT FROM ROW(b->>'from_location_id',b->>'to_location_id') THEN
     RETURN NEW;
   END IF;
   ids=ARRAY[(a->>'from_location_id')::bigint,(a->>'to_location_id')::bigint,
             (b->>'from_location_id')::bigint,(b->>'to_location_id')::bigint]
       || wms.inventory_task_locations(ARRAY[(a->>'task_id')::bigint,(b->>'task_id')::bigint,
                                              (a->>'parent_task_id')::bigint,(b->>'parent_task_id')::bigint]);
 ELSIF relation_name='task_items' THEN
   ids=ARRAY[(a->>'from_location_id')::bigint,(b->>'from_location_id')::bigint]
       || wms.inventory_task_locations(ARRAY[(a->>'task_id')::bigint,(b->>'task_id')::bigint]);
 ELSIF relation_name='container_contents' THEN
   SELECT array_agg(location_id) INTO ids FROM wms.containers
    WHERE container_id IN ((a->>'container_id')::bigint,(b->>'container_id')::bigint);
 ELSE
   ids=ARRAY[(a->>'location_id')::bigint,(b->>'location_id')::bigint];
   IF relation_name='kiz' THEN
     ids=ids || ARRAY(SELECT location_id FROM wms.containers
       WHERE container_id IN ((a->>'container_id')::bigint,(b->>'container_id')::bigint));
   END IF;
   fbs=relation_name IN ('kiz','inventory');
   app=(relation_name='inventory' AND pg_trigger_depth()>1);
 END IF;
 FOR loc IN SELECT DISTINCT id FROM unnest(ids) AS u(id) WHERE id IS NOT NULL ORDER BY 1 LOOP
   PERFORM wms.inventory_check_location_write(loc,pid,fbs,app,movement,movement_time);
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
-- Explicit operator roster; no role discovery, ALTER ROLE, default privileges,
-- SECURITY DEFINER, or schema-wide GRANT. Default mode only checks privileges.
CREATE FUNCTION wms.inventory_runtime_privileges(p_phase text) RETURNS void
 LANGUAGE plpgsql AS $privileges$
DECLARE roster jsonb; entry jsonb; role_name text; profile text; grant_mode boolean;
 spec record; privilege text; seq text; fn record; table_name text;
BEGIN
 IF p_phase NOT IN ('freeze','apply') THEN RAISE EXCEPTION 'Invalid inventory privilege phase'; END IF;
 roster=NULLIF(current_setting('wms.inventory_runtime_roles',true),'')::jsonb;
 IF roster IS NULL OR jsonb_typeof(roster)<>'array' OR jsonb_array_length(roster)=0 THEN
   RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Explicit inventory runtime role roster is required',
     HINT='Set wms.inventory_runtime_roles to [{"role":"actual_runtime_role","profile":"writer|inventory"}]. Include every API/consumer/retry role.';
 END IF;
 grant_mode=COALESCE(current_setting('wms.inventory_grant_runtime_privileges',true),'off')='on';
 FOR entry IN SELECT value FROM jsonb_array_elements(roster) LOOP
   role_name=entry->>'role'; profile=entry->>'profile';
   IF role_name IS NULL OR profile IS NULL OR profile NOT IN ('writer','inventory')
      OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
     RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Invalid inventory runtime role/profile';
   END IF;
   IF NOT has_schema_privilege(role_name,'wms','USAGE') OR NOT has_schema_privilege(role_name,'public','USAGE') THEN
     RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Runtime role requires existing wms/public schema USAGE',DETAIL=role_name;
   END IF;
   -- Apply adds locking reads on metadata rows. PostgreSQL accepts UPDATE on
   -- any column; grant only the key column if no existing UPDATE right exists.
   IF p_phase='apply' AND profile='inventory' THEN
     FOR spec IN SELECT * FROM (VALUES ('public.products','id'),('wms.locations','location_id'))
       AS locks(object_name,key_column) LOOP
       IF grant_mode AND NOT has_table_privilege(role_name,spec.object_name,'SELECT') THEN
         EXECUTE format('GRANT SELECT ON TABLE %s TO %I',spec.object_name,role_name);
       END IF;
       IF grant_mode AND NOT has_any_column_privilege(role_name,spec.object_name,'UPDATE') THEN
         EXECUTE format('GRANT UPDATE (%I) ON TABLE %s TO %I',spec.key_column,spec.object_name,role_name);
       END IF;
       IF NOT has_table_privilege(role_name,spec.object_name,'SELECT')
          OR NOT has_any_column_privilege(role_name,spec.object_name,'UPDATE') THEN
         RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Missing inventory metadata locking privilege',
           DETAIL=role_name||' '||spec.object_name||' SELECT + UPDATE on at least one column';
       END IF;
     END LOOP;
   END IF;
   FOR spec IN SELECT * FROM (VALUES
     ('inventory_documents','SELECT','SELECT,INSERT,UPDATE'),
     ('inventory_document_filters',NULL,'SELECT,INSERT,UPDATE'),
     ('inventory_document_locations',NULL,'SELECT,INSERT'),
     ('inventory_location_claims','SELECT','SELECT,INSERT,DELETE'),
     ('inventory_document_revisions',NULL,'SELECT,INSERT'),
     ('inventory_document_stock_components',NULL,'SELECT,INSERT'),
     ('inventory_document_kiz_snapshot',NULL,'SELECT,INSERT'),
     ('inventory_document_items','SELECT','SELECT,INSERT,UPDATE'),
     ('inventory_count_attempts','SELECT','SELECT,INSERT,UPDATE'),
     ('inventory_document_history',NULL,'SELECT,INSERT'),
     ('inventory_document_commands',NULL,'SELECT,INSERT'),
     ('inventory_application_preparations','SELECT','SELECT,INSERT'),
     ('inventory_application_components','SELECT','SELECT,INSERT'),
     ('inventory_freezes','SELECT,UPDATE','SELECT,INSERT,UPDATE'),
     ('inventory_location_fences','SELECT,INSERT,UPDATE','SELECT,INSERT,UPDATE'),
     ('inventory_freeze_events','SELECT,INSERT','SELECT,INSERT'),
     ('inventory_applications','SELECT','SELECT,INSERT,UPDATE'),
     ('inventory_application_movements','SELECT','SELECT,INSERT')
   ) AS rules(object_name,writer_rights,inventory_rights) LOOP
     IF p_phase='freeze' AND spec.object_name IN ('inventory_applications','inventory_application_movements') THEN CONTINUE; END IF;
     privilege=CASE WHEN profile='writer' THEN spec.writer_rights ELSE spec.inventory_rights END;
     IF privilege IS NULL THEN CONTINUE; END IF;
     table_name=format('wms.%I',spec.object_name);
     IF to_regclass(table_name) IS NULL THEN
       RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Missing inventory privilege target',DETAIL=table_name;
     END IF;
     IF grant_mode THEN EXECUTE format('GRANT %s ON TABLE %s TO %I',privilege,table_name,role_name); END IF;
     FOREACH privilege IN ARRAY string_to_array(privilege,',') LOOP
       IF NOT has_table_privilege(role_name,table_name,privilege) THEN
         RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Missing inventory runtime privilege',
           DETAIL=format('role=%I object=%s privilege=%s',role_name,table_name,privilege),
           HINT='Default mode is check-only. An authorized operator may explicitly opt in to scoped grants with wms.inventory_grant_runtime_privileges=on.';
       END IF;
     END LOOP;
     -- Identity sequences: USAGE only, no blanket sequence/table permissions.
     IF (CASE WHEN profile='writer' THEN spec.writer_rights ELSE spec.inventory_rights END) LIKE '%INSERT%' THEN
       FOR seq IN SELECT pg_get_serial_sequence(table_name,a.attname) FROM pg_attribute a
         WHERE a.attrelid=table_name::regclass AND a.attidentity<>'' AND NOT a.attisdropped LOOP
         IF grant_mode THEN EXECUTE format('GRANT USAGE ON SEQUENCE %s TO %I',seq,role_name); END IF;
         IF NOT has_sequence_privilege(role_name,seq,'USAGE') THEN
           RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Missing inventory identity sequence USAGE',DETAIL=role_name||' '||seq;
         END IF;
       END LOOP;
     END IF;
   END LOOP;
   FOR fn IN SELECT oid,oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='wms'::regnamespace
     AND proname IN ('inventory_application_can_write','inventory_fbs_context','inventory_lock_fbs_fence',
       'inventory_check_location_write','inventory_freeze_fbs_lock_locations','inventory_task_locations',
       'apply_strict_inventory_source_adjustment') LOOP
     IF grant_mode THEN EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',fn.signature,role_name); END IF;
     IF NOT has_function_privilege(role_name,fn.oid,'EXECUTE') THEN
       RAISE EXCEPTION USING ERRCODE='P7605',MESSAGE='Missing inventory helper EXECUTE',DETAIL=role_name||' '||fn.signature;
     END IF;
   END LOOP;
 END LOOP;
END $privileges$;
REVOKE ALL ON FUNCTION wms.inventory_runtime_privileges(text) FROM PUBLIC;
-- All ACL changes, if explicitly requested, and checks precede installation of
-- guards on existing writers. Any failure rolls back objects, ACLs and guards.
SELECT wms.inventory_runtime_privileges('freeze');

DO $$ DECLARE rel text; BEGIN
 FOREACH rel IN ARRAY ARRAY['movements','inventory','kiz','containers','container_contents','tasks','task_items'] LOOP
   EXECUTE format('CREATE TRIGGER aaa_inventory_location_freeze BEFORE INSERT OR UPDATE OR DELETE ON wms.%I '
                  'FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_location_freeze(%L)',rel,rel);
 END LOOP;
END $$;
CREATE TRIGGER aaa_inventory_location_freeze BEFORE UPDATE OR DELETE ON wms.locations
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_location_freeze();
CREATE FUNCTION wms.create_inventory_location_fence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO wms.inventory_location_fences(location_id) VALUES(NEW.location_id); RETURN NEW; END $$;
CREATE TRIGGER zz_inventory_location_fence AFTER INSERT ON wms.locations
 FOR EACH ROW EXECUTE FUNCTION wms.create_inventory_location_fence();

CREATE FUNCTION wms.guard_inventory_freeze_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM wms.inventory_location_fences WHERE freeze_id IS NOT NULL) THEN
   RAISE EXCEPTION USING ERRCODE='P7601',MESSAGE='TRUNCATE forbidden during inventory freeze';
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE rel text; BEGIN
 FOREACH rel IN ARRAY ARRAY['movements','inventory','kiz','containers','container_contents','locations'] LOOP
   EXECUTE format('CREATE TRIGGER inventory_freeze_truncate BEFORE TRUNCATE ON wms.%I '
                  'EXECUTE FUNCTION wms.guard_inventory_freeze_truncate()',rel);
 END LOOP;
END $$;

CREATE FUNCTION wms.require_inventory_fbs_effect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.action<>'fbs_invalidated' THEN RETURN NULL; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM wms.inventory_freeze_events witness
   JOIN wms.movements m ON m.movement_id=witness.movement_id AND m.created_at=witness.movement_created_at
  WHERE witness.action='fbs_invalidated' AND witness.freeze_id=NEW.freeze_id
   AND witness.movement_id IS NOT NULL
   AND witness.transaction_id=NEW.transaction_id AND witness.fbs_shipment_id=NEW.fbs_shipment_id
   AND witness.location_id=NEW.location_id AND witness.product_id=NEW.product_id
   AND m.movement_type='ship'
   AND m.product_id=NEW.product_id AND m.from_location_id=NEW.location_id
   AND (EXISTS(SELECT 1 FROM wms.fbs_shipment_items i
         WHERE i.shipment_id=NEW.fbs_shipment_id AND i.product_id=m.product_id
           AND i.status='success' AND i.movement_id=m.movement_id)
     OR EXISTS(SELECT 1 FROM wms.fbs_shipment_task_results r
         WHERE r.shipment_id=NEW.fbs_shipment_id AND r.product_id=m.product_id
           AND r.outcome='written_off' AND r.movement_id=m.movement_id AND r.movement_created_at=m.created_at))
 ) THEN RAISE EXCEPTION USING ERRCODE='P7602',MESSAGE='FBS freeze bypass has no committed shipment effect'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER inventory_fbs_effect AFTER INSERT ON wms.inventory_freeze_events
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='fbs_invalidated')
 EXECUTE FUNCTION wms.require_inventory_fbs_effect();

ALTER TABLE wms.inventory_count_attempts DROP CONSTRAINT inventory_count_attempts_status_check;
ALTER TABLE wms.inventory_count_attempts ADD CHECK(status IN ('in_progress','completed','cancelled'));
CREATE FUNCTION wms.guard_inventory_cancelled_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='cancelled' THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Cancelled count attempt is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_cancelled_attempt BEFORE UPDATE ON wms.inventory_count_attempts
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_cancelled_attempt();

CREATE FUNCTION wms.guard_inventory_freeze_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Freeze history cannot be deleted'; END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.state<>'freezing' OR NEW.epoch<>0 OR NOT EXISTS(SELECT 1 FROM wms.inventory_documents
       WHERE document_id=NEW.document_id AND mode='partial' AND status='counting') THEN
     RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid initial freeze';
   END IF;
   RETURN NEW;
 END IF;
 IF OLD.state='released' OR ROW(NEW.freeze_id,NEW.document_id,NEW.created_by,NEW.created_at)
     IS DISTINCT FROM ROW(OLD.freeze_id,OLD.document_id,OLD.created_by,OLD.created_at)
 OR NOT (
    (OLD.state='freezing' AND NEW.state IN ('frozen','invalidated','released')) OR
    (OLD.state='frozen' AND NEW.state IN ('ready_to_apply','invalidated','released')) OR
    (OLD.state='ready_to_apply' AND NEW.state IN ('frozen','invalidated','released')) OR
    (OLD.state='invalidated' AND NEW.state IN ('frozen','invalidated','released'))
 ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid freeze transition'; END IF;
 IF NEW.state='frozen' AND OLD.state IN ('freezing','invalidated') THEN
   IF NEW.epoch<>OLD.epoch+1 THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Reconfirmation requires a new count epoch'; END IF;
 ELSIF ROW(NEW.epoch,NEW.confirmed_by,NEW.confirmed_at,NEW.confirmation,NEW.baseline)
     IS DISTINCT FROM ROW(OLD.epoch,OLD.confirmed_by,OLD.confirmed_at,OLD.confirmation,OLD.baseline) THEN
   RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Frozen baseline is immutable within an epoch';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_freeze_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON wms.inventory_freezes
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_freeze_lifecycle();

CREATE FUNCTION wms.guard_inventory_location_fence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF OLD.freeze_id IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='P7601',MESSAGE='Active fence cannot be deleted'; END IF;
   RETURN OLD;
 END IF;
 IF NEW.freeze_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM wms.inventory_freezes f JOIN wms.inventory_location_claims c ON c.document_id=f.document_id
  WHERE f.freeze_id=NEW.freeze_id AND f.state<>'released' AND c.location_id=NEW.location_id
 ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Freeze fence requires its document location claim'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_fence_guard BEFORE INSERT OR UPDATE OR DELETE ON wms.inventory_location_fences
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_location_fence();
COMMIT;
