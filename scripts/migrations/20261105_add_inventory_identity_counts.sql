-- Stage 2B. Manual additive deployment AFTER Stage 2A. No physical data changes.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
CREATE TABLE wms.inventory_identity_config (
 document_id bigint PRIMARY KEY REFERENCES wms.inventory_documents,
 containers boolean NOT NULL DEFAULT false
);
CREATE TABLE wms.inventory_identity_snapshots (
 document_id bigint PRIMARY KEY REFERENCES wms.inventory_identity_config,
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object')
);
CREATE TABLE wms.inventory_identity_counts (
 count_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 document_id bigint NOT NULL REFERENCES wms.inventory_identity_config,
 revision_no integer NOT NULL,
 author varchar(100) NOT NULL,
 status text NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','completed','superseded')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 start_evidence jsonb NOT NULL,
 end_evidence jsonb,
 freeze_token jsonb,
 result jsonb,
 UNIQUE(count_id,document_id),
 FOREIGN KEY(document_id,revision_no) REFERENCES wms.inventory_document_revisions(document_id,revision_no),
 CHECK((status='completed')=(completed_at IS NOT NULL)),
 CHECK(status<>'completed' OR (end_evidence IS NOT NULL AND result IS NOT NULL))
);
CREATE INDEX inventory_identity_count_history ON wms.inventory_identity_counts(document_id,count_id DESC);
CREATE UNIQUE INDEX inventory_identity_one_open_count ON wms.inventory_identity_counts(document_id) WHERE status='in_progress';
CREATE TABLE wms.inventory_identity_scans (
 scan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 count_id bigint NOT NULL,
 document_id bigint NOT NULL,
 scanned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 author varchar(100) NOT NULL,
 kind text NOT NULL CHECK(kind IN ('kiz','container')),
 code text NOT NULL CHECK(length(code)>0),
 location_id bigint NOT NULL,
 holder_qr text,
 contents jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(contents)='array'),
 FOREIGN KEY(count_id,document_id) REFERENCES wms.inventory_identity_counts(count_id,document_id),
 FOREIGN KEY(document_id,location_id) REFERENCES wms.inventory_document_locations(document_id,location_id),
 CHECK(kind<>'kiz' OR length(code)<=24)
);
CREATE INDEX inventory_identity_scan_history ON wms.inventory_identity_scans(count_id,scan_id);
CREATE TABLE wms.inventory_identity_actions (
 application_id bigint NOT NULL REFERENCES wms.inventory_applications,
 action_no integer NOT NULL CHECK(action_no>0),
 intent jsonb NOT NULL,
 result jsonb,
 PRIMARY KEY(application_id,action_no)
);
CREATE TABLE wms.inventory_identity_movement_links (
 application_id bigint NOT NULL,
 action_no integer NOT NULL,
 movement_ref bigint NOT NULL UNIQUE REFERENCES wms.movement_registry,
 PRIMARY KEY(application_id,action_no,movement_ref),
 FOREIGN KEY(application_id,action_no) REFERENCES wms.inventory_identity_actions
);
CREATE FUNCTION wms.guard_inventory_identity_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Identity history cannot be deleted'; END IF;
 IF TG_TABLE_NAME='inventory_identity_counts' THEN
   IF OLD.status<>'in_progress' OR NEW.status NOT IN ('completed','superseded')
    OR (to_jsonb(NEW)-ARRAY['status','completed_at','end_evidence','result'])
       IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','completed_at','end_evidence','result']) THEN
    RAISE EXCEPTION 'Completed identity count is immutable'; END IF;
 ELSIF TG_TABLE_NAME='inventory_identity_actions' THEN
   IF OLD.result IS NOT NULL OR NEW.result IS NULL
    OR ROW(NEW.application_id,NEW.action_no,NEW.intent) IS DISTINCT FROM ROW(OLD.application_id,OLD.action_no,OLD.intent)
    THEN RAISE EXCEPTION 'Identity action is immutable'; END IF;
 ELSE RAISE EXCEPTION 'Identity history is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_identity_counts_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_counts
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE TRIGGER inventory_identity_actions_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_actions
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE TRIGGER inventory_identity_snapshots_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_snapshots
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE TRIGGER inventory_identity_scans_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_scans
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE TRIGGER inventory_identity_links_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_movement_links
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE FUNCTION wms.guard_inventory_identity_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d wms.inventory_documents%ROWTYPE;
BEGIN
 SELECT * INTO d FROM wms.inventory_documents WHERE document_id=NEW.document_id FOR UPDATE;
 IF TG_TABLE_NAME='inventory_identity_config' THEN
   IF d.status<>'draft' THEN RAISE EXCEPTION 'Identity accounting is fixed at start'; END IF;
 ELSIF TG_TABLE_NAME='inventory_identity_snapshots' THEN
   IF d.status<>'draft' THEN RAISE EXCEPTION 'Initial identity snapshot is fixed at start'; END IF;
 ELSE
   IF d.status<>'counting' THEN RAISE EXCEPTION 'Identity scans require counting document'; END IF;
   IF TG_TABLE_NAME='inventory_identity_scans' AND NOT EXISTS(
    SELECT 1 FROM wms.inventory_identity_counts c WHERE c.count_id=NEW.count_id AND c.status='in_progress'
     AND c.count_id=(SELECT max(count_id) FROM wms.inventory_identity_counts WHERE document_id=NEW.document_id)
   ) THEN RAISE EXCEPTION 'Identity count is not current/open'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_identity_config_input BEFORE INSERT ON wms.inventory_identity_config
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_input();
CREATE TRIGGER inventory_identity_config_history BEFORE UPDATE OR DELETE ON wms.inventory_identity_config
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_history();
CREATE TRIGGER inventory_identity_snapshot_input BEFORE INSERT ON wms.inventory_identity_snapshots
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_input();
CREATE TRIGGER inventory_identity_scan_input BEFORE INSERT ON wms.inventory_identity_scans
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_input();
CREATE TRIGGER inventory_identity_count_input BEFORE INSERT OR UPDATE ON wms.inventory_identity_counts
 FOR EACH ROW EXECUTE FUNCTION wms.guard_inventory_identity_input();

