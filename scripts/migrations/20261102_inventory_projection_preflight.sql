-- Read-only preflight. No application functions or physical writes.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='1s';
DO $preflight$ DECLARE p record;
BEGIN
 SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
 WHERE f.oid=to_regprocedure('wms.update_inventory_from_movement()');
 IF NOT FOUND THEN RAISE EXCEPTION 'Missing inventory projection function'; END IF;
 IF p.lanname<>'plpgsql' OR p.prorettype<>'trigger'::regtype OR p.pronargs<>0
   OR p.prokind<>'f' OR p.prosecdef OR p.proleakproof OR p.provolatile<>'v'
   OR p.proparallel<>'u' OR p.proconfig IS NOT NULL
   OR md5(btrim(p.prosrc,E' \t\r\n')) NOT IN ('59757ad997db9dd81274bd13dadc9e4f','e20d6d1d99ebfea866825b07f0644009') THEN
   RAISE EXCEPTION USING ERRCODE='P7604',MESSAGE='Unsupported installed inventory projection function contract',
     DETAIL='Observed trimmed md5='||md5(btrim(p.prosrc,E' \t\r\n')),
     HINT='Review the installed body and attributes; do not overwrite runtime with archival DDL.';
 END IF;
 RAISE NOTICE 'Supported legacy projection contract, trimmed md5=%',md5(btrim(p.prosrc,E' \t\r\n'));
END $preflight$;
COMMIT;
