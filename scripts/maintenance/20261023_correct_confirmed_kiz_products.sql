-- User-confirmed identities only. Run after 20261023_add_kiz_product_corrections.sql.
-- This corrects registration identity, not receipt quantities or FBS shipment.
BEGIN;
SET LOCAL lock_timeout = '10s';
DO $$
DECLARE matched integer;
BEGIN
    PERFORM id FROM public.products
    WHERE id IN ('wild1886','wild188601','wild1889','wild188901') ORDER BY id FOR SHARE;
    PERFORM kiz_id FROM wms.kiz WHERE kiz_id IN (8441,9498,9482) ORDER BY kiz_id FOR UPDATE;
    INSERT INTO wms.kiz_product_corrections
      (kiz_id,old_product_id,new_product_id,source_message_id,fbs_task_id,reason,author)
    SELECT v.kiz_id,v.old_product,v.new_product,v.message_id,v.task_id,
           'Пользователь подтвердил артикул FBS 2.0 для конкретного КИЗа; исправление регистрации',
           'manual:user-approved'
    FROM (VALUES
      (8441::bigint,'0104610618770033215fDBeD','wild1886','wild188601',13::bigint,6089721324::bigint),
      (9482::bigint,'0104610618770064215LYxs1','wild1889','wild188901',14::bigint,6099438915::bigint),
      (9498::bigint,'0104610618770064215M,bn_','wild1889','wild188901',14::bigint,6095917007::bigint)
    ) AS v(kiz_id,kiz_code,old_product,new_product,message_id,task_id)
    JOIN wms.kiz k ON k.kiz_id=v.kiz_id AND k.kiz_code=v.kiz_code
    WHERE NOT EXISTS (SELECT 1 FROM wms.kiz_product_corrections c WHERE c.kiz_id=v.kiz_id)
    ORDER BY v.kiz_id;

    SELECT count(*) INTO matched
    FROM wms.kiz_product_corrections c JOIN wms.kiz k USING(kiz_id)
    JOIN (VALUES
      (8441::bigint,'0104610618770033215fDBeD','wild1886','wild188601',13::bigint,6089721324::bigint),
      (9482::bigint,'0104610618770064215LYxs1','wild1889','wild188901',14::bigint,6099438915::bigint),
      (9498::bigint,'0104610618770064215M,bn_','wild1889','wild188901',14::bigint,6095917007::bigint)
    ) AS v(kiz_id,kiz_code,old_product,new_product,message_id,task_id)
      ON c.kiz_id=v.kiz_id AND k.kiz_code=v.kiz_code
     AND c.old_product_id=v.old_product AND c.new_product_id=v.new_product
     AND c.source_message_id=v.message_id AND c.fbs_task_id=v.task_id
     AND k.product_id=v.new_product
    WHERE k.lifecycle_status='registered' AND k.location_id IS NULL AND k.container_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM wms.kiz_movement_links l WHERE l.kiz_id=k.kiz_id);
    IF matched<>3 THEN
        RAISE EXCEPTION 'Expected exactly three safe corrected identities, got %; rollback', matched;
    END IF;
END;
$$;
COMMIT;
