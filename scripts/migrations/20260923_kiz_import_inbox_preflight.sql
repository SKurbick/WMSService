-- Ожидаемый результат перед миграцией:
-- wms_schema_exists = true
-- existing_kiz_import_table = NULL

SELECT to_regnamespace('wms') IS NOT NULL AS wms_schema_exists;

SELECT to_regclass('wms.kiz_import_messages') AS existing_kiz_import_table;