-- Admission remains transaction-scoped and fence-locked. Never a free-form GUC bypass.
CREATE FUNCTION wms.inventory_identity_can_write(p_freeze bigint,p_location bigint) RETURNS boolean
 LANGUAGE plpgsql STABLE AS $$
BEGIN
 IF NULLIF(current_setting('wms.inventory_identity_action',true),'') IS NULL THEN RETURN false; END IF;
 RETURN EXISTS(SELECT 1 FROM wms.inventory_identity_actions x
  JOIN wms.inventory_applications a USING(application_id)
  JOIN wms.inventory_freezes f ON f.freeze_id=a.freeze_id
  JOIN wms.inventory_documents d ON d.document_id=a.document_id
  JOIN wms.inventory_document_locations l ON l.document_id=d.document_id
  WHERE a.application_id=NULLIF(current_setting('wms.inventory_application_id',true),'')::bigint
   AND x.action_no=current_setting('wms.inventory_identity_action')::integer AND x.result IS NULL
   AND a.transaction_id=pg_current_xact_id() AND a.status='applying'
   AND f.freeze_id=p_freeze AND f.state='ready_to_apply' AND f.epoch=a.freeze_epoch
   AND d.status='pending_approval' AND d.current_revision=a.approved_revision
   AND l.location_id=p_location);
END $$;

DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
 WHERE p.oid='wms.inventory_check_location_write(bigint,character varying,boolean,boolean,bigint,timestamp with time zone)'::regprocedure
 AND md5(p.prosrc)='aca60cf9d0cc472df2c334adf4f983eb' AND NOT p.prosecdef
 AND p.proconfig IS NULL AND l.lanname='plpgsql' AND p.prorettype='void'::regtype
 AND p.provolatile='v' AND p.proparallel='u') THEN
 RAISE EXCEPTION 'Unsupported Stage 2A admission function; review before upgrade'; END IF; END $$;
