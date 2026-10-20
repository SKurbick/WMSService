-- Optional supply.organization provenance for KIZ import and KIZ identity.
-- Apply manually after 20261005_kiz_organization_preflight.sql is clean.

BEGIN;

ALTER TABLE wms.kiz_import_messages
    ADD COLUMN organization text;

UPDATE wms.kiz_import_messages
SET organization = NULLIF(btrim(raw_payload->'supply'->>'organization'), '')
WHERE jsonb_typeof(raw_payload->'supply'->'organization') = 'string'
  AND NULLIF(btrim(raw_payload->'supply'->>'organization'), '') IS NOT NULL;

ALTER TABLE wms.kiz_import_messages
    ADD CONSTRAINT chk_kiz_import_messages_organization
        CHECK (organization IS NULL OR btrim(organization) <> '');

CREATE INDEX idx_kiz_import_messages_organization
    ON wms.kiz_import_messages (organization, message_id DESC)
    WHERE organization IS NOT NULL;

ALTER TABLE wms.kiz
    ADD COLUMN organization text,
    ADD CONSTRAINT chk_kiz_organization
        CHECK (organization IS NULL OR btrim(organization) <> '');

CREATE INDEX idx_kiz_organization
    ON wms.kiz (organization, kiz_id)
    WHERE organization IS NOT NULL;

CREATE FUNCTION wms.guard_kiz_organization_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, wms
AS $$
BEGIN
    IF NEW.organization IS DISTINCT FROM OLD.organization THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P7501',
            MESSAGE = 'Организация КИЗ неизменяема';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_kiz_organization_immutable
BEFORE UPDATE OF organization ON wms.kiz
FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_organization_immutable();

COMMENT ON COLUMN wms.kiz_import_messages.organization IS
'Optional normalized supply.organization extracted from the immutable raw payload.';

COMMENT ON COLUMN wms.kiz.organization IS
'Optional immutable organization received with receipt-import KIZ.';

COMMIT;
