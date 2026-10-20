-- Stage 3C C1: active KIZ may be held directly by a location or by a container.
-- Apply manually after 20260917_add_container_b4_final.sql. No business stock is moved.
BEGIN;
SET LOCAL lock_timeout='10s';

LOCK TABLE wms.kiz,wms.kiz_events,wms.container_operations,wms.container_operation_items
IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE wms.kiz
    ADD COLUMN container_id bigint,
    ADD CONSTRAINT fk_kiz_container FOREIGN KEY(container_id)
      REFERENCES wms.containers(container_id) ON UPDATE RESTRICT ON DELETE RESTRICT;
ALTER TABLE wms.kiz DROP CONSTRAINT chk_kiz_lifecycle_location;
ALTER TABLE wms.kiz ADD CONSTRAINT chk_kiz_lifecycle_holder CHECK (
    (lifecycle_status='active' AND closed_at IS NULL
      AND ((location_id IS NOT NULL)::int+(container_id IS NOT NULL)::int)=1)
 OR (lifecycle_status IN ('error','deactivated') AND closed_at IS NOT NULL
      AND NOT (location_id IS NOT NULL AND container_id IS NOT NULL))
 OR (lifecycle_status='shipped' AND closed_at IS NOT NULL
      AND location_id IS NULL AND container_id IS NULL)
);
CREATE INDEX idx_kiz_active_container_product
    ON wms.kiz(container_id,product_id,kiz_id) WHERE lifecycle_status='active';