CREATE OR REPLACE FUNCTION wms.inventory_check_location_write(p_location bigint,p_product varchar,p_fbs boolean,p_apply boolean DEFAULT false,
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
 IF fence.freeze_id IS NULL OR (p_apply AND wms.inventory_application_can_write(fence.freeze_id))
    OR wms.inventory_identity_can_write(fence.freeze_id,p_location) THEN RETURN; END IF;
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

CREATE FUNCTION wms.require_inventory_identity_action() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a wms.inventory_identity_actions%ROWTYPE;
BEGIN
 SELECT * INTO a FROM wms.inventory_identity_actions WHERE application_id=NEW.application_id AND action_no=NEW.action_no;
 IF a.result IS NULL OR NOT EXISTS(SELECT 1 FROM wms.inventory_applications
   WHERE application_id=a.application_id AND status='applied') THEN
  RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Incomplete identity action'; END IF;
 IF a.intent->>'kind'='kiz_transfer' THEN
  IF NOT EXISTS(SELECT 1 FROM wms.kiz_operations o WHERE o.operation_id=(a.result->>'operation_id')::bigint
   AND o.operation_type='transfer' AND o.source_system='inventory_document'
   AND o.external_operation_id=a.application_id||':identity:'||a.action_no AND o.result_payload IS NOT NULL) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Identity action lacks controlled KIZ operation'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM wms.container_operations o WHERE o.operation_id=(a.result->>'operation_id')::bigint
   AND o.source_system='inventory_document' AND o.result_payload IS NOT NULL
   AND o.operation_type=CASE WHEN a.intent->>'kind'='container_move' THEN 'move' ELSE a.intent->>'kind' END
   AND o.external_operation_id=a.application_id||':identity:'||a.action_no) THEN
   RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Identity action lacks controlled container operation'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(a.result->'movement_refs') r
  WHERE NOT EXISTS(SELECT 1 FROM wms.inventory_identity_movement_links l WHERE l.application_id=a.application_id
    AND l.action_no=a.action_no AND l.movement_ref=r::bigint)) THEN
  RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Missing identity movement registry link'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER inventory_identity_action_complete AFTER INSERT OR UPDATE ON wms.inventory_identity_actions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_inventory_identity_action();

CREATE FUNCTION wms.require_inventory_identity_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identity jsonb; c wms.inventory_identity_counts%ROWTYPE; expected bigint;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM wms.inventory_identity_config WHERE document_id=NEW.document_id) THEN RETURN NULL; END IF;
 SELECT evidence->'identity' INTO identity FROM wms.inventory_application_preparations WHERE preparation_id=NEW.preparation_id;
 SELECT * INTO c FROM wms.inventory_identity_counts WHERE count_id=(identity->>'count_id')::bigint AND document_id=NEW.document_id;
 IF NOT FOUND OR c.status<>'completed' OR identity->'blockers' IS DISTINCT FROM '[]'::jsonb
   OR c.freeze_token IS DISTINCT FROM jsonb_build_object('freeze_id',NEW.freeze_id,'epoch',NEW.freeze_epoch)
   OR c.result->'conflicts' IS DISTINCT FROM '[]'::jsonb
   OR c.count_id<>(SELECT max(count_id) FROM wms.inventory_identity_counts WHERE document_id=NEW.document_id) THEN
  RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Application lacks current final identity count'; END IF;
 expected=jsonb_array_length(identity->'plan'->'actions');
 IF expected<>(SELECT count(*) FROM wms.inventory_identity_actions WHERE application_id=NEW.application_id)
 OR EXISTS(SELECT 1 FROM wms.inventory_identity_actions a WHERE a.application_id=NEW.application_id
   AND (a.intent IS DISTINCT FROM identity->'plan'->'actions'->(a.action_no-1) OR a.result IS NULL)) THEN
  RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='Inventory identity action plan incomplete'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER inventory_identity_document_complete AFTER INSERT OR UPDATE ON wms.inventory_applications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_inventory_identity_document();

