BEGIN TRANSACTION READ ONLY;

SELECT left(kiz_code, 24) AS canonical_code, array_agg(kiz_id ORDER BY kiz_id) AS kiz_ids
FROM wms.kiz
GROUP BY left(kiz_code, 24)
HAVING count(*) > 1;

SELECT left(kiz_code, 24) AS canonical_code, array_agg(task_id ORDER BY task_id) AS task_ids
FROM wms.fbs_kiz_bindings
GROUP BY left(kiz_code, 24)
HAVING count(*) > 1;

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

SELECT count(*) FILTER (WHERE char_length(kiz_code) > 24) AS kiz_to_shorten
FROM wms.kiz;
SELECT count(*) FILTER (WHERE char_length(kiz_code) > 24) AS bindings_to_shorten
FROM wms.fbs_kiz_bindings;

COMMIT;
