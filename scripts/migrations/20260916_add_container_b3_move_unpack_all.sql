-- Stage 3B Phase B3: controlled move and unpack-all protocols.
-- Schema/protocol only: no container is moved and no physical quantity is changed.
BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE wms.container_operations, wms.container_operation_items
IN SHARE ROW EXCLUSIVE MODE;

-- The B2 guard correctly forbids application-level identity changes. B3 must
-- backfill its new structural identity while both operation tables are locked.
DROP TRIGGER trg_container_operations_guard ON wms.container_operations;

ALTER TABLE wms.container_operations ADD COLUMN container_id bigint;
UPDATE wms.container_operations o
SET container_id=(
    SELECT min(oi.container_id)
    FROM wms.container_operation_items oi
    WHERE oi.operation_id=o.operation_id
);
ALTER TABLE wms.container_operations
    ALTER COLUMN container_id SET NOT NULL,
    ADD CONSTRAINT fk_container_operations_container
        FOREIGN KEY (container_id) REFERENCES wms.containers(container_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    DROP CONSTRAINT chk_container_operations_type,
    ADD CONSTRAINT chk_container_operations_type
        CHECK (operation_type IN ('fill','extract','move','unpack_all'));

ALTER TABLE wms.container_operation_items
    ADD COLUMN movement_ref bigint,
    ADD CONSTRAINT fk_container_operation_items_movement
        FOREIGN KEY (movement_ref) REFERENCES wms.movement_registry(movement_ref)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT uq_container_operation_items_movement UNIQUE (movement_ref),
    DROP CONSTRAINT chk_container_operation_items_refs,
    ADD CONSTRAINT chk_container_operation_items_refs CHECK (
        (movement_ref IS NULL AND outgoing_movement_ref IS NULL AND incoming_movement_ref IS NULL)
        OR (movement_ref IS NOT NULL AND outgoing_movement_ref IS NULL AND incoming_movement_ref IS NULL)
        OR (movement_ref IS NULL AND outgoing_movement_ref IS NOT NULL AND incoming_movement_ref IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION wms.guard_container_operation_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
BEGIN
    IF TG_OP='DELETE'
       OR ROW(NEW.operation_id,NEW.operation_type,NEW.container_id,NEW.source_system,
              NEW.external_operation_id,NEW.request_fingerprint,NEW.author,NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_id,OLD.operation_type,OLD.container_id,OLD.source_system,
              OLD.external_operation_id,OLD.request_fingerprint,OLD.author,OLD.created_at)
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

CREATE OR REPLACE FUNCTION wms.guard_container_operation_item_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
BEGIN
    IF TG_OP='DELETE'
       OR ROW(NEW.operation_item_id,NEW.operation_id,NEW.external_line_id,
              NEW.container_id,NEW.product_id,NEW.batch_number,NEW.quantity,NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.operation_item_id,OLD.operation_id,OLD.external_line_id,
              OLD.container_id,OLD.product_id,OLD.batch_number,OLD.quantity,OLD.created_at)
       OR OLD.movement_ref IS NOT NULL
       OR OLD.outgoing_movement_ref IS NOT NULL
       OR OLD.incoming_movement_ref IS NOT NULL
       OR NOT (
           (NEW.movement_ref IS NOT NULL AND NEW.outgoing_movement_ref IS NULL
             AND NEW.incoming_movement_ref IS NULL)
           OR
           (NEW.movement_ref IS NULL AND NEW.outgoing_movement_ref IS NOT NULL
             AND NEW.incoming_movement_ref IS NOT NULL)
       ) THEN
        RAISE EXCEPTION USING ERRCODE='55000',
            MESSAGE='Container operation item identity/movement links are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION wms.apply_container_extract_content(
    p_operation_item_id bigint
) RETURNS numeric
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
DECLARE
    target record;
    current_quantity numeric(10,2);
    contained_quantity numeric(10,2);
    remaining_quantity numeric(10,2);
BEGIN
    SELECT oi.container_id,oi.product_id,oi.batch_number,oi.quantity,
           c.location_id,c.qr_code
    INTO target
    FROM wms.container_operation_items oi
    JOIN wms.container_operations o ON o.operation_id=oi.operation_id
    JOIN wms.containers c ON c.container_id=oi.container_id
    WHERE oi.operation_item_id=p_operation_item_id
      AND o.operation_type IN ('extract','unpack_all')
      AND o.result_payload IS NULL
      AND oi.movement_ref IS NULL
      AND oi.outgoing_movement_ref IS NOT NULL
      AND oi.incoming_movement_ref IS NOT NULL
    FOR UPDATE OF oi;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='55000',
            MESSAGE='Container extract item is not ready for contents mutation';
    END IF;
    SELECT cc.quantity INTO current_quantity
    FROM wms.container_contents cc
    WHERE cc.container_id=target.container_id
      AND cc.product_id=target.product_id
      AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
      AND cc.status='active'
    FOR UPDATE;
    IF current_quantity IS NULL OR current_quantity<target.quantity THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container extract content scope changed or is insufficient';
    END IF;
    SELECT COALESCE(i.quantity,0) INTO contained_quantity
    FROM (SELECT 1) marker
    LEFT JOIN wms.inventory i
      ON i.product_id=target.product_id
     AND i.location_id=target.location_id
     AND i.status='available'
     AND i.batch_number IS NOT DISTINCT FROM target.batch_number
     AND i.container_code=target.qr_code;
    IF current_quantity-contained_quantity IS DISTINCT FROM target.quantity THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container extract movements/content delta is invalid';
    END IF;
    remaining_quantity:=current_quantity-target.quantity;
    IF remaining_quantity=0 THEN
        DELETE FROM wms.container_contents cc
        WHERE cc.container_id=target.container_id
          AND cc.product_id=target.product_id
          AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
          AND cc.status='active';
    ELSE
        UPDATE wms.container_contents cc
        SET quantity=remaining_quantity,updated_at=now()
        WHERE cc.container_id=target.container_id
          AND cc.product_id=target.product_id
          AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
          AND cc.status='active';
    END IF;
    RETURN remaining_quantity;
END;
$$;

CREATE OR REPLACE FUNCTION wms.move_container_inventory() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
DECLARE
    controlled_operation_id bigint;
BEGIN
    IF OLD.location_id IS DISTINCT FROM NEW.location_id THEN
        controlled_operation_id:=NULLIF(
            current_setting('wms.container_move_operation_id',true),'')::bigint;
        IF controlled_operation_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM wms.container_operations o
                WHERE o.operation_id=controlled_operation_id
                  AND o.operation_type='move'
                  AND o.container_id=NEW.container_id
                  AND o.result_payload IS NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='Invalid controlled container move authorization';
            END IF;
            RETURN NEW;
        END IF;
        INSERT INTO wms.movements(
            movement_type,product_id,from_location_id,to_location_id,quantity,
            batch_number,container_code,reason
        )
        SELECT 'transfer',i.product_id,OLD.location_id,NEW.location_id,i.quantity,
               i.batch_number,NEW.qr_code,'Container moved'
        FROM wms.inventory i
        WHERE i.container_code=NEW.qr_code;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION wms.require_complete_container_operation() RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,wms
AS $$
DECLARE
    target_operation_id bigint;
BEGIN
    target_operation_id:=COALESCE(NEW.operation_id,OLD.operation_id);
    IF EXISTS (
        SELECT 1
        FROM wms.container_operations o
        JOIN wms.containers c ON c.container_id=o.container_id
        WHERE o.operation_id=target_operation_id
          AND (
              o.result_payload IS NULL
              OR (o.result_payload->>'operation_id')::bigint IS DISTINCT FROM o.operation_id
              OR (o.result_payload->>'container_id')::bigint IS DISTINCT FROM o.container_id
              OR EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id AND oi.container_id<>o.container_id
              )
              OR (o.operation_type IN ('fill','extract','unpack_all') AND NOT EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
              ))
              OR EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
                    AND (
                        (o.operation_type='move' AND (
                            oi.movement_ref IS NULL OR oi.outgoing_movement_ref IS NOT NULL
                            OR oi.incoming_movement_ref IS NOT NULL
                        ))
                        OR (o.operation_type<>'move' AND (
                            oi.movement_ref IS NOT NULL OR oi.outgoing_movement_ref IS NULL
                            OR oi.incoming_movement_ref IS NULL
                        ))
                    )
              )
              OR EXISTS (
                  SELECT 1
                  FROM wms.container_operation_items oi
                  LEFT JOIN wms.movement_registry single_registry
                    ON single_registry.movement_ref=oi.movement_ref
                  LEFT JOIN wms.movements single_movement
                    ON single_movement.movement_id=single_registry.movement_id
                   AND single_movement.created_at=single_registry.movement_created_at
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
                        (o.operation_type='move' AND (
                            single_movement.movement_type IS DISTINCT FROM 'transfer'
                            OR single_movement.product_id IS DISTINCT FROM oi.product_id
                            OR single_movement.batch_number IS DISTINCT FROM oi.batch_number
                            OR single_movement.quantity IS DISTINCT FROM oi.quantity
                            OR single_movement.container_code IS DISTINCT FROM c.qr_code
                            OR single_movement.source_type IS DISTINCT FROM 'container_operation'
                            OR single_movement.source_id IS DISTINCT FROM oi.operation_id
                            OR single_movement.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR single_movement.from_location_id IS DISTINCT FROM (
                                SELECT location_id FROM wms.locations
                                WHERE location_code=o.result_payload->>'from_location_code'
                            )
                            OR single_movement.to_location_id IS DISTINCT FROM c.location_id
                        ))
                        OR (o.operation_type<>'move' AND (
                            outgoing.movement_type IS DISTINCT FROM 'transfer'
                            OR outgoing.product_id IS DISTINCT FROM oi.product_id
                            OR outgoing.batch_number IS DISTINCT FROM oi.batch_number
                            OR outgoing.quantity IS DISTINCT FROM oi.quantity
                            OR outgoing.source_type IS DISTINCT FROM 'container_operation'
                            OR outgoing.source_id IS DISTINCT FROM oi.operation_id
                            OR outgoing.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR incoming.movement_type IS DISTINCT FROM 'transfer'
                            OR incoming.product_id IS DISTINCT FROM oi.product_id
                            OR incoming.batch_number IS DISTINCT FROM oi.batch_number
                            OR incoming.quantity IS DISTINCT FROM oi.quantity
                            OR incoming.source_type IS DISTINCT FROM 'container_operation'
                            OR incoming.source_id IS DISTINCT FROM oi.operation_id
                            OR incoming.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR (o.operation_type='fill' AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS NOT NULL
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS DISTINCT FROM c.qr_code
                            ))
                            OR (o.operation_type IN ('extract','unpack_all') AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS DISTINCT FROM c.qr_code
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS NOT NULL
                            ))
                        ))
                    )
              )
              OR (o.operation_type='move' AND (
                  c.location_id IS DISTINCT FROM (
                      SELECT location_id FROM wms.locations
                      WHERE location_code=o.result_payload->>'to_location_code'
                  )
                  OR c.status IS DISTINCT FROM o.result_payload->>'container_status'
              ))
              OR (o.operation_type='unpack_all' AND (
                  c.status<>'empty'
                  OR EXISTS (SELECT 1 FROM wms.container_contents cc
                             WHERE cc.container_id=c.container_id AND cc.status='active')
                  OR EXISTS (SELECT 1 FROM wms.inventory i
                             WHERE i.container_code=c.qr_code)
              ))
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container operation graph must be complete at commit';
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER trg_container_operation_items_complete ON wms.container_operation_items;
CREATE CONSTRAINT TRIGGER trg_container_operation_items_complete
AFTER INSERT OR UPDATE OF movement_ref,outgoing_movement_ref,incoming_movement_ref
ON wms.container_operation_items
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION wms.require_complete_container_operation();

COMMENT ON TABLE wms.container_operations IS
'Idempotency/result boundary for ordinary container fill, extract, move and unpack-all operations.';
COMMENT ON COLUMN wms.container_operations.container_id IS
'Immutable target container identity; supports itemless empty/no-op move operations.';
COMMENT ON TABLE wms.container_operation_items IS
'Immutable physical scope snapshot linked to either one move or paired fill/extract/unpack-all movements.';
COMMENT ON COLUMN wms.container_operation_items.movement_ref IS
'Stable registry ref of the single location-to-location contained move.';
COMMENT ON FUNCTION wms.move_container_inventory() IS
'Legacy container move projection; skips duplicate projection only for an authorized unfinished B3 move.';
COMMENT ON FUNCTION wms.apply_container_extract_content(bigint) IS
'Controlled current-content mutation for one unfinished extract or unpack-all item.';

COMMIT;