-- No automatic GRANT. Explicit check/grant on new objects, only inventory profile.
CREATE FUNCTION wms.inventory_identity_runtime_privileges() RETURNS void LANGUAGE plpgsql AS $$
DECLARE entry jsonb; role_name text; spec record; seq text; privilege text; signature text;
BEGIN
 IF NULLIF(current_setting('wms.inventory_runtime_roles',true),'') IS NULL THEN
  RAISE EXCEPTION 'Explicit runtime roster required'; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(current_setting('wms.inventory_runtime_roles')::jsonb) LOOP
  role_name=entry->>'role';
  IF role_name IS NULL OR entry->>'profile' IS NULL OR entry->>'profile' NOT IN ('writer','inventory')
    OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN RAISE EXCEPTION 'Invalid runtime role/profile'; END IF;
  IF current_setting('wms.inventory_grant_runtime_privileges',true)='on' THEN
    EXECUTE format('GRANT EXECUTE ON FUNCTION wms.inventory_identity_can_write(bigint,bigint) TO %I',role_name);
  END IF;
  IF NOT has_function_privilege(role_name,'wms.inventory_identity_can_write(bigint,bigint)','EXECUTE') THEN
    RAISE EXCEPTION 'Missing identity admission helper EXECUTE for %',role_name; END IF;
  IF entry->>'profile'<>'inventory' THEN CONTINUE; END IF;
  -- Existing controlled-writer rights are prerequisites, never silently broadened.
  FOR spec IN SELECT * FROM (VALUES
   ('wms.kiz','SELECT'),('wms.kiz_events','SELECT'),
   ('wms.kiz_operations','SELECT,INSERT,UPDATE'),('wms.kiz_operation_items','SELECT,INSERT,UPDATE'),
   ('wms.kiz_movement_links','SELECT,INSERT'),('wms.containers','SELECT,UPDATE'),
   ('wms.container_contents','SELECT,INSERT,UPDATE'),
   ('wms.container_operations','SELECT,INSERT,UPDATE'),('wms.container_operation_items','SELECT,INSERT,UPDATE')
  ) t(obj,rights) LOOP
    FOREACH privilege IN ARRAY string_to_array(spec.rights,',') LOOP
     IF NOT has_table_privilege(role_name,spec.obj,privilege) THEN
      RAISE EXCEPTION 'Missing existing controlled-writer prerequisite: % % %',role_name,spec.obj,privilege; END IF;
    END LOOP;
    IF spec.rights LIKE '%INSERT%' THEN
     FOR seq IN SELECT pg_get_serial_sequence(spec.obj,a.attname) FROM pg_attribute a
       WHERE a.attrelid=spec.obj::regclass AND a.attnum>0 AND NOT a.attisdropped LOOP
      IF seq IS NOT NULL AND NOT has_sequence_privilege(role_name,seq,'USAGE') THEN
       RAISE EXCEPTION 'Missing existing controlled sequence USAGE: % %',role_name,seq; END IF;
     END LOOP;
    END IF;
  END LOOP;
  IF NOT has_any_column_privilege(role_name,'wms.kiz','UPDATE') THEN
    RAISE EXCEPTION 'Existing KIZ locking read requires UPDATE on at least one column: %',role_name; END IF;
  FOREACH signature IN ARRAY ARRAY[
    'wms.transfer_kiz_location(bigint,bigint,character varying,bigint,bigint)',
    'wms.transition_kiz_container_holder(bigint,bigint,character varying)',
    'wms.apply_container_extract_content(bigint)',
    'wms.check_kiz_holder_integrity()'] LOOP
    IF current_setting('wms.inventory_grant_runtime_privileges',true)='on' THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,role_name); END IF;
    IF NOT has_function_privilege(role_name,signature,'EXECUTE') THEN
      RAISE EXCEPTION 'Missing controlled operation EXECUTE: % %',role_name,signature; END IF;
  END LOOP;
  FOR spec IN SELECT * FROM (VALUES
   ('inventory_identity_config','SELECT,INSERT'),('inventory_identity_snapshots','SELECT,INSERT'),
   ('inventory_identity_counts','SELECT,INSERT,UPDATE'),('inventory_identity_scans','SELECT,INSERT'),
   ('inventory_identity_actions','SELECT,INSERT,UPDATE'),('inventory_identity_movement_links','SELECT,INSERT')) t(obj,rights) LOOP
    IF current_setting('wms.inventory_grant_runtime_privileges',true)='on' THEN
      EXECUTE format('GRANT %s ON wms.%I TO %I',spec.rights,spec.obj,role_name);
    END IF;
    FOR seq IN SELECT pg_get_serial_sequence('wms.'||spec.obj,a.attname)
       FROM pg_attribute a WHERE a.attrelid=('wms.'||spec.obj)::regclass AND a.attidentity<>'' LOOP
      IF current_setting('wms.inventory_grant_runtime_privileges',true)='on' THEN
        EXECUTE format('GRANT USAGE ON SEQUENCE %s TO %I',seq,role_name); END IF;
      IF NOT has_sequence_privilege(role_name,seq,'USAGE') THEN RAISE EXCEPTION 'Missing identity sequence USAGE for %',role_name; END IF;
    END LOOP;
    FOREACH privilege IN ARRAY string_to_array(spec.rights,',') LOOP
     IF NOT has_table_privilege(role_name,'wms.'||spec.obj,privilege) THEN
      RAISE EXCEPTION 'Missing Stage 2B privilege: % % %',role_name,spec.obj,privilege; END IF;
    END LOOP;
  END LOOP;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION wms.inventory_identity_runtime_privileges() FROM PUBLIC;
SELECT wms.inventory_identity_runtime_privileges();
COMMIT;
