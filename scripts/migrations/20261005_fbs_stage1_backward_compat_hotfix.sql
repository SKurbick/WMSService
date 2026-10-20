-- Hotfix для БД, где 20261002_add_fbs_kiz_stage1.sql применена раньше Stage 1 кода.
-- Разрешает старой версии приложения писать written_off task result без новых source-полей.

BEGIN;

ALTER TABLE wms.fbs_shipment_task_results
    DROP CONSTRAINT chk_fbs_task_result_written_off_source;

ALTER TABLE wms.fbs_shipment_task_results
    ADD CONSTRAINT chk_fbs_task_result_written_off_source
        CHECK (
            outcome <> 'written_off'
            OR movement_ref IS NULL
               AND source_kind IS NULL
               AND source_location_id IS NULL
               AND source_container_id IS NULL
            OR movement_ref IS NOT NULL
               AND source_kind IS NOT NULL
               AND source_location_id IS NOT NULL
               AND source_container_id IS NULL
        );

COMMIT;
