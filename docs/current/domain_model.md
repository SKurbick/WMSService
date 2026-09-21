# Domain Model

Статус: `CURRENT`.

## Склад и локации

Основная сущность адресного хранения - `wms.locations`.

Локации образуют иерархию:

- `level = 0` - склад;
- `level = 1` - зона;
- `level = 2..5` - вложенные уровни до ячейки.

В коде упоминаются уровни: склад, зона, стеллаж, секция, ярус, ячейка. Иерархия хранится через `parent_location_id` и `path` типа LTREE.

Типы зон (`ZoneType`):

- `warehouse`;
- `receiving`;
- `storage`;
- `picking`;
- `packing`;
- `shipping`;
- `quarantine`.

Поля локации по коду:

- `location_id`;
- `location_code`;
- `path`;
- `name`;
- `zone_type`;
- `level`;
- `max_weight`;
- `max_volume`;
- `is_active`;
- `is_pickable`;
- `metadata`;
- `parent_location_id`;
- `created_at`;
- `updated_at`.

## Товары / SKU

Товары не определены в этом сервисе как собственная модель. Сервис ссылается на `public.products`:

- `id`;
- `name`;
- `category`;
- `is_active` - используется kit operations;
- `is_kit` - признак комплекта для kit operations;
- `kit_components` - JSON-состав комплекта для kit operations.

`product_id` в WMS-таблицах соответствует `public.products.id`.

## Остатки

Остатки хранятся в `wms.inventory`.

Из кода используются поля:

- `inventory_id`;
- `product_id`;
- `location_id`;
- `quantity`;
- `status`;
- `batch_number`;
- `container_code`;
- `created_at`;
- `updated_at`.

Статусы остатков (`InventoryStatus`):

- `available`;
- `reserved`;
- `quarantine`;
- `damaged`.

Остатки могут быть:

- в контейнере (`container_code IS NOT NULL`);
- россыпью (`container_code IS NULL`).

## Движения

История операций хранится в `wms.movements`. Она является event log для изменения остатков.

Поля из кода:

- `movement_id`;
- `movement_type`;
- `product_id`;
- `from_location_id`;
- `to_location_id`;
- `quantity`;
- `batch_number`;
- `container_code`;
- `user_name`;
- `reason`;
- `created_at`.

Типы движений (`MovementType`):

- `receive`;
- `ship`;
- `transfer`;
- `adjust`;
- `write_off`;
- `unpack`.

Правило направления:

- `to_location_id` задает приход;
- `from_location_id` задает расход;
- transfer содержит оба направления.

## Контейнеры

Контейнеры хранятся в `wms.containers`, содержимое - в `wms.container_contents`.

Поля контейнера из кода:

- `container_id`;
- `qr_code`;
- `container_type`;
- `status`;
- `location_id`;
- `parent_container_id`;
- `metadata`;
- `created_at`;
- `updated_at`.

Типы контейнеров:

- `pallet`;
- `box`;
- `cage`;
- `trolley`.

Статусы контейнеров:

- `empty`;
- `sealed`;
- `open`;
- `blocked`.

Содержимое контейнера:

- `container_id`;
- `product_id`;
- `quantity`;
- `batch_number`;
- `is_scanned`;
- `status`.

## Заявки

Заявки хранятся в `wms.tasks`, позиции - в `wms.task_items`.

Типы заявок:

- `replenishment`;
- `transfer`;
- `picking`;
- `putaway`;
- `recount`;
- `discrepancy_approval`.

Статусы заявок:

- `pending`;
- `assigned`;
- `in_progress`;
- `completed`;
- `completed_with_discrepancy`;
- `pending_approval`;
- `pending_recount`;
- `waiting_recount`;
- `cancelled`.

Для расхождений создаются дочерние заявки `discrepancy_approval`, для пересчета - `recount`.

## Блокировки

В коде явно есть блокировка контейнера через статус `blocked`. Заблокированный контейнер нельзя перемещать, распаковывать и нельзя изменить его статус через обычный endpoint.