ALTER TABLE wms.kiz_events
    ALTER COLUMN location_id DROP NOT NULL,
    ADD COLUMN container_id bigint,
    ADD CONSTRAINT fk_kiz_events_container FOREIGN KEY(container_id)
      REFERENCES wms.containers(container_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT chk_kiz_events_holder_shape
      CHECK (NOT (location_id IS NOT NULL AND container_id IS NOT NULL));

CREATE TABLE wms.kiz_container_holder_authorizations(
    transaction_id bigint NOT NULL,
    operation_item_id bigint NOT NULL,
    kiz_id bigint NOT NULL,
    direction varchar(16) NOT NULL CHECK(direction IN ('fill','extract')),
    product_id varchar(50) NOT NULL,
    location_id bigint NOT NULL,
    container_id bigint NOT NULL,
    PRIMARY KEY(transaction_id,kiz_id),
    FOREIGN KEY(operation_item_id) REFERENCES wms.container_operation_items(operation_item_id)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(kiz_id) REFERENCES wms.kiz(kiz_id) ON UPDATE RESTRICT ON DELETE RESTRICT
);
REVOKE ALL ON wms.kiz_container_holder_authorizations FROM PUBLIC;

CREATE OR REPLACE FUNCTION wms.guard_kiz_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,wms AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Hard delete КИЗ запрещён';
    END IF;
    IF ROW(NEW.kiz_id,NEW.kiz_code,NEW.product_id,NEW.container_id,
           NEW.origin_type,NEW.origin_reference,NEW.assigned_at,NEW.created_at,
           NEW.created_by,NEW.metadata,NEW.lifecycle_status,NEW.closed_at)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id,OLD.kiz_code,OLD.product_id,OLD.container_id,
           OLD.origin_type,OLD.origin_reference,OLD.assigned_at,OLD.created_at,
           OLD.created_by,OLD.metadata,OLD.lifecycle_status,OLD.closed_at)
       AND OLD.lifecycle_status='active'
       AND NEW.location_id IS DISTINCT FROM OLD.location_id
       AND EXISTS(SELECT 1 FROM wms.kiz_location_update_authorizations a
          WHERE a.transaction_id=txid_current() AND a.kiz_id=OLD.kiz_id
            AND a.product_id=OLD.product_id AND a.from_location_id=OLD.location_id
            AND a.to_location_id=NEW.location_id) THEN
        NEW.updated_at=now(); RETURN NEW;
    END IF;
    IF ROW(NEW.kiz_id,NEW.kiz_code,NEW.product_id,
           NEW.origin_type,NEW.origin_reference,NEW.assigned_at,NEW.created_at,
           NEW.created_by,NEW.metadata)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id,OLD.kiz_code,OLD.product_id,
           OLD.origin_type,OLD.origin_reference,OLD.assigned_at,OLD.created_at,
           OLD.created_by,OLD.metadata)
       AND OLD.lifecycle_status='active' AND OLD.location_id IS NOT NULL
       AND OLD.container_id IS NULL AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status='shipped' AND NEW.location_id IS NULL
       AND NEW.container_id IS NULL AND NEW.closed_at IS NOT NULL
       AND EXISTS(SELECT 1 FROM wms.kiz_shipment_authorizations a
          WHERE a.transaction_id=txid_current() AND a.kiz_id=OLD.kiz_id
            AND a.product_id=OLD.product_id AND a.from_location_id=OLD.location_id) THEN
        NEW.updated_at=now(); RETURN NEW;
    END IF;
    IF ROW(NEW.kiz_id,NEW.kiz_code,NEW.product_id,
           NEW.origin_type,NEW.origin_reference,NEW.assigned_at,NEW.created_at,
           NEW.created_by,NEW.metadata,NEW.lifecycle_status,NEW.closed_at)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id,OLD.kiz_code,OLD.product_id,
           OLD.origin_type,OLD.origin_reference,OLD.assigned_at,OLD.created_at,
           OLD.created_by,OLD.metadata,OLD.lifecycle_status,OLD.closed_at)
       AND OLD.lifecycle_status='active'
       AND EXISTS(SELECT 1 FROM wms.kiz_container_holder_authorizations a
          WHERE a.transaction_id=txid_current() AND a.kiz_id=OLD.kiz_id
            AND a.product_id=OLD.product_id AND
            ((a.direction='fill' AND OLD.location_id=a.location_id AND OLD.container_id IS NULL
              AND NEW.location_id IS NULL AND NEW.container_id=a.container_id)
             OR
             (a.direction='extract' AND OLD.location_id IS NULL AND OLD.container_id=a.container_id
              AND NEW.location_id=a.location_id AND NEW.container_id IS NULL))) THEN
        NEW.updated_at=now(); RETURN NEW;
    END IF;
    IF ROW(NEW.kiz_id,NEW.kiz_code,NEW.product_id,NEW.location_id,NEW.container_id,
           NEW.origin_type,NEW.origin_reference,NEW.assigned_at,NEW.created_at,
           NEW.created_by,NEW.metadata)
       IS DISTINCT FROM
       ROW(OLD.kiz_id,OLD.kiz_code,OLD.product_id,OLD.location_id,OLD.container_id,
           OLD.origin_type,OLD.origin_reference,OLD.assigned_at,OLD.created_at,
           OLD.created_by,OLD.metadata)
       OR OLD.lifecycle_status<>'active' OR NEW.lifecycle_status NOT IN ('error','deactivated') THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Недопустимое изменение КИЗ';
    END IF;
    NEW.closed_at=now(); NEW.updated_at=now(); RETURN NEW;
END $$;

