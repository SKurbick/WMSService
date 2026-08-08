# Database Triggers

> **Статус: CURRENT.** Runtime-аудит 2026-08-08 подтвердил 26 включённых пользовательских triggers схемы `wms`, включая inventory triggers partitions; см. [`production_schema_audit_2026-08-08.md`](production_schema_audit_2026-08-08.md).

Источник: `docs/archive/snapshots/wms_schema.sql`.

## `trg_update_inventory_from_movement`

Таблица: `wms.movements`. Когда: `AFTER INSERT`, for each row. Функция: `wms.update_inventory_from_movement()`.

Меняет `wms.inventory`: `to_location_id` увеличивает/создает available остаток; `from_location_id` уменьшает available остаток; строки `quantity <= 0` удаляются. Для `ship/transfer` без найденной строки списания бросает exception. Если строка найдена, но quantity уходит ниже нуля, срабатывает `inventory_quantity_check`.

Бизнес-правило: movement materializes current stock. Explicit `SELECT FOR UPDATE`/advisory locks нет.

## `trg_sync_container_contents_to_inventory`

Таблица: `wms.container_contents`. Когда: `AFTER INSERT`. Функция: `wms.sync_container_to_inventory()`.

Для active content создает `receive` movement в location контейнера с `container_code=qr_code`; дальше inventory меняет movement trigger. Если content не active, ничего не делает. Требует location у контейнера.

## `trg_move_container_inventory`

Таблица: `wms.containers`. Когда: `AFTER UPDATE OF location_id`. Функция: `wms.move_container_inventory()`.

При смене location создает `transfer` movements для всех inventory rows с `container_code=NEW.qr_code`. Inventory напрямую не меняет. Статус контейнера на уровне trigger не проверяется.

## Location triggers

`trg_generate_location_code`: `BEFORE INSERT ON wms.locations`, вызывает `generate_location_code`, заполняет `NEW.location_code`.

`trg_generate_location_path`: `BEFORE INSERT OR UPDATE OF parent_location_id ON wms.locations`, вызывает `generate_location_path`, заполняет `NEW.path`. При смене parent обновляет только текущую строку, не потомков.

`trg_locations_updated_at`: `BEFORE UPDATE ON wms.locations`, обновляет `updated_at`.

## Timestamp triggers

- `trg_containers_updated_at`: `BEFORE UPDATE ON containers`, `update_containers_timestamp`.
- `trg_inventory_updated_at`: `BEFORE UPDATE ON inventory`, `update_inventory_timestamp`.
- `trg_tasks_updated_at`: `BEFORE UPDATE ON tasks`, `update_updated_at_column`.
- `trg_fbs_item_updated_at`: `BEFORE UPDATE ON fbs_shipment_items`, `update_fbs_item_updated_at`.
- `trg_receipt_items_updated_at`: `BEFORE UPDATE ON receipt_items`, `update_inventory_timestamp`.
