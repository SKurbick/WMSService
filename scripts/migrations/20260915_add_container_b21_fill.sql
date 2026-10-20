-- Stage 3B Phase B2.1: idempotent loose-to-container fill protocol.
-- Schema/protocol only: this migration performs no business fill and changes no stock.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE wms.container_operations (
    operation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_type varchar(32) NOT NULL,
    source_system varchar(100) NOT NULL,
    external_operation_id varchar(200) NOT NULL,
    request_fingerprint varchar(64) NOT NULL,
    author varchar(100) NOT NULL,
    result_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_container_operations_type CHECK (operation_type='fill'),
    CONSTRAINT chk_container_operations_source CHECK (btrim(source_system)<>''),
    CONSTRAINT chk_container_operations_external_id
        CHECK (btrim(external_operation_id)<>''),
    CONSTRAINT chk_container_operations_fingerprint
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_container_operations_author CHECK (btrim(author)<>''),
    CONSTRAINT chk_container_operations_result
        CHECK (result_payload IS NULL OR jsonb_typeof(result_payload)='object'),
    CONSTRAINT uq_container_operations_external_identity
        UNIQUE (source_system,operation_type,external_operation_id)
);

CREATE TABLE wms.container_operation_items (
    operation_item_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id bigint NOT NULL,
    external_line_id varchar(200) NOT NULL,
    container_id bigint NOT NULL,
    product_id varchar(50) NOT NULL,
    batch_number varchar(50),
    quantity numeric(10,2) NOT NULL,
    outgoing_movement_ref bigint,
    incoming_movement_ref bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_container_operation_items_line
        CHECK (btrim(external_line_id)<>''),
    CONSTRAINT chk_container_operation_items_quantity CHECK (quantity>0),
    CONSTRAINT fk_container_operation_items_operation
        FOREIGN KEY (operation_id) REFERENCES wms.container_operations(operation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_container_operation_items_container
        FOREIGN KEY (container_id) REFERENCES wms.containers(container_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_container_operation_items_product
        FOREIGN KEY (product_id) REFERENCES public.products(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_container_operation_items_outgoing
        FOREIGN KEY (outgoing_movement_ref)
        REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_container_operation_items_incoming
        FOREIGN KEY (incoming_movement_ref)
        REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT uq_container_operation_items_line
        UNIQUE (operation_id,external_line_id),
    CONSTRAINT uq_container_operation_items_outgoing UNIQUE (outgoing_movement_ref),
    CONSTRAINT uq_container_operation_items_incoming UNIQUE (incoming_movement_ref),
    CONSTRAINT chk_container_operation_items_refs
        CHECK (
            (outgoing_movement_ref IS NULL AND incoming_movement_ref IS NULL)
            OR
            (outgoing_movement_ref IS NOT NULL AND incoming_movement_ref IS NOT NULL)
        )
);

CREATE FUNCTION wms.guard_container_operation_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
BEGIN
    IF TG_OP='DELETE'
       OR ROW(NEW.operation_id,NEW.operation_type,NEW.source_system,
              NEW.external_operation_id,NEW.request_fingerprint,NEW.author,
              NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_id,OLD.operation_type,OLD.source_system,
              OLD.external_operation_id,OLD.request_fingerprint,OLD.author,
              OLD.created_at)
       OR OLD.result_payload IS NOT NULL
       OR NEW.result_payload IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='55000',
            MESSAGE='Container operation identity/result is immutable';
    END IF;
    NEW.updated_at:=now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_container_operations_guard
BEFORE UPDATE OR DELETE ON wms.container_operations
FOR EACH ROW EXECUTE FUNCTION wms.guard_container_operation_update();

CREATE FUNCTION wms.guard_container_operation_item_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
BEGIN
    IF TG_OP='DELETE'
       OR ROW(NEW.operation_item_id,NEW.operation_id,NEW.external_line_id,
              NEW.container_id,NEW.product_id,NEW.batch_number,NEW.quantity,
              NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_item_id,OLD.operation_id,OLD.external_line_id,
              OLD.container_id,OLD.product_id,OLD.batch_number,OLD.quantity,
              OLD.created_at)
       OR OLD.outgoing_movement_ref IS NOT NULL
       OR OLD.incoming_movement_ref IS NOT NULL
       OR NEW.outgoing_movement_ref IS NULL
       OR NEW.incoming_movement_ref IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='55000',
            MESSAGE='Container operation item identity/movement links are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_container_operation_items_guard
BEFORE UPDATE OR DELETE ON wms.container_operation_items
FOR EACH ROW EXECUTE FUNCTION wms.guard_container_operation_item_update();

CREATE FUNCTION wms.require_complete_container_operation() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
DECLARE
    target_operation_id bigint;
BEGIN
    target_operation_id:=COALESCE(NEW.operation_id,OLD.operation_id);
    IF EXISTS (
        SELECT 1 FROM wms.container_operations o
        WHERE o.operation_id=target_operation_id
          AND (
              o.result_payload IS NULL
              OR NOT EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
              )
              OR EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
                    AND (
                        oi.outgoing_movement_ref IS NULL
                        OR oi.incoming_movement_ref IS NULL
                    )
              )
              OR EXISTS (
                  SELECT 1
                  FROM wms.container_operation_items oi
                  JOIN wms.containers c ON c.container_id=oi.container_id
                  LEFT JOIN wms.movement_registry outgoing_registry
                    ON outgoing_registry.movement_ref=oi.outgoing_movement_ref
                  LEFT JOIN wms.movements outgoing
                    ON outgoing.movement_id=outgoing_registry.movement_id
                   AND outgoing.created_at=outgoing_registry.movement_created_at
                  LEFT JOIN wms.movement_registry incoming_registry
                    ON incoming_registry.movement_ref=oi.incoming_movement_ref
                  LEFT JOIN wms.movements incoming
                    ON incoming.movement_id=incoming_registry.movement_id
                   AND incoming.created_at=incoming_registry.movement_created_at
                  WHERE oi.operation_id=o.operation_id
                    AND (
                        outgoing.movement_type IS DISTINCT FROM 'transfer'
                        OR outgoing.product_id IS DISTINCT FROM oi.product_id
                        OR outgoing.batch_number IS DISTINCT FROM oi.batch_number
                        OR outgoing.quantity IS DISTINCT FROM oi.quantity
                        OR outgoing.from_location_id IS DISTINCT FROM c.location_id
                        OR outgoing.to_location_id IS NOT NULL
                        OR outgoing.container_code IS NOT NULL
                        OR outgoing.source_type IS DISTINCT FROM 'container_operation'
                        OR outgoing.source_id IS DISTINCT FROM oi.operation_id
                        OR outgoing.source_item_id IS DISTINCT FROM oi.operation_item_id
                        OR incoming.movement_type IS DISTINCT FROM 'transfer'
                        OR incoming.product_id IS DISTINCT FROM oi.product_id
                        OR incoming.batch_number IS DISTINCT FROM oi.batch_number
                        OR incoming.quantity IS DISTINCT FROM oi.quantity
                        OR incoming.from_location_id IS NOT NULL
                        OR incoming.to_location_id IS DISTINCT FROM c.location_id
                        OR incoming.container_code IS DISTINCT FROM c.qr_code
                        OR incoming.source_type IS DISTINCT FROM 'container_operation'
                        OR incoming.source_id IS DISTINCT FROM oi.operation_id
                        OR incoming.source_item_id IS DISTINCT FROM oi.operation_item_id
                    )
              )
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container operation graph must be complete at commit';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_container_operations_complete
AFTER INSERT OR UPDATE OF result_payload ON wms.container_operations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_container_operation();

CREATE CONSTRAINT TRIGGER trg_container_operation_items_complete
AFTER INSERT OR UPDATE OF outgoing_movement_ref,incoming_movement_ref
ON wms.container_operation_items
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_container_operation();

CREATE OR REPLACE FUNCTION wms.sync_container_to_inventory() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
DECLARE
    location bigint;
    code varchar(100);
    fill_item_id bigint;
BEGIN
    IF NEW.status<>'active' THEN
        RETURN NEW;
    END IF;

    fill_item_id:=NULLIF(
        current_setting('wms.container_fill_item_id',true),''
    )::bigint;
    IF fill_item_id IS NOT NULL AND EXISTS (
        SELECT 1
        FROM wms.container_operation_items oi
        JOIN wms.container_operations o ON o.operation_id=oi.operation_id
        WHERE oi.operation_item_id=fill_item_id
          AND oi.container_id=NEW.container_id
          AND oi.product_id=NEW.product_id
          AND oi.batch_number IS NOT DISTINCT FROM NEW.batch_number
          AND oi.quantity=NEW.quantity
          AND o.operation_type='fill'
          AND o.result_payload IS NULL
    ) THEN
        RETURN NEW;
    END IF;

    SELECT c.location_id,c.qr_code INTO location,code
    FROM wms.containers c WHERE c.container_id=NEW.container_id;
    IF location IS NULL THEN
        RAISE EXCEPTION 'Container % has no location assigned',NEW.container_id;
    END IF;

    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'receive',NEW.product_id,NULL,location,NEW.quantity,
        NEW.batch_number,code,'Container registered'
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION wms.unpack_from_container(
    p_qr_code varchar,
    p_product_id varchar,
    p_quantity numeric
) RETURNS TABLE(success boolean,remaining_in_container numeric,loose_quantity numeric)
LANGUAGE plpgsql
AS $$
DECLARE
    v_container_id bigint;
    v_location_id bigint;
    v_current_qty numeric;
    v_batch_number varchar(50);
BEGIN
    SELECT container_id,location_id INTO v_container_id,v_location_id
    FROM wms.containers WHERE qr_code=p_qr_code
    FOR UPDATE;
    IF v_container_id IS NULL THEN
        RAISE EXCEPTION 'Container % not found',p_qr_code;
    END IF;

    SELECT quantity,batch_number INTO v_current_qty,v_batch_number
    FROM wms.container_contents
    WHERE container_id=v_container_id
      AND product_id=p_product_id AND status='active';
    IF v_current_qty IS NULL OR v_current_qty<p_quantity THEN
        RAISE EXCEPTION
            'Not enough quantity in container. Available: %, requested: %',
            COALESCE(v_current_qty,0),p_quantity;
    END IF;

    UPDATE wms.container_contents
    SET quantity=v_current_qty-p_quantity,updated_at=now()
    WHERE container_id=v_container_id
      AND product_id=p_product_id AND status='active';
    UPDATE wms.container_contents SET status='removed'
    WHERE container_id=v_container_id
      AND product_id=p_product_id AND quantity=0 AND status='active';

    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'unpack',p_product_id,v_location_id,NULL,p_quantity,
        v_batch_number,p_qr_code,'Unpacked from container'
    );
    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'unpack',p_product_id,NULL,v_location_id,p_quantity,
        v_batch_number,NULL,'Unpacked from '||p_qr_code
    );
    UPDATE wms.containers SET status='open'
    WHERE container_id=v_container_id AND status='sealed';
    RETURN QUERY SELECT true,COALESCE(v_current_qty-p_quantity,0),p_quantity;
END;
$$;

COMMENT ON TABLE wms.container_operations IS
'Idempotency/result boundary for ordinary container physical operations.';
COMMENT ON TABLE wms.container_operation_items IS
'Immutable fill lines linked to outgoing loose and incoming contained movements.';
COMMENT ON COLUMN wms.container_operation_items.outgoing_movement_ref IS
'Stable registry ref of loose outgoing transfer movement.';
COMMENT ON COLUMN wms.container_operation_items.incoming_movement_ref IS
'Stable registry ref of contained incoming transfer movement.';

COMMIT;
