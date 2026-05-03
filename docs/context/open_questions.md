# Open Questions

## База данных и миграции

- Где находится миграция, создающая `wms.ltree`/extension `ltree`? В `wms_schema.sql` используется `wms.ltree`, но `CREATE EXTENSION/TYPE` отсутствует.
- Почему parent table `wms.movements` не имеет primary key/unique constraint на `movement_id`?
- Нужны ли FK для `movements.from_container_id`, `movements.to_container_id`, `movements.container_code`, `fbs_shipment_items.movement_id`, `inventory_snapshots.product_id/location_id`, `tasks.related_movement_id`?
- Должен ли `movements.quantity` быть строго положительным?
- Должен ли movement иметь хотя бы одну сторону (`from_location_id` или `to_location_id`) на уровне БД?

## Остатки и конкурентность

- Достаточно ли check `inventory.quantity >= 0` для предотвращения отрицательных остатков, или нужна явная проверка `quantity >= requested` в trigger?
- Нужно ли добавлять `SELECT FOR UPDATE` в операции списания/распаковки/перемещения контейнера?
- Как должны обрабатываться параллельные `unpack_from_container` одного контейнера и товара?
- Как должны обрабатываться параллельные FBS retry workers для одной позиции?
- Нужны ли advisory locks по `(product_id, location_id, batch_number, container_code)` для критичных списаний?

## Локации

- Где enforce диапазон и смысл `locations.level`?
- Должна ли БД запрещать создание дочерней локации под неактивным parent?
- Должна ли БД запрещать размещение/движение в `is_active = false` location?
- Что должно происходить с path потомков при смене `parent_location_id` у parent location?
- Нужно ли генерировать `location_code` заново при rename или смене parent?
- Что считается доступной ячейкой: только `is_active`, `zone_type`, `level=5`, capacity или еще `is_pickable`/блокировки?

## Контейнеры

- Как исправить конфликт `unpack_from_container` с constraints `container_contents.quantity > 0` и `status in active/replaced/removed`, если функция ставит `quantity=0` и `status='empty'`?
- Должен ли `container_contents` иметь статус `empty`, или функция должна использовать `removed/replaced`?
- Нужно ли проверять статус контейнера (`blocked`, `empty`) в trigger/function на уровне БД?
- Должно ли перемещение parent container перемещать вложенные child containers и их inventory?
- Должна ли `block_empty_container` блокировать строку контейнера/contents на время проверки empty?

## Заявки и FBS

- Должна ли БД enforce `task_items.quantity_planned > 0` и минимум одну позицию на task?
- Нужно ли блокировать task row при assign/start/complete/approve?
- Как связывать movements, созданные по task, с task?
- Должен ли `fbs_shipment_items.movement_id` быть bigint и FK на `wms.movements(movement_id)`?
- Нужен ли retry worker с `FOR UPDATE SKIP LOCKED`?
- Должны ли FBS списания учитывать batch/container или всегда списывают агрегированный available остаток из одной location?

## Тесты

- Есть ли тестовая БД/fixtures для проверки PL/pgSQL функций и триггеров?
- Нужны ли regression tests на конфликт `unpack_from_container` с текущими constraints?