Отдельной модели блокировок адресов хранения в коде не найдено. Этот вопрос зафиксирован в `open_questions.md`.

## ФБС-отгрузки

ФБС-журнал:

- `wms.fbs_shipments` - raw-сообщение, общий статус обработки;
- `wms.fbs_shipment_items` - позиции списания и retry-статусы.

Позиции приходят из RabbitMQ, валидируются схемой `WriteOffAccordingToFBS` и списываются из локации `settings.FBS_LOCATION_CODE`.

Сервис также обращается к `public.assembly_task`, где проверяет существование сборочных заданий и атомарно помечает их `is_shipped = TRUE` вместе со списанием.

## Мягкие резервы товаров

Текущие мягкие резервы хранятся в `wms.stock_reservation_orders`. Идентичность резерва задается уникальной связкой `source_type + product_id + external_order_id`.

Поля текущего состояния:

- `reservation_order_id`;
- `source_type`;
- `product_id`;
- `external_order_id`;
- `external_status`;
- `is_reserved`;
- `reserved_qty`;
- `external_created_at`;
- `last_event_at`;
- `raw_payload`;
- `created_at`;
- `updated_at`.

Audit всех входящих событий резервов хранится в `wms.stock_reservation_events`, включая успешные события, повторные события, неизвестные статусы, неизвестные товары и невалидные payload.

Доступность товара читается из `wms.v_product_availability` и включает `physical_qty`, `reserved_qty`, `free_qty`, `shortage_qty`.

## Операции комплектов

Разрешённые места выполнения операций хранятся в `wms.operation_locations`.

`operation_locations`: `operation_location_id`, `operation_code`, `location_id`, `location_code`, `scope`, `is_active`, `author`, `metadata`, `created_at`, `updated_at`.

Для комплектаций используется `operation_code='kit_operations'`, `scope='direct'`. Direct scope означает, что используются только остатки непосредственно на `location_id`; subtree дочерних адресов не включается.

Операции комплектации/разукомплектации хранятся в `wms.kit_operations`, строки операции - в `wms.kit_operation_items`.

`kit_operations`: `operation_id`, `operation_location_id`, `operation_type` (`assembly/disassembly`), `kit_product_id`, `quantity`, `location_id`, `location_code`, `author`, `status`, `created_at`, `completed_at`.

`kit_operation_items`: `item_id`, `operation_id`, `role`, `product_id`, `quantity_per_kit`, `total_quantity`, `movement_id`, `movement_created_at`, `created_at`.

Источник состава комплекта - `public.products.kit_components`. Для assembly компоненты списываются, комплект приходуется; для disassembly комплект списывается, компоненты приходуются. Остатки меняются только через `wms.movements` с `movement_type = kit_assembly/kit_disassembly` и `source_type = kit_operation`.

## Re-sorting operation

`wms.re_sorting_operations` — audit header пересортицы; `wms.re_sorting_operation_items` — ровно две строки ролей `source_outgoing`/`target_incoming`. Каждая строка связана с movement посредством `(movement_id, movement_created_at)`. Allow-list хранится в общей `wms.operation_locations` с отдельным operation_code.

## KIZ v1

wms.kiz — identity одной физической единицы, а не отдельный количественный остаток.
product_id может одновременно иметь идентифицированную и неидентифицированную часть.
Активный KIZ привязан к точной location и занимает одну единицу доступного россыпного
остатка без партии и контейнера.
Terminal error/deactivated сохраняет историческую location, но не занимает остаток.
wms.kiz_events — неизменяемые identity events assigned/marked_as_error/deactivated,
без association с movements. Подробности: [KIZ v1](../flows/kiz_v1.md).

## Stable movement identity

`wms.movement_registry` отделяет внутренний stable `movement_ref` от физической
координаты partitioned ledger `(movement_id, movement_created_at)`. Registry не хранит
quantity или стороны движения и не является вторым ledger. Новые mappings создаются
DB trigger в transaction movement; legacy domain links пока используют прежние поля.

## KIZ movement association и shipped current state

