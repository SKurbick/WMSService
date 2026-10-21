BEGIN;
SET LOCAL lock_timeout = '10s';

LOCK TABLE wms.kiz, wms.fbs_kiz_bindings IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM wms.kiz GROUP BY left(kiz_code, 24) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'KIZ_PREFIX_COLLISION: несколько КИЗ имеют одинаковые первые 24 символа';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.fbs_kiz_bindings
        GROUP BY left(kiz_code, 24) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'KIZ_BINDING_PREFIX_COLLISION: разные СЗ получат одинаковый КИЗ';
    END IF;
    IF EXISTS (
        SELECT 1 FROM wms.fbs_kiz_bindings binding
        JOIN wms.kiz kiz ON kiz.kiz_id = binding.kiz_id
            OR left(kiz.kiz_code, 24) = left(binding.kiz_code, 24)
        WHERE kiz.product_id <> binding.product_id
           OR binding.kiz_id IS NOT NULL AND (
               binding.kiz_id <> kiz.kiz_id
               OR left(binding.kiz_code, 24) <> left(kiz.kiz_code, 24)
           )
    ) THEN
        RAISE EXCEPTION 'KIZ_BINDING_IDENTITY_CONFLICT: привязка СЗ противоречит КИЗ';
    END IF;
END;
$$;

ALTER TABLE wms.kiz DROP CONSTRAINT kiz_kiz_code_check;
ALTER TABLE wms.fbs_kiz_bindings DROP CONSTRAINT fbs_kiz_bindings_kiz_code_check;

ALTER TABLE wms.kiz DISABLE TRIGGER trg_kiz_identity_guard;
ALTER TABLE wms.fbs_kiz_bindings DISABLE TRIGGER trg_fbs_kiz_bindings_identity;

UPDATE wms.kiz SET kiz_code = left(kiz_code, 24)
WHERE char_length(kiz_code) > 24;
UPDATE wms.fbs_kiz_bindings SET kiz_code = left(kiz_code, 24)
WHERE char_length(kiz_code) > 24;

SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE wms.kiz ENABLE TRIGGER trg_kiz_identity_guard;
ALTER TABLE wms.fbs_kiz_bindings ENABLE TRIGGER trg_fbs_kiz_bindings_identity;

ALTER TABLE wms.kiz ADD CONSTRAINT kiz_kiz_code_check
    CHECK (char_length(kiz_code) BETWEEN 1 AND 24);
ALTER TABLE wms.fbs_kiz_bindings ADD CONSTRAINT fbs_kiz_bindings_kiz_code_check
    CHECK (char_length(kiz_code) BETWEEN 1 AND 24);

COMMENT ON COLUMN wms.kiz.kiz_code IS
    'Literal first 24 Unicode characters; no trimming or case normalization.';
COMMENT ON COLUMN wms.fbs_kiz_bindings.kiz_code IS
    'Canonical KIZ identity: literal first 24 Unicode characters.';

COMMIT;
