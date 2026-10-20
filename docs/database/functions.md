# Database Functions

> **Статус: CURRENT.** Runtime-аудит 2026-08-08 подтвердил состав и определения 97 функций схемы `wms`; границы проверки описаны в [`production_schema_audit_2026-08-08.md`](production_schema_audit_2026-08-08.md).

Источник: `docs/archive/snapshots/wms_schema.sql`. Описано только поведение PL/pgSQL из DDL.

## `wms.register_container(p_qr_code, p_container_type, p_location_code, p_contents)`

Возвращает `(container_id bigint, qr_code varchar, items_registered integer)`.

Делает: проверяет отсутствие контейнера с таким QR; ищет `location_id` по коду; создает `containers` со статусом `empty` для пустого contents или `sealed` для непустого contents; для каждого элемента JSON-массива вставляет `container_contents`; возвращает id, QR и число items.

Читает: `containers`, `locations`. Изменяет: `containers`, `container_contents`; косвенно `movements` и `inventory` через triggers. Movements создает не напрямую: insert в `container_contents` вызывает `sync_container_to_inventory`, который создает `receive` movement. Inventory меняется trigger на `movements`.

Ошибки: `Container with QR code % already exists...`; `Location % not found`; также FK/check/unique ошибки. Конкурентность: `SELECT FOR UPDATE`/advisory locks нет; гонку duplicate QR закрывает unique constraint.

## `wms.unpack_from_container(p_qr_code, p_product_id, p_quantity)`

Возвращает `(success boolean, remaining_in_container numeric, loose_quantity numeric)`.

Делает: находит контейнер; читает active content по товару; проверяет достаточность; уменьшает `container_contents.quantity`; пытается закрыть нулевую current scope как `removed` после уменьшения; создает два `unpack` movements: расход из локации контейнера с `container_code=p_qr_code` и приход россыпью в ту же локацию с `container_code=NULL`; меняет контейнер `sealed -> open`.

Читает: `containers`, `container_contents`. Изменяет: `container_contents`, `movements`, `containers`; косвенно `inventory`. Movements: да, две записи `unpack`. Inventory: через trigger movement.

Ошибки: `Container % not found`; `Not enough quantity...`; check violation из-за `quantity=0`; последующий перевод scope в `removed` недостижим при полном извлечении, пока UPDATE quantity первым пытается записать ноль. Конкурентность: locks нет, read-then-update может гоняться при параллельной распаковке.

## `wms.find_available_location(p_product_id, p_quantity, p_zone_type default 'storage')`

Возвращает одну строку `(location_id, location_code, available_space)`.

Делает: ищет active локацию `level=5` и `zone_type=p_zone_type`; считает свободный вес как `max_weight - SUM(inventory.quantity * products.weight)`; сравнивает с весом искомого товара `weight * p_quantity`; сортирует по максимальному свободному месту.

Читает: `locations`, `inventory`, `public.products`. Не изменяет данные, movements не создает, inventory не меняет. Locks нет; результат не резервирует ячейку и не защищает от параллельного размещения. Если product не найден, функция, вероятно, просто не вернет строку.

## `wms.get_task_items_summary(p_task_id)`

Возвращает позиции заявки с `product_name` и `from_location_code`. Читает `task_items`, `public.products`, `locations`. Данные не меняет, movements не создает, locks нет.

## `wms.update_inventory_from_movement()`

Trigger function для `AFTER INSERT ON wms.movements`.

Делает: если заполнен `to_location_id`, вставляет или увеличивает inventory по `(product_id, location_id, status, batch_number, container_code)` со статусом `available`; если заполнен `from_location_id`, уменьшает inventory со статусом `available`; если строка для списания не найдена и type `ship/transfer`, считает суммарный остаток и бросает exception; удаляет строки inventory с `quantity <= 0`.

