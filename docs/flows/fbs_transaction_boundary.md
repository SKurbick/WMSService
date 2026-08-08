# FBS Write-Off Transaction Boundary

Статус: `CURRENT`.

Дата актуализации: 2026-08-07.

## Текущая граница product group

```text
BEGIN
  -> SELECT fbs_shipment_items ... FOR UPDATE
  -> SELECT assembly_task ... FOR UPDATE
  -> validate unique assembly tasks
  -> UPDATE public.assembly_task SET is_shipped = TRUE ... RETURNING task_id
  -> проверить, что захвачены все ожидаемые task_id
  -> create movement через переданный conn
  -> AFTER INSERT inventory trigger
  -> UPDATE всех связанных fbs_shipment_items в success с одним movement_id RETURNING item_id
  -> проверить, что обновлены все ожидаемые item_id
  -> пересчитать статус родительского fbs_shipment
COMMIT
```

Любая ошибка movement, inventory trigger или неполного item update откатывает `is_shipped`, movement, inventory и item success. Основной handler, retry worker и manual item retry используют `_process_shipment_group`.

Проверка location внутри `MovementService.create_movement_in_transaction` использует тот же `conn`; отдельного connection/commit между шагами нет.

Если assembly task уже отгружена, обработчик проверяет наличие `success` FBS item с
непустым `movement_id` для всех СЗ группы. Полностью подтверждённое состояние считается
обычным дублем. Если хотя бы одна СЗ не подтверждена, выбрасывается
`InconsistentFbsShipmentError`; новый movement не создаётся и production recovery
автоматически не выполняется.

## После commit

Основной handler и retry worker повторно пересчитывают `wms.fbs_shipments.status` после
групп для совместимости. Критический пересчёт конкретной product group уже выполнен до
её commit; повторный вызов идемпотентен.

## Основные точки кода

- atomic claim: `app/handlers/write_off_fbs_handler.py`, `validate_assembly_tasks`;
- общая product group: `app/handlers/write_off_fbs_handler.py`, `_process_shipment_group`;
- atomic items update: `app/infrastructure/database/repositories/fbs_shipment_repository.py`, `mark_items_success_in_transaction`;
- primary flow: `handle_write_off_fbs`;
- retry flow: `app/retry_worker.py`, `process_pending_retries`;
- manual retry: `POST /api/fbs-shipments/items/{item_id}/retry`.
