BEGIN;

ALTER TABLE wms.fbs_shipments
    ADD COLUMN IF NOT EXISTS source varchar(30) NOT NULL DEFAULT 'standard';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_fbs_shipments_source'
          AND conrelid = 'wms.fbs_shipments'::regclass
    ) THEN
        ALTER TABLE wms.fbs_shipments
            ADD CONSTRAINT chk_fbs_shipments_source
            CHECK (source IN ('standard', 'external_detected'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_fbs_shipments_source_received_at
    ON wms.fbs_shipments (source, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_fbs_shipments_source_status
    ON wms.fbs_shipments (source, status);

COMMIT;
