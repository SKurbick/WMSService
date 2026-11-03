-- Explicit roster must be set by the deployment operator. This file NEVER grants.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='1s';
SET LOCAL wms.inventory_grant_runtime_privileges='off';
SELECT wms.inventory_runtime_privileges(CASE
 WHEN to_regclass('wms.inventory_applications') IS NULL THEN 'freeze' ELSE 'apply' END);
COMMIT;
