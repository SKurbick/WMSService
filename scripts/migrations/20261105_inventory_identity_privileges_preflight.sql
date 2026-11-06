-- Post-install read-only preflight. Supply explicit wms.inventory_runtime_roles
-- in the deployment session. This script never grants privileges.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='1s';
SET LOCAL wms.inventory_grant_runtime_privileges='off';
SELECT current_setting('server_version') AS server_version,current_user;
SELECT p.oid::regprocedure,p.prosecdef,p.proconfig,md5(p.prosrc) AS body_md5
 FROM pg_proc p WHERE p.oid='wms.inventory_check_location_write(bigint,character varying,boolean,boolean,bigint,timestamp with time zone)'::regprocedure;
SELECT conname,convalidated,pg_get_constraintdef(oid)
 FROM pg_constraint WHERE conrelid='wms.containers'::regclass AND conname='chk_container_flat';
SELECT wms.inventory_identity_runtime_privileges();
COMMIT;
