# Database Functions

> **Статус: CURRENT.** Runtime-аудит 2026-08-08 подтвердил состав и определения 97 функций схемы `wms`; границы проверки описаны в [`production_schema_audit_2026-08-08.md`](production_schema_audit_2026-08-08.md).

Источник: `docs/archive/snapshots/wms_schema.sql`. Описано только поведение PL/pgSQL из DDL.

## `wms.register_container(p_qr_code, p_container_type, p_location_code, p_contents)`

Возвращает `(container_id bigint, qr_code varchar, items_registered integer)`.

Делает: проверяет отсутствие контейнера с таким QR; ищет `location_id` по коду; создает `containers` со статусом `sealed`; для каждого элемента JSON-массива вставляет `container_contents`; возвращает id, QR и число items.

Читает: `containers`, `locations`. Изменяет: `containers`, `container_contents`; косвенно `movements` и `inventory` через triggers. Movements создает не напрямую: insert в `container_contents` вызывает `sync_container_to_inventory`, который создает `receive` movement. Inventory меняется trigger на `movements`.

Ошибки: `Container with QR code % already exists...`; `Location % not found`; также FK/check/unique ошибки. Конкурентность: `SELECT FOR UPDATE`/advisory locks нет; гонку duplicate QR закрывает unique constraint.

## `wms.unpack_from_container(p_qr_code, p_product_id, p_quantity)`

Возвращает `(success boolean, remaining_in_container numeric, loose_quantity numeric)`.

Делает: находит контейнер; читает active content по товару; проверяет достаточность; уменьшает `container_contents.quantity`; пытается поставить `status='empty'` при нуле; создает два `unpack` movements: расход из локации контейнера с `container_code=p_qr_code` и приход россыпью в ту же локацию с `container_code=NULL`; меняет контейнер `sealed -> opened`.

Читает: `containers`, `container_contents`. Изменяет: `container_contents`, `movements`, `containers`; косвенно `inventory`. Movements: да, две записи `unpack`. Inventory: через trigger movement.

Ошибки: `Container % not found`; `Not enough quantity...`; check violation из-за `quantity=0`; check violation из-за `status='empty'`, которого нет в `chk_content_status`. Конкурентность: locks нет, read-then-update может гоняться при параллельной распаковке.

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