Читает/изменяет: `inventory`. Movements не создает. Inventory меняет напрямую. Ошибки: `Недостаточно остатка...` при отсутствии строки для `ship/transfer`; `inventory_quantity_check` при уходе ниже нуля; FK/check ошибки. Конкурентность: explicit locks/advisory locks нет; row locks возникают у PostgreSQL при `UPDATE` и `INSERT ... ON CONFLICT DO UPDATE`.

## `wms.sync_container_to_inventory()`

Trigger function для `AFTER INSERT ON wms.container_contents`. Если `NEW.status != 'active'`, ничего не делает. Иначе берет location и QR контейнера, создает `receive` movement с `container_code=qr_code`. Ошибка: `Container % has no location assigned`. Inventory меняется косвенно trigger на movement. Locks нет.

## `wms.move_container_inventory()`

Trigger function для `AFTER UPDATE OF location_id ON wms.containers`. Если location изменился, читает inventory rows с `container_code=NEW.qr_code` и создает `transfer` movement по каждой строке с `OLD.location_id -> NEW.location_id`, quantity и batch из inventory. Inventory напрямую не меняет. Locks нет.

## `wms.block_empty_container(p_qr_code)`

Возвращает boolean. Находит контейнер, проверяет отсутствие active contents, ставит `containers.status='blocked'`. Movements/inventory не меняет. Ошибки: `Container % not found`; `Container % is not empty, cannot block`. Locks нет; между проверкой и update возможна гонка.

## Location helper functions

`wms.generate_location_code()` - BEFORE INSERT trigger function; генерирует `location_code` из parent code и `NEW.name/NEW.level`: root из имени, level 1 зона, level 2 стеллаж, level 3 секция `Sxx`, level 4 ярус `Lxx`, level 5 ячейка. Явной ошибки при отсутствующем parent нет, дальше сработают NOT NULL/FK/unique.

`wms.generate_location_path()` - BEFORE INSERT OR UPDATE OF `parent_location_id`; root path = `location_id`, child path = `parent.path || '.' || location_id`. Ошибка: `Parent location % not found`. Потомков при смене parent не обновляет.

`wms.get_child_locations(p_location_id)` - читает parent path, возвращает потомков через `path <@ v_path`, исключая саму локацию. Ошибка: `Location % not found`.

## Other read/timestamp functions

`wms.get_approvers()` читает `public.users` и `public.user_permissions`, возвращает enabled users с `approve_discrepancies=TRUE`.

`update_containers_timestamp`, `update_fbs_item_updated_at`, `update_inventory_timestamp`, `update_locations_timestamp`, `update_updated_at_column` только присваивают `NEW.updated_at = now()`.

## KIZ v1 functions

- wms.guard_kiz_inventory(): VOLATILE BEFORE row guard исходного доступного россыпного остатка,
  COUNT активных через partial index, exception P7501 с JSON DETAIL diagnostics.
- wms.guard_kiz_identity(): только active → error/deactivated; immutable identity;
  closed_at/updated_at выставляются в now(); DELETE запрещён.
- wms.guard_kiz_event_immutable(): UPDATE/DELETE events всегда P7501.

Все KIZ v1 функции имеют фиксированный search_path pg_catalog,wms, без SECURITY DEFINER.
Assignment lock/touch/count/audit и recalculate orchestration находятся в Python/SQL query
слое; новых physical movement functions нет. [Миграция](../../scripts/migrations/20260906_add_kiz_v1.sql).

## Movement identity Phase 1 functions

- `wms.register_movement_identity()` — SECURITY DEFINER row trigger function с фиксированным
  `search_path`, атомарно регистрирует NEW movement exact coordinate и не требует выдавать
  writer-ролям прямой INSERT на registry/identity sequence.
- `wms.guard_movement_registry_immutable()` — запрещает UPDATE/DELETE registry mapping.
- `wms.backfill_movement_registry(batch_size)` — вставляет до 100000 missing mappings,
  использует `FOR UPDATE SKIP LOCKED` и conflict-safe retry; вызывается отдельными
  transactions до результата 0.
- `wms.check_movement_registry_integrity()` — read-only snapshot counts для coverage,
  orphan и duplicate diagnostics.