CREATE FUNCTION wms.transition_kiz_container_holder(
    p_operation_item_id bigint,p_kiz_id bigint,p_direction varchar
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,wms AS $$
DECLARE target record; current_kiz record;
BEGIN
    SELECT oi.operation_item_id,oi.product_id,oi.batch_number,oi.container_id,
           o.operation_type,o.result_payload,c.location_id
    INTO target FROM wms.container_operation_items oi
    JOIN wms.container_operations o USING(operation_id)
    JOIN wms.containers c ON c.container_id=oi.container_id
    WHERE oi.operation_item_id=p_operation_item_id FOR UPDATE OF oi;
    IF NOT FOUND OR target.result_payload IS NOT NULL OR target.batch_number IS NOT NULL
       OR (p_direction='fill' AND target.operation_type<>'fill')
       OR (p_direction='extract' AND target.operation_type NOT IN ('extract','unpack_all')) THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Недопустимый container KIZ transition protocol';
    END IF;
    SELECT kiz_id,product_id,location_id,container_id,lifecycle_status,closed_at
    INTO current_kiz FROM wms.kiz WHERE kiz_id=p_kiz_id FOR UPDATE;
    IF NOT FOUND OR current_kiz.lifecycle_status<>'active' OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id<>target.product_id
       OR (p_direction='fill' AND (current_kiz.location_id<>target.location_id OR current_kiz.container_id IS NOT NULL))
       OR (p_direction='extract' AND (current_kiz.location_id IS NOT NULL OR current_kiz.container_id<>target.container_id)) THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='КИЗ holder изменился до container operation';
    END IF;
    INSERT INTO wms.kiz_container_holder_authorizations
      VALUES(txid_current(),p_operation_item_id,p_kiz_id,p_direction,target.product_id,target.location_id,target.container_id);
    IF p_direction='fill' THEN
      UPDATE wms.kiz SET location_id=NULL,container_id=target.container_id WHERE kiz_id=p_kiz_id;
    ELSE
      UPDATE wms.kiz SET location_id=target.location_id,container_id=NULL WHERE kiz_id=p_kiz_id;
    END IF;
END $$;
REVOKE ALL ON FUNCTION wms.transition_kiz_container_holder(bigint,bigint,varchar) FROM PUBLIC;

CREATE FUNCTION wms.require_complete_kiz_container_holder() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,wms AS $$
BEGIN
 IF NOT EXISTS(
   SELECT 1 FROM wms.container_operation_items oi
   JOIN wms.container_operations o USING(operation_id)
   JOIN wms.kiz k ON k.kiz_id=NEW.kiz_id
   JOIN wms.kiz_movement_links lo ON lo.kiz_id=NEW.kiz_id AND lo.movement_ref=oi.outgoing_movement_ref
   JOIN wms.kiz_movement_links li ON li.kiz_id=NEW.kiz_id AND li.movement_ref=oi.incoming_movement_ref
   WHERE oi.operation_item_id=NEW.operation_item_id AND o.result_payload IS NOT NULL
     AND oi.batch_number IS NULL AND oi.product_id=NEW.product_id
     AND ((NEW.direction='fill' AND o.operation_type='fill' AND k.location_id IS NULL AND k.container_id=NEW.container_id)
       OR (NEW.direction='extract' AND o.operation_type IN ('extract','unpack_all') AND k.location_id=NEW.location_id AND k.container_id IS NULL))
 ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Incomplete controlled container KIZ transition'; END IF;
 DELETE FROM wms.kiz_container_holder_authorizations
 WHERE transaction_id=NEW.transaction_id AND kiz_id=NEW.kiz_id;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_complete_kiz_container_holder
AFTER INSERT ON wms.kiz_container_holder_authorizations DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_container_holder();

CREATE OR REPLACE FUNCTION wms.guard_kiz_inventory() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,wms AS $$
DECLARE identified bigint; remaining numeric; holder_container_id bigint; authorized_operation bigint;
BEGIN
 IF OLD.status<>'available' OR OLD.batch_number IS NOT NULL THEN
   IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END IF;
 remaining=0;
 IF TG_OP='UPDATE' AND ROW(OLD.product_id,OLD.location_id,OLD.status,OLD.batch_number,OLD.container_code)
    IS NOT DISTINCT FROM ROW(NEW.product_id,NEW.location_id,NEW.status,NEW.batch_number,NEW.container_code) THEN
   IF NEW.quantity>=OLD.quantity THEN RETURN NEW; END IF; remaining=NEW.quantity;
 END IF;
 IF OLD.container_code IS NULL THEN
   SELECT count(*) INTO identified FROM wms.kiz WHERE product_id=OLD.product_id
     AND location_id=OLD.location_id AND container_id IS NULL AND lifecycle_status='active';
 ELSE
   SELECT container_id INTO holder_container_id FROM wms.containers WHERE qr_code=OLD.container_code;
   authorized_operation=NULLIF(current_setting('wms.kiz_container_operation_id',true),'')::bigint;
   IF authorized_operation IS NOT NULL AND EXISTS(
      SELECT 1 FROM wms.container_operations o WHERE o.operation_id=authorized_operation
        AND o.operation_type='move' AND o.container_id=holder_container_id AND o.result_payload IS NULL
   ) THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
   SELECT count(*) INTO identified FROM wms.kiz WHERE product_id=OLD.product_id
     AND container_id=holder_container_id AND location_id IS NULL AND lifecycle_status='active';
 END IF;
 IF identified>0 AND remaining<identified THEN
   RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Недостаточно неидентифицированного остатка',
     DETAIL=jsonb_build_object('product_id',OLD.product_id,'location_id',OLD.location_id,
       'container_code',OLD.container_code,'physical_quantity',OLD.quantity,
       'identified_quantity',identified,'requested_remaining',remaining)::text;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;

CREATE FUNCTION wms.require_kiz_final_holder_integrity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,wms AS $$
DECLARE physical numeric; contents numeric; identified bigint; holder record;
BEGIN
 IF NEW.lifecycle_status<>'active' THEN RETURN NULL; END IF;
 IF NEW.container_id IS NULL THEN
   SELECT count(*) INTO identified FROM wms.kiz WHERE lifecycle_status='active'
     AND product_id=NEW.product_id AND location_id=NEW.location_id AND container_id IS NULL;
   SELECT COALESCE(i.quantity,0) INTO physical FROM (SELECT 1) seed LEFT JOIN wms.inventory i
     ON i.product_id=NEW.product_id AND i.location_id=NEW.location_id AND i.status='available'
    AND i.batch_number IS NULL AND i.container_code IS NULL;
   IF identified>physical THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Final loose KIZ quantity exceeds physical inventory'; END IF;
 ELSE
   SELECT c.location_id,c.qr_code INTO holder FROM wms.containers c WHERE c.container_id=NEW.container_id;
   IF NOT FOUND OR holder.location_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Contained KIZ holder has no valid location'; END IF;
   SELECT count(*) INTO identified FROM wms.kiz WHERE lifecycle_status='active'
     AND product_id=NEW.product_id AND container_id=NEW.container_id AND location_id IS NULL;
   SELECT COALESCE(i.quantity,0) INTO physical FROM (SELECT 1) seed LEFT JOIN wms.inventory i
     ON i.product_id=NEW.product_id AND i.location_id=holder.location_id AND i.status='available'
    AND i.batch_number IS NULL AND i.container_code=holder.qr_code;
   SELECT COALESCE(cc.quantity,0) INTO contents FROM (SELECT 1) seed LEFT JOIN wms.container_contents cc
     ON cc.container_id=NEW.container_id AND cc.product_id=NEW.product_id
    AND cc.batch_number IS NULL AND cc.status='active';
   IF identified>physical OR identified>contents THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Final contained KIZ quantity exceeds physical projection'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_kiz_final_holder_integrity
AFTER INSERT OR UPDATE ON wms.kiz DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_kiz_final_holder_integrity();

CREATE FUNCTION wms.check_kiz_holder_integrity()
RETURNS TABLE(violation_type text,product_id varchar,location_id bigint,location_code varchar,
 container_id bigint,container_qr_code varchar,physical_quantity numeric,identified_quantity bigint,
 difference numeric,inventory_missing boolean)
LANGUAGE sql STABLE SET search_path=pg_catalog,wms AS $$
 WITH loose AS (
  SELECT k.product_id,k.location_id,count(*) identified FROM wms.kiz k
  WHERE k.lifecycle_status='active' AND k.location_id IS NOT NULL AND k.container_id IS NULL GROUP BY 1,2
 ), contained AS (
  SELECT k.product_id,k.container_id,count(*) identified FROM wms.kiz k
  WHERE k.lifecycle_status='active' AND k.container_id IS NOT NULL AND k.location_id IS NULL GROUP BY 1,2
 )
 SELECT 'holder_shape',k.product_id,k.location_id,l.location_code,k.container_id,c.qr_code,0,count(*)::bigint,count(*)::numeric,true
 FROM wms.kiz k LEFT JOIN wms.locations l ON l.location_id=k.location_id LEFT JOIN wms.containers c ON c.container_id=k.container_id
 WHERE (k.lifecycle_status='active' AND ((k.location_id IS NOT NULL)::int+(k.container_id IS NOT NULL)::int)<>1)
    OR (k.lifecycle_status='shipped' AND (k.location_id IS NOT NULL OR k.container_id IS NOT NULL))
 GROUP BY k.product_id,k.location_id,l.location_code,k.container_id,c.qr_code
 UNION ALL
 SELECT 'loose_quantity',x.product_id,x.location_id,l.location_code,NULL,NULL,COALESCE(i.quantity,0),x.identified,x.identified-COALESCE(i.quantity,0),i.inventory_id IS NULL
 FROM loose x JOIN wms.locations l USING(location_id) LEFT JOIN wms.inventory i ON i.product_id=x.product_id AND i.location_id=x.location_id AND i.status='available' AND i.batch_number IS NULL AND i.container_code IS NULL
 WHERE x.identified>COALESCE(i.quantity,0)
 UNION ALL
 SELECT 'container_inventory_quantity',x.product_id,NULL,NULL,x.container_id,c.qr_code,COALESCE(i.quantity,0),x.identified,x.identified-COALESCE(i.quantity,0),i.inventory_id IS NULL
 FROM contained x LEFT JOIN wms.containers c USING(container_id) LEFT JOIN wms.inventory i ON i.product_id=x.product_id AND i.location_id=c.location_id AND i.status='available' AND i.batch_number IS NULL AND i.container_code=c.qr_code
 WHERE c.container_id IS NULL OR c.location_id IS NULL OR x.identified>COALESCE(i.quantity,0)
 UNION ALL
 SELECT 'container_contents_quantity',x.product_id,NULL,NULL,x.container_id,c.qr_code,COALESCE(cc.quantity,0),x.identified,x.identified-COALESCE(cc.quantity,0),cc.content_id IS NULL
 FROM contained x LEFT JOIN wms.containers c USING(container_id) LEFT JOIN wms.container_contents cc ON cc.container_id=x.container_id AND cc.product_id=x.product_id AND cc.batch_number IS NULL AND cc.status='active'
 WHERE x.identified>COALESCE(cc.quantity,0)
 UNION ALL
 SELECT 'unsupported_kiz_batch',m.product_id,NULL,NULL,oi.container_id,c.qr_code,m.quantity,count(DISTINCT l.kiz_id),0,false
 FROM wms.kiz_movement_links l JOIN wms.movement_registry r USING(movement_ref)
 JOIN wms.movements m ON m.movement_id=r.movement_id AND m.created_at=r.movement_created_at
 JOIN wms.container_operation_items oi ON oi.operation_item_id=m.source_item_id
 JOIN wms.containers c ON c.container_id=oi.container_id
 WHERE m.source_type='container_operation' AND m.batch_number IS NOT NULL
 GROUP BY m.product_id,oi.container_id,c.qr_code,m.quantity
 ORDER BY 1,2 NULLS LAST,3 NULLS LAST,5 NULLS LAST
$$;

COMMENT ON COLUMN wms.kiz.container_id IS 'Direct container holder. Active KIZ has exact XOR location_id/container_id.';
COMMENT ON FUNCTION wms.transition_kiz_container_holder(bigint,bigint,varchar) IS 'Controlled fill/extract holder transition; commit requires paired movement links and saved container result.';
COMMENT ON FUNCTION wms.check_kiz_holder_integrity() IS 'Read-only final KIZ holder/physical consistency audit for loose and container scopes.';

COMMIT;
