# Invariants

Источник: `docs/archive/snapshots/wms_schema.sql` плюс явно отмеченные требования, которые должны обеспечиваться приложением.

## Enforced by DB

- `locations.location_code` уникален.
- `locations.path` обязателен и имеет тип `wms.ltree`.
- `locations.parent_location_id`, если заполнен, ссылается на существующую location.
- `containers.qr_code` уникален.
- `containers.container_type` входит в `pallet/box/cage/trolley`.
- `containers.status` входит в `empty/open/sealed/blocked`.
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

- `fbs_shipments.source` принимает только `standard`, `external_detected` и `http_api`.
- Успешно обработанный FBS item обязан иметь `movement_id`.
- Все items одной успешно обработанной product group получают один `movement_id`.
- `assembly_task.is_shipped` и movement атомарны; повторное списание assembly task запрещено.
- Movement, inventory trigger, `assembly_task.is_shipped`, item `success/movement_id` и
  пересчёт родительского shipment выполняются одной product-group транзакцией.
- FBS ship movement с `user_name IN ('FBS-service', 'FBS 2.0')` не должен оставаться без
  связи через `fbs_shipment_items.movement_id`.
- `fbs_shipment_items.status='success'` требует непустой `movement_id`.

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
- Kit operations MVP расходует только доступный россыпной остаток: `status='available'`, `batch_number IS NULL`, `container_code IS NULL`.

## Re-sorting invariants

Completed операция имеет две разные роли и одинаковое положительное целое quantity. Оба movements имеют `movement_type=re_sorting`, `source_type=re_sorting_operation`, положительное quantity и в сумме направленный net delta 0. Конкурентность защищают canonical-pair advisory lock и source inventory row lock.

## KIZ v1: quantity и MVCC

Для exact available/NULL batch/NULL container scope:
COUNT(active KIZ) <= inventory.quantity; отсутствующая inventory row означает 0.
Counter-поля нет. Authoritative physical guard — BEFORE UPDATE/DELETE inventory.
Увеличение/неизменное quantity при прежнем ключе пропускается без count; смена ключа
или DELETE требуют нулевого active count. SQLSTATE нарушения — P7501.

KIZ writes выполняются транзакционным KizService на одной asyncpg connection.
Lock order: inventory FOR UPDATE → KIZ row → events. Assignment после inventory lock
делает служебный UPDATE updated_at без изменения quantity/key, затем count/INSERT/event.
Touch создаёт новую MVCC-версию и откатывается вместе с неуспешным assignment.
Конкурентный writer со старым REPEATABLE READ snapshot получает 40001.
Terminal сначала читает scope без lock, затем блокирует inventory и KIZ, проверяет
актуальный lifecycle. READ COMMITTED задан для собственных KIZ write transactions;
caller-owned RR assignment поддерживается через touch до count.

Нормальные KIZ inserts идут только через этот протокол; произвольный SQL INSERT KIZ,
отключение triggers/TRUNCATE или writes владельца вне протокола не являются
поддерживаемым write API. Ограничения runtime-role: [проверка прав](../database/kiz_v1_runtime_check.md).

## Movement identity Phase 1

Точная identity movement уникальна по `(movement_id, created_at)` на partitioned parent.
Каждый movement после завершённого backfill имеет ровно одну immutable строку
`movement_registry`; registry coordinate имеет composite FK к movement. Ошибка
автоматической регистрации откатывает movement и inventory projection в той же
transaction. Backfill не создаёт movements и не меняет inventory.

## KIZ association Phase 2

- `kiz_movement_links(kiz_id, movement_ref)` уникальна, имеет два FK RESTRICT и
  запрещает UPDATE/DELETE trigger-ом.
- В links нет quantity, product, location или копии movement coordinate.
- active требует location и `closed_at IS NULL`; shipped требует location NULL и
  `closed_at IS NOT NULL`; error/deactivated сохраняют location и terminal timestamp.
- Shipped event требует `movement_ref` и composite FK на link того же KIZ. Старые
  assigned/marked_as_error/deactivated events требуют NULL movement_ref.
- Прямой UPDATE `kiz.location_id` и active→shipped всё ещё запрещает identity guard.
  Поддерживаемые controlled transfer/ship являются отдельными KIZ operations.

## KIZ operation idempotency Phase 3

- Business key: `(source_system, operation_type, external_operation_id)`.
- Operation type ограничен `transfer/ship`; fingerprint — 64 lowercase SHA-256 hex.
- Intent row блокируется до будущих inventory/KIZ locks.
- К моменту commit `result_payload` обязателен; intent/items/result откатываются вместе.
- Identity/result immutable после заполнения. Item movement_ref заполняется не более
  одного раза и уникален среди KIZ operation items.
