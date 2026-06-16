# FBS Write-Off Transaction Boundary

Дата актуализации: 2026-06-14.

## Текущая граница product group

```text
BEGIN
  -> validate unique assembly tasks
  -> UPDATE public.assembly_task SET is_shipped = TRUE ... RETURNING task_id
  -> проверить, что захвачены все ожидаемые task_id
  -> create movement через переданный conn
  -> AFTER INSERT inventory trigger
  -> UPDATE всех связанных fbs_shipment_items в success с одним movement_id RETURNING item_id
  -> проверить, что обновлены все ожидаемые item_id
COMMIT
```

Любая ошибка movement, inventory trigger или неполного item update откатывает `is_shipped`, movement, inventory и item success. Основной handler, retry worker и manual item retry используют `_process_shipment_group`.

Проверка location внутри `MovementService.create_movement_in_transaction` по-прежнему использует отдельный `LocationRepository.get_by_code()` connection.

## После commit

После обработки product groups отдельным запросом пересчитывается `wms.fbs_shipments.status`. Recovery зависшего shipment status остается техдолгом.

## Основные точки кода

- atomic claim: `app/handlers/write_off_fbs_handler.py`, `validate_assembly_tasks`;
- общая product group: `app/handlers/write_off_fbs_handler.py`, `_process_shipment_group`;
- atomic items update: `app/infrastructure/database/repositories/fbs_shipment_repository.py`, `mark_items_success_in_transaction`;
- primary flow: `handle_write_off_fbs`;
- retry flow: `app/retry_worker.py`, `process_pending_retries`;
- manual retry: `POST /api/fbs-shipments/items/{item_id}/retry`.
