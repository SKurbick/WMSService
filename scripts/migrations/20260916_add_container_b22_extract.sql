-- Stage 3B Phase B2.2: idempotent controlled container-to-loose extract.
-- Schema/protocol only: this migration performs no business extract and changes no stock.
BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE wms.container_operations, wms.container_operation_items
IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE wms.container_operations
    DROP CONSTRAINT chk_container_operations_type,
    ADD CONSTRAINT chk_container_operations_type
        CHECK (operation_type IN ('fill','extract'));

CREATE FUNCTION wms.apply_container_extract_content(
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
      AND o.operation_type='extract'
      AND o.result_payload IS NULL
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

CREATE OR REPLACE FUNCTION wms.require_complete_container_operation() RETURNS trigger
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
                        OR (
                            o.operation_type='fill'
                            AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS NOT NULL
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS DISTINCT FROM c.qr_code
                            )
                        )
                        OR (
                            o.operation_type='extract'
                            AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS DISTINCT FROM c.qr_code
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS NOT NULL
                            )
                        )
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

COMMENT ON FUNCTION wms.apply_container_extract_content(bigint) IS
'Controlled partial/full current-content mutation for one unfinished extract item.';
COMMENT ON TABLE wms.container_operations IS
'Idempotency/result boundary for ordinary container fill and extract operations.';
COMMENT ON TABLE wms.container_operation_items IS
'Immutable fill/extract lines linked to paired physical transfer movements.';
COMMENT ON COLUMN wms.container_operation_items.outgoing_movement_ref IS
'Fill: loose outgoing; extract: contained outgoing stable movement registry ref.';
COMMENT ON COLUMN wms.container_operation_items.incoming_movement_ref IS
'Fill: contained incoming; extract: loose incoming stable movement registry ref.';

COMMIT;