- Phase 3 сама не создаёт movements, KIZ links и не меняет inventory/KIZ.

## KIZ transfer invariants

- Для исходного скоупа `Q-N` не превышает неидентифицированный по КИЗ остаток;
  `0 <= N <= Q`.
- Один operation item создаёт один transfer movement полного Q.
- Каждый selected KIZ active, совпадает по product/source и связан с movement item.
- После commit активный КИЗ-идентифицированный остаток не превышает физический доступный
  россыпной остаток точной локации.
- Direct location UPDATE запрещён; controlled change не commit без полного
  operation/movement/registry/link/result graph.

## KIZ shipment invariants

- Для source `0 <= N <= Q` и `Q-N <= P-I`; shipped KIZ не входит в active count.
- Один operation item создаёт один `movement_type=ship` полного Q с NULL destination.
- Каждый selected KIZ повторно проверяется после row lock, переходит
  `active/source/closed_at NULL → shipped/NULL/closed_at NOT NULL` и получает link и
  shipped event с одним movement_ref.
- Direct lifecycle UPDATE запрещён. Deferred DB validation требует полный
  operation/item/movement/registry/link/event/result graph для каждого controlled KIZ.
- Transaction rollback не оставляет operation, lifecycle transition, movement,
  registry mapping, link, event или result.

## Container Stage 3B B1 invariants

- `container_id` — stable internal identity; `qr_code` UNIQUE и immutable.
- Hard DELETE container запрещён guard-trigger; supported QR reuse невозможен.
- `location_id NOT NULL`, `parent_container_id IS NULL`.
- Allowed status: `empty/open/sealed/blocked`; allowed type:
  `pallet/box/cage/trolley`.
- `empty` не может иметь active contents; active content нельзя добавить в `empty`.
- Content scope уникален с `NULLS NOT DISTINCT`; quantity current row остаётся `> 0`.
- `container_code` остаётся legacy text reference без FK; generic movement bypass до B4
  способен рассинхронизировать dual representation и не считается supported container flow.

## Container Stage 3B B2.1 invariants

- Для каждого успешного fill scope: `loose_after + contained_after = total_before`.
- `active container_contents.quantity = available contained inventory.quantity`.
- Каждая fill line имеет ровно два registry-backed movements: loose outgoing и contained incoming.
- Operation result и оба movement refs фиксируются атомарно; incomplete graph не проходит deferred commit guard.
- Container и inventory locks сериализуют supported conflicting writers; JSON item order не влияет на lock order/fingerprint.

## Container Stage 3B B2.2 invariants

- Для каждого успешного extract scope: `contained_after = contained_before - Q`,
  `loose_after = loose_before + Q`, physical total неизменен.
- Active `container_contents.quantity` равен available contained inventory quantity.
- Full extract удаляет active current row; zero-quantity content row не создаётся.
- Container status равен `empty` только после отсутствия active contents, иначе `open`.
- Каждая extract line имеет contained outgoing и loose incoming registry-backed transfer
  movements с общими `source_type/source_id/source_item_id`.
- Container lock, canonical inventory locks и canonical content locks сериализуют
  supported fill/extract/unpack/move writers; operation/result/movements/contents/status
  откатываются одной transaction.

## Container B3 invariants

- После move весь inventory с container QR находится только на `container.location_id`;
  каждый active content scope количественно равен exact contained inventory scope.
- После unpack-all нет active contents и inventory с container QR, status равен `empty`.
- Physical total product/NULL-safe batch сохраняется; committed operation graph содержит
  result и корректные single(move) либо paired(other operations) movement links.

## Container Stage 3B B4 invariants

- Public generic movement не принимает non-NULL `container_code`; batch rejected целиком.
- DB insert container-coded movement требует `source_type='container_operation'` и заполненные
  `source_id/source_item_id`.
- Поддерживаемые container physical writers: fill, extract, move, unpack-all.
- Register создаёт только `empty` container и никогда не создаёт contents/movements.
- Legacy location trigger и `unpack_from_container` отсутствуют после B4 migration.
- Task stock selection не смешивает loose/contained, statuses или batches.
- Recalculate допускается только при равенстве movement ledger, active contents и available
  contained inventory в текущей location контейнера до и после rewrite.

## Container/KIZ C1 invariants

- Active: `(location_id IS NOT NULL) XOR (container_id IS NOT NULL)`, `closed_at IS NULL`.
- Shipped: оба holder NULL, `closed_at IS NOT NULL`; terminal diagnostic holder не двойной.
- Для loose и contained exact scope: `active identified <= available batch-less physical`.
- Для container дополнительно `identified <= active container_contents.quantity`.
- Selected KIZ связан с обеими fill/extract/unpack legs; при move — с одним scope movement.
- Holder transition не commit без operation result и immutable movement links.
