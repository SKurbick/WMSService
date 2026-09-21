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

## KIZ v1 migration

| Trigger | Table / timing | Function |
|---|---|---|
| trg_kiz_inventory_guard | inventory BEFORE UPDATE OR DELETE, row | wms.guard_kiz_inventory |
| trg_kiz_identity_guard | kiz BEFORE UPDATE OR DELETE, row | wms.guard_kiz_identity |
| trg_kiz_events_immutable | kiz_events BEFORE UPDATE OR DELETE, row | wms.guard_kiz_event_immutable |

Inventory guard проверяет OLD available/NULL/NULL scope: quantity reduction требует
NEW.quantity >= active count; DELETE/key change требуют active count=0. Increase/touch
без смены scope пропускаются без count. P7501 откатывает projection и породивший movement.
Существующий trg_inventory_updated_at остаётся включён, в том числе при assignment touch.
KIZ trigger запрещает hard delete/identity edits/повторный terminal; event trigger запрещает
UPDATE/DELETE. Trigger не защищает от отключения владельцем или TRUNCATE.

## Movement identity triggers

`trg_register_movement_identity` — `AFTER INSERT`, row trigger на partitioned
`wms.movements`; вызывает `wms.register_movement_identity()` и создаёт одну registry row
по точным `movement_id + created_at` в той же transaction. Он покрывает Python,
PL/pgSQL и direct supported writers. Ошибка регистрации откатывает movement и изменения
inventory другого AFTER trigger.

`trg_movement_registry_immutable` вызывает `guard_movement_registry_immutable()` и
запрещает UPDATE/DELETE mapping с SQLSTATE 55000.

## KIZ association trigger

`trg_kiz_movement_links_immutable` — BEFORE UPDATE OR DELETE row trigger; вызывает
`wms.guard_kiz_movement_link_immutable()`. Прямой KIZ identity guard не ослаблен:
location update и active→shipped остаются запрещены до реализации controlled operation.

## KIZ operation idempotency triggers

- `trg_kiz_operations_guard` — BEFORE UPDATE OR DELETE; identity immutable, result
  допускает только NULL→jsonb.
- `trg_kiz_operations_result_at_commit` — deferred constraint trigger; COMMIT intent
  без result запрещён.
- `trg_kiz_operation_items_guard` — BEFORE UPDATE OR DELETE; разрешает однократное
  присоединение movement_ref.

### Phase 4 controlled KIZ transfer

`trg_kiz_identity_guard` допускает location-only update лишь при точном authorization
текущей transaction. Deferred `trg_complete_kiz_location_transfer` откатывает incomplete
operation до commit.

### Phase 5 controlled KIZ shipment

`trg_kiz_identity_guard` допускает active/source → shipped/NULL только при exact
shipment authorization текущей transaction. Deferred
`trg_complete_kiz_shipment` запрещает commit неполного physical shipment graph.

## Container Stage 3B B1 guards

- `trg_container_identity_immutable` (`BEFORE UPDATE OF container_id,qr_code OR DELETE`)
  запрещает rename/re-identification/hard delete с SQLSTATE `55000`.
- `trg_container_empty_state` запрещает status `empty`, если active contents существуют.
- `trg_active_content_container_state` запрещает INSERT/UPDATE active content в `empty`.

Legacy sync/move triggers не переработаны; B1 не добавляет quantity или movement writes.

## Container B2.1 triggers

- Immutable guards разрешают operation result и пару refs установить ровно один раз.
- Deferred constraint triggers запрещают commit без result, items или двух movement refs.
- Legacy contents-to-inventory trigger не создаёт receive для авторизованного fill INSERT;
  все остальные legacy inserts сохраняют прежнее поведение.

## Container B3 trigger behavior

`trg_move_container_inventory` не отключается. Для обычного UPDATE он работает как
раньше; controlled move устанавливает transaction-local operation id, который функция
проверяет по unfinished operation/container и пропускает только duplicate legacy path.
Deferred operation-item completeness теперь отслеживает и single `movement_ref`.

## Container B4 trigger changes

После `20260917_add_container_b4_final.sql` trigger `trg_move_container_inventory` удалён.
Добавлен `BEFORE INSERT` trigger `trg_guard_controlled_container_movement` на
`wms.movements`: non-NULL `container_code` допустим только при
`source_type='container_operation'` и заполненных `source_id/source_item_id`.
Controlled B3 move выполняет location update и movement самостоятельно в одной transaction.

## Container Stage 3C C1 triggers

- `trg_complete_kiz_container_holder` запрещает commit неполной holder transition.
- `trg_kiz_final_holder_integrity` deferred проверяет final loose/contained quantity.
- Existing `trg_kiz_identity_guard` разрешает container holder update только при exact
  transaction authorization; terminal diagnostic holder сохраняется.
