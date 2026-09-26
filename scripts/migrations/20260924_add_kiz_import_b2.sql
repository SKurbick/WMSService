-- Controlled business apply сохранённых KIZ import messages.
-- Применять после B1 migration и до выкладки B2-кода.

BEGIN;

ALTER TABLE wms.kiz
    DROP CONSTRAINT kiz_origin_type_check;

ALTER TABLE wms.kiz
    ADD CONSTRAINT kiz_origin_type_check
    CHECK (origin_type IN ('warehouse_assignment', 'receipt_import'));

ALTER TABLE wms.kiz_import_messages
    ADD COLUMN business_status varchar(20) NOT NULL DEFAULT 'pending',
    ADD COLUMN processed_at timestamptz,
    ADD COLUMN business_error_code varchar(100),
    ADD COLUMN business_error_message text,
    ADD COLUMN business_result jsonb;

ALTER TABLE wms.kiz_import_messages
    ADD CONSTRAINT chk_kiz_import_messages_business_status
        CHECK (business_status IN ('pending', 'applied', 'rejected')),
    ADD CONSTRAINT chk_kiz_import_messages_business_result
        CHECK (business_result IS NULL OR jsonb_typeof(business_result) = 'object'),
    ADD CONSTRAINT chk_kiz_import_messages_business_state
        CHECK (
            (
                business_status = 'pending'
                AND processed_at IS NULL
                AND business_error_code IS NULL
                AND business_error_message IS NULL
                AND business_result IS NULL
            )
            OR (
                business_status = 'applied'
                AND processed_at IS NOT NULL
                AND business_error_code IS NULL
                AND business_error_message IS NULL
                AND business_result IS NOT NULL
            )
            OR (
                business_status = 'rejected'
                AND processed_at IS NOT NULL
                AND business_error_code IS NOT NULL
                AND business_error_message IS NOT NULL
                AND business_result IS NULL
            )
        );

CREATE TABLE wms.kiz_import_message_kiz (
    message_id bigint NOT NULL,
    kiz_id bigint NOT NULL,
    linked_at timestamptz NOT NULL DEFAULT now(),
    was_created boolean NOT NULL,
    CONSTRAINT pk_kiz_import_message_kiz PRIMARY KEY (message_id, kiz_id),
    CONSTRAINT fk_kiz_import_message_kiz_message
        FOREIGN KEY (message_id)
        REFERENCES wms.kiz_import_messages(message_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_kiz_import_message_kiz_kiz
        FOREIGN KEY (kiz_id)
        REFERENCES wms.kiz(kiz_id)
        ON DELETE RESTRICT
);

CREATE FUNCTION wms.guard_kiz_import_message_kiz_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'KIZ import message associations are immutable';
END;
$$;

CREATE TRIGGER trg_kiz_import_message_kiz_immutable
BEFORE UPDATE OR DELETE ON wms.kiz_import_message_kiz
FOR EACH ROW
EXECUTE FUNCTION wms.guard_kiz_import_message_kiz_immutable();

CREATE INDEX idx_kiz_import_messages_business_status
    ON wms.kiz_import_messages (business_status, message_id DESC);

CREATE INDEX idx_kiz_active_receipt_origin
    ON wms.kiz (origin_type, origin_reference, product_id, kiz_id)
    WHERE lifecycle_status = 'active';

CREATE INDEX idx_kiz_import_message_kiz_kiz
    ON wms.kiz_import_message_kiz (kiz_id, message_id);

COMMENT ON TABLE wms.kiz_import_message_kiz IS
'Immutable provenance links between raw KIZ import messages and existing or created KIZ.';

COMMENT ON COLUMN wms.kiz_import_messages.business_status IS
'Manual B2 apply status: pending, applied or rejected.';

COMMIT;
