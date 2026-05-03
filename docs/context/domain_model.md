# Domain Model

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
- `category`.

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
- `in_transit`;
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
