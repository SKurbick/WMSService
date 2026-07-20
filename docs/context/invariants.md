# Invariants

Источник: `docs/context/wms_schema.sql` плюс явно отмеченные требования, которые должны обеспечиваться приложением.

## Enforced by DB

- `locations.location_code` уникален.
- `locations.path` обязателен и имеет тип `wms.ltree`.
- `locations.parent_location_id`, если заполнен, ссылается на существующую location.
- `containers.qr_code` уникален.
- `containers.container_type` входит в `pallet/box/unit`.
- `containers.status` входит в `sealed/opened/empty/blocked`.
- `container_contents.quantity > 0`.
- `container_contents.status` входит в `active/replaced/removed`.
- `inventory.quantity >= 0`.
- `inventory.status` входит в `available/damaged/quarantine`.
- `inventory` не имеет дублей по `(product_id, location_id, status, batch_number, container_code)` с учетом `NULLS NOT DISTINCT`.
- `movements.movement_type`, `tasks.task_type/status`, `fbs_shipments.status`, `fbs_shipment_items.status` входят в разрешенные списки.
- Product/location/user references, объявленные FK, должны существовать.

## Inventory/movements

- `movements` должен быть достаточным источником для восстановления `inventory`.
- Insert в `movements` должен быть нормальным способом менять остатки.
- `to_location_id` увеличивает inventory; `from_location_id` уменьшает inventory.
- Расход не должен приводить к отрицательному `inventory.quantity`; сейчас это enforced check constraint, а не явная предварительная проверка `quantity >= requested`.
- Нулевые inventory rows удаляются trigger function.
- `container_code` в inventory/movements должен совпадать с QR контейнера, но FK этого не enforce.

## Locations

- `parent_location_id` и `path` должны описывать одну и ту же иерархию.
- При смене parent у локации должны быть согласованы path всех потомков; DDL обновляет только строку, где был UPDATE.
- `location_code` генерируется только при INSERT, не при rename или смене parent.
- Канонические уровни из комментариев функций: root/склад без parent, level 1 зона, level 2 стеллаж, level 3 секция, level 4 ярус, level 5 ячейка. DDL диапазон не проверяет.

## Containers

- Active contents контейнера должны соответствовать inventory rows с `container_code = containers.qr_code`.
- Регистрация контейнера не должна обходить trigger sync.
- Перемещение контейнера должно создавать transfer movements по всем остаткам контейнера.
- Распаковка не должна извлекать больше active quantity, чем есть.
- Заблокированный контейнер не должен использоваться в операциях; DDL это не enforce, кроме ручной функции `block_empty_container`.

## Tasks and FBS

- Task item должен принадлежать существующей task.
- Complete/approve/recount semantics не заданы DDL и должны поддерживаться приложением.
- Movements, созданные по task, должны быть согласованы с task, но FK для `related_movement_id` нет.
- FBS item должен принадлежать shipment.
- Если `fbs_shipment_items.movement_id` заполнен, он должен указывать на созданное списание, но DDL этого не enforce.

## Конкурентный доступ

- Операции изменения остатков должны выполняться в транзакции.
- DDL не содержит advisory locks или explicit `SELECT FOR UPDATE`.
- Триггеры полагаются на row locks при `UPDATE inventory` и `INSERT ... ON CONFLICT DO UPDATE`.
- Read-then-write операции (`unpack_from_container`, `block_empty_container`, `find_available_location`) не имеют явной защиты от гонок в DDL.

## Мягкие резервы

- Мягкий резерв не должен изменять `wms.inventory`.
- Мягкий резерв не должен создавать записи в `wms.movements`.
- Идемпотентность текущего состояния резервов обеспечивается UPSERT по `(source_type, product_id, external_order_id)`.
- Все входящие события резервов должны попадать в `wms.stock_reservation_events`, включая `unknown_status`, `product_not_found` и `invalid_payload`.
- Бизнес-ошибки резервов ACK-аются после успешной записи audit.
- Ошибки БД/транзакции при обработке резервов должны приводить к retry/NACK со стороны RabbitMQ consumer.

## External FBS invariants

- `fbs_shipments.source` принимает только `standard` и `external_detected`.
- Успешно обработанный FBS item обязан иметь `movement_id`.
- Все items одной успешно обработанной product group получают один `movement_id`.
- `assembly_task.is_shipped` и movement атомарны; повторное списание assembly task запрещено.

## Kit operations invariants

- `operation_locations.operation_code='kit_operations'` и `scope='direct'` определяют разрешённые локации комплектации.
- `operation_locations.scope` должен быть `direct` для текущего MVP.
- `operation_locations` не должно иметь дублей по `(operation_code, location_id, scope)`; это должен обеспечивать unique index `uq_operation_locations_operation_location_scope`.
- `POST /api/kit-operations` должен проверять активную строку `operation_locations` перед проверкой остатков и созданием movements.
- Для kit operations нельзя требовать `locations.level=5`; допустима любая активная WMS location, явно разрешённая в `operation_locations`.
- Direct scope означает, что расходные остатки ищутся только по `inventory.location_id = operation_locations.location_id`; дочерние адреса не учитываются.
- `kit_operations.operation_location_id` должен ссылаться на использованную разрешённую локацию.
- `kit_operations.operation_type` должен быть `assembly` или `disassembly`.
- `kit_operations.status` должен быть `processing`, `completed` или `failed`.
- `kit_operations.quantity > 0`.
- `kit_operation_items.role` должен быть одной из ролей: `component_consumption`, `kit_result`, `kit_consumption`, `component_result`.
- `kit_operation_items.quantity_per_kit > 0` и `total_quantity > 0`.
- `kit_operation_items.movement_id` должен указывать на созданный movement, но FK не enforced, потому что parent `wms.movements` не имеет PK/unique constraint.
- Для write flow kit operations приложение обязано использовать transaction, advisory lock по `kit_product_id + location_id` и row lock расходных inventory rows.
- Kit operations должны менять остатки только через insert в `wms.movements`; прямой update/insert/delete `wms.inventory` в этом flow запрещен.
- Kit operations MVP расходует только loose stock: `status='available'`, `batch_number IS NULL`, `container_code IS NULL`.

## Re-sorting invariants

Completed операция имеет две разные роли и одинаковое положительное целое quantity. Оба movements имеют `movement_type=re_sorting`, `source_type=re_sorting_operation`, положительное quantity и в сумме направленный net delta 0. Конкурентность защищают canonical-pair advisory lock и source inventory row lock.
