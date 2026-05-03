# Risk Map

Источник: анализ `docs/context/wms_schema.sql`.

## Высокие риски

### `unpack_from_container` конфликтует с constraints

Функция уменьшает `container_contents.quantity` до 0 и затем ставит `status='empty'`. DDL запрещает оба состояния: `quantity > 0`, `status` только `active/replaced/removed`. Распаковка полного количества должна падать check violation.

### Нет явной конкурентной защиты read-then-write операций

В DDL нет `SELECT FOR UPDATE` или advisory locks. Trigger inventory использует row locks при update/upsert, но `unpack_from_container`, `block_empty_container`, `find_available_location` не блокируют строки при проверке.

### `movements` допускает неоднозначные события

DDL не запрещает `quantity <= 0` и не требует заполнения `from_location_id` или `to_location_id`. Можно вставить event, который не меняет остаток или меняет его неожиданно.

## Средние риски

### `movements` без PK на parent table

Есть sequence/default, но нет primary key/unique на partitioned table. Это усложняет ссылки на movement и не гарантирует DB-level уникальность `movement_id` по всей таблице.

### Неполная связность контейнеров и movements

`container_code` хранится строкой без FK на `containers.qr_code`; `from_container_id/to_container_id` без FK. Возможны orphan references и расхождение `containers/container_contents/inventory/movements`.

### Смена parent location не обновляет потомков

`generate_location_path` обновляет только текущую строку. Потомки сохранят старые path, если приложение не обновит их отдельно или не запретит перенос узлов.

### `find_available_location` не резервирует место

Функция возвращает ячейку без блокировки и учитывает только вес, `is_active`, `level=5`, `zone_type`. Объем, `is_pickable`, блокировки адреса и параллельные размещения не учитываются DDL.

### FBS `movement_id` не защищен FK

`fbs_shipment_items.movement_id` имеет тип integer и не связан с `movements.movement_id` FK.

## Низкие/операционные риски

- Явные индексы на `containers.qr_code` и `locations.location_code` дублируют unique indexes.
- `inventory_snapshots` без FK/unique может содержать невалидные или дублирующие снимки.
- `notifications.severity` описан комментарием, но check отсутствует.