## KIZ Stage 2A Phase 2 function

- `wms.guard_kiz_movement_link_immutable()` — всегда запрещает UPDATE/DELETE
  `kiz_movement_links` с SQLSTATE 55000. INSERT выполнят только будущие поддерживаемые
  physical operations; в Phase 2 application writer отсутствует.

## KIZ Stage 2A Phase 3 functions

- `wms.guard_kiz_operation_update()` сохраняет identity и разрешает result_payload
  заполнить ровно один раз; DELETE запрещён.
- `wms.require_kiz_operation_result_at_commit()` проверяет deferred, что intent не
  может быть committed без replay result.
- `wms.guard_kiz_operation_item_update()` разрешает movement_ref заполнить один раз,
  сохраняя identity строки; DELETE запрещён.

### Phase 4

- `wms.transfer_kiz_location(...)` — SECURITY DEFINER с fixed search_path; проверяет
  active KIZ, expected product/source и transfer item, затем меняет только location.
- `wms.require_complete_kiz_location_transfer()` — deferred проверка полного
  operation→movement→registry→link graph и удаление transaction authorization.

### Phase 5

- `wms.ship_kiz(...)` — SECURITY DEFINER с fixed search_path; проверяет incomplete
  ship item и expected active KIZ, создаёт transaction authorization и выполняет только
  active/source → shipped/NULL transition.
- `wms.require_complete_kiz_shipment()` — deferred проверка operation result,
  item/movement provenance, registry, link, shipped current state и shipped event;
  затем удаляет authorization.

## Container Stage 3B B1 function compatibility

`register_container` сохраняет receipt-like quantity behavior, но создаёт пустой
container со status `empty`, непустой — `sealed`. `unpack_from_container` нормализован
на `sealed -> open`; физический алгоритм, locks и full-extract defect не исправлялись.

Новые guard functions используют fixed `search_path=pg_catalog,wms`. Для будущих
SECURITY DEFINER physical functions требуется REVOKE PUBLIC EXECUTE и точечный GRANT
non-owner runtime role; production role B1 не меняет.

## Container B2.1 functions

- `sync_container_to_inventory()` сохраняет legacy receipt behavior, но пропускает новый
  active contents INSERT только при совпадающей transaction-local unfinished fill line.
- `unpack_from_container()` получает container row `FOR UPDATE`, чтобы не перезаписывать
  concurrent fill stale quantity; extract semantics не изменены.
- Global movement projection и movement registry создают inventory changes и stable refs
  для двух fill movements.

## Container B3 functions

- `wms.move_container_inventory()` сохраняет legacy projection, но пропускает её при
  валидном transaction-local authorization unfinished B3 move, исключая duplicate movement.
- `wms.apply_container_extract_content(bigint)` допускает unfinished `extract` и
  `unpack_all` item с полной paired movement provenance.
- `wms.require_complete_container_operation()` проверяет B3 result/item/movement graph.

## Container B4 function changes

После `20260917_add_container_b4_final.sql`:

- `wms.register_container(...)` создаёт только empty container и отклоняет non-empty JSON array;
- `wms.unpack_from_container(...)` удалена;
- `wms.move_container_inventory()` удалена;
- `wms.guard_controlled_container_movement()` отклоняет container-coded movement без
  полного `container_operation` provenance.

Ранние разделы этого файла описывают pre-B4 historical behavior и не являются текущим write contract.

## Container Stage 3C C1 functions

- `wms.transition_kiz_container_holder(...)` — SECURITY DEFINER controlled fill/extract
  holder transition; direct UPDATE остаётся запрещён.
- `wms.require_complete_kiz_container_holder()` — deferred result/paired-links/final-holder check.
- `wms.require_kiz_final_holder_integrity()` — deferred identified/physical final check.
- `wms.check_kiz_holder_integrity()` — read-only loose/container integrity dataset.
- `wms.guard_kiz_inventory()` теперь учитывает direct container holder и controlled move.