`wms.kiz_movement_links` связывает одну identity KIZ с несколькими physical movements
во времени, а один movement — с несколькими KIZ. Единственная пара
`(kiz_id, movement_ref)` неизменяема; quantity/product/location берутся из movement.
`shipped` означает, что единица покинула склад: current `location_id` равна NULL,
а source location будущей отгрузки остаётся в linked outgoing movement и shipped event.
`error/deactivated` сохраняют прежнюю location. Explicit transfer и ship реализованы
как отдельные KIZ operations.

## KIZ operation idempotency

`wms.kiz_operations` хранит business key внешней команды, fingerprint physical intent
и сохранённый response snapshot. `wms.kiz_operation_items` хранит stable external line
и nullable `movement_ref`; physical data не дублируются. Один movement_ref принадлежит
максимум одной строке KIZ operation. Таблицы предназначены только для будущих KIZ
transfer/ship и не включены в legacy movement/FBS/task/kit/re-sorting flows.

## KIZ explicit transfer

`kiz_operations → kiz_operation_items → movement_ref → movement` фиксирует operation и
physical effect. `kiz_movement_links` связывает selected KIZ с тем же movement_ref.
`kiz.location_id` меняется на destination, lifecycle остаётся active. Transaction-local
`kiz_location_update_authorizations` не является ledger и пуст после commit.

## KIZ explicit shipment

`kiz_operations → kiz_operation_items → movement_ref → ship movement` фиксирует
operation и physical write-off. Selected KIZ получают immutable links и shipped events
с тем же movement_ref. Current state меняется `active/source → shipped/NULL`,
`closed_at` заполняется. Transaction-local `kiz_shipment_authorizations` существует
только для controlled DB protocol и удаляется deferred validation перед commit.

## KIZ unified history read model

История конкретного КИЗ не является новым ledger. Current state читается из `wms.kiz`,
identity/lifecycle — из `wms.kiz_events`, physical transfer/ship — по immutable graph
`kiz_movement_links → movement_registry → movements`. Historical locations берутся из
event/movement, не реконструируются из текущей `kiz.location_id`.

Один link означает участие одной identified unit в movement; `movement.quantity`
остаётся полным количеством движения. `shipped` event и linked ship movement одного
`movement_ref` представлены одной user-facing записью `ship` с обеими stable identities.

## Container identity Stage 3B B1

`container_id` — стабильная внутренняя identity; будущие domain links используют её.
`qr_code` — уникальная неизменяемая business identity. Supported hard delete и QR reuse
запрещены. Модель flat: parent всегда NULL, текущая location всегда задана напрямую.

Warehouse контейнера определяется корневым ancestor его location path; отдельное поле
на container не дублируется. Будущий move должен оставаться в одном таком warehouse.
`container_contents` и inventory/container_code пока являются dual representation.

## Container operation identity B2.1/B2.2

`container_operations.operation_id` — внутренняя identity команды, а business identity —
`(source_system, operation_type, external_operation_id)`. Immutable item связывает
`container_id`, external line, exact product/batch/quantity и два `movement_ref`.
`container_id`, а не QR, используется как domain target; QR сохраняется в response и
legacy inventory projection. `operation_type` разделяет business keys `fill` и `extract`.
Для extract item outgoing ref означает contained outgoing movement, incoming ref — loose
incoming movement. Result snapshot дополнительно фиксирует итоговый container status.

## Container operation identity B3

`container_operations.container_id` структурно хранит target даже для empty move без
items. Move item использует один `movement_ref`; fill/extract/unpack-all сохраняют пару
outgoing/incoming refs. Operation types: `fill/extract/move/unpack_all`. Move result
фиксирует обе location identities, unpack-all items являются server snapshot.

## KIZ container holder overlay (C1)

`wms.kiz.container_id` — nullable FK RESTRICT на `wms.containers.container_id`.
Direct holder active KIZ представлен location XOR container; QR и effective container
location являются join/read fields. `kiz_movement_links` остаётся identity association без
quantity. `kiz_container_holder_authorizations` — transaction-local protocol, не ledger.
