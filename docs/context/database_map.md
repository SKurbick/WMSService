# Database Map

Источник: `docs/context/wms_schema.sql`, dump PostgreSQL 17.4. Описано только то, что следует из DDL; поведение Python/API не считается доказанным, если оно не выражено таблицами, constraints, функциями или триггерами.

## Схемы, типы, views

- `wms` - схема WMS.
- `public` - внешние таблицы, на которые есть FK/joins: `products`, `users`, `user_permissions`.
- В DDL нет `CREATE TYPE`, `CREATE DOMAIN`, `CREATE EXTENSION`.
- Используется `wms.ltree` в `locations.path` и функциях. Создание extension/type отсутствует в файле и должно обеспечиваться вне этого DDL.
- Views: `v_product_stock`, `mv_product_stock`, `v_container_contents_current`, `v_container_details`, `v_tasks_with_users`.

## `wms.locations`

Назначение: иерархия склада и адресов хранения через `parent_location_id` и LTREE `path`.

Поля: `location_id`, `parent_location_id`, `location_code`, `path`, `name`, `zone_type`, `level`, `max_weight`, `max_volume`, `is_active`, `is_pickable`, `metadata`, `created_at`, `updated_at`.

Ограничения: PK `location_id`; unique `location_code`; FK `parent_location_id -> wms.locations(location_id) ON DELETE RESTRICT`; check `zone_type` in `receiving/storage/picking/packing/shipping/quarantine/NULL`. Defaults: `is_active=true`, `is_pickable=false`, timestamps `now()`, sequence for id.

Связи: referenced by `containers`, `inventory`, `movements`, `tasks`, `task_items`, `operation_locations`, `kit_operations`. `get_child_locations` использует `path <@ parent_path`.

## `wms.inventory`

Назначение: материализованное текущее состояние остатков, восстанавливаемое из `movements`.

Поля: `inventory_id`, `product_id`, `location_id`, `quantity`, `status`, `batch_number`, `container_code`, `created_at`, `updated_at`.

Ограничения: PK `inventory_id`; unique `UNIQUE NULLS NOT DISTINCT (product_id, location_id, status, batch_number, container_code)`; FK `product_id -> public.products(id)`, `location_id -> wms.locations(location_id)` with `ON DELETE RESTRICT`; check `quantity >= 0`; check `status` in `available/damaged/quarantine`. Defaults: `status='available'`, timestamps `now()`, sequence for id.

Связи: обновляется `trg_update_inventory_from_movement` после insert в `movements`; контейнерное хранение связывается строкой `container_code = containers.qr_code` без FK.

## `wms.movements`

Назначение: event log движений товаров и источник правды для пересчета inventory.

Поля: `movement_id`, `movement_type`, `product_id`, `from_location_id`, `to_location_id`, `quantity`, `batch_number`, `container_code`, `from_container_id`, `to_container_id`, `user_name`, `reason`, `metadata`, `source_type`, `source_id`, `source_item_id`, `created_at`.

Ограничения: partitioned by range `created_at`; FK `product_id -> public.products(id)`, `from_location_id/to_location_id -> locations(location_id)`; check `movement_type` in `receive/putaway/transfer/pick/ship/unpack/adjust/kit_assembly/kit_disassembly`; defaults `movement_id=nextval(...)`, `created_at=now()`. PK на parent table в DDL не задан. Нет check на `quantity > 0` и нет check, что заполнена хотя бы одна сторона movement.

Для kit operations используются source-связи: `source_type='kit_operation'`, `source_id = wms.kit_operations.operation_id`, `source_item_id = wms.kit_operation_items.item_id`. FK на эти source-поля отсутствует.

Партиции: `movements_2026_01` ... `movements_2026_12` с месячными диапазонами UTC, соответствующими московской границе месяца (`21:00:00+00`).

## `wms.containers`

Назначение: QR-контейнеры с типом, статусом, вложенностью и текущей локацией.

Поля: `container_id`, `qr_code`, `container_type`, `parent_container_id`, `location_id`, `status`, `metadata`, `created_at`, `updated_at`.

Ограничения: PK `container_id`; unique `qr_code`; FK `location_id -> locations ON DELETE RESTRICT`; FK `parent_container_id -> containers ON DELETE RESTRICT`; check `container_type` in `pallet/box/unit`; check `status` in `sealed/opened/empty/blocked`. Defaults: `status='sealed'`, timestamps `now()`, sequence for id.

Связи: `container_contents.container_id`; при изменении `location_id` trigger создает transfer movements для inventory rows с `container_code = NEW.qr_code`.

## `wms.container_contents`

Назначение: состав контейнеров по товарам/партиям.

Поля: `content_id`, `container_id`, `product_id`, `quantity`, `batch_number`, `is_scanned`, `status`, `created_at`, `updated_at`.

Ограничения: PK `content_id`; unique `(container_id, product_id, batch_number, status)`; FK `container_id -> containers ON DELETE CASCADE`; FK `product_id -> public.products(id) ON DELETE RESTRICT`; check `quantity > 0`; check `status` in `active/replaced/removed`. Defaults: `is_scanned=false`, `status='active'`, timestamps `now()`, sequence for id.

Связи: after insert trigger создает receive movement только для `status='active'`. Важное несоответствие: `unpack_from_container` пытается получить `quantity=0` и `status='empty'`, но DDL это запрещает.

## `wms.tasks` и `wms.task_items`

`tasks` хранит складские заявки. Поля: `task_id`, `task_type`, `status`, `priority`, `from_location_id`, `to_location_id`, `assigned_to`, `assigned_at`, `due_date`, `started_at`, `completed_at`, `reason`, `notes`, `related_movement_id`, `parent_task_id`, `metadata`, `created_by`, timestamps.

Ограничения `tasks`: PK `task_id`; FK на `locations`, `public.users`, parent task; check `task_type` in `replenishment/transfer/picking/putaway/inventory/discrepancy_approval/recount`; check `status` in `pending/assigned/in_progress/pending_approval/pending_recount/completed/completed_with_discrepancy/cancelled`; defaults `status='pending'`, `priority=5`, timestamps.

`task_items` хранит позиции заявки. Поля: `item_id`, `task_id`, `product_id`, `quantity_planned`, `quantity_actual`, `from_location_id`, `batch_number`, `discrepancy_reason`, `created_at`. Ограничения: PK; FK `task_id -> tasks ON DELETE CASCADE`; FK product/location. В DDL нет check на положительное `quantity_planned`.

## Остальные таблицы

### `wms.notifications`

Уведомления пользователей: `notification_id`, `user_id`, `notification_type`, `title`, `message`, `severity`, `related_task_id`, `metadata`, `is_read`, `read_at`, `created_at`. PK; FK `user_id -> public.users ON DELETE CASCADE`; FK `related_task_id -> tasks ON DELETE CASCADE`; defaults `severity='info'`, `is_read=false`, `created_at=now()`. Check на `severity` отсутствует.

### `wms.inventory_snapshots`

Исторические снимки остатков: `snapshot_id`, `snapshot_date`, `product_id`, `location_id`, `container_code`, `quantity`, `status`, `created_at`. PK и default timestamp/id. FK и unique для снимка отсутствуют.

### `wms.fbs_shipments`

Входящие FBS-сообщения: `shipment_id`, `received_at`, `raw_message`, `total_items`, `status`, `error_message`, `completed_at`. PK; check `status` in `processing/completed/partially_completed/failed/validation_failed`; defaults `received_at=now()`, `status='processing'`.

### `wms.fbs_shipment_items`

Позиции FBS и retry: `item_id`, `shipment_id`, `product_id`, `quantity`, `author`, `supply_id`, `account`, `assembly_tasks`, `warehouse_id`, `delivery_type`, `wb_warehouse`, `shipment_date`, `status`, `error_message`, `retry_count`, `max_retries`, `next_retry_at`, `movement_id`, timestamps. PK; FK `shipment_id -> fbs_shipments ON DELETE CASCADE`; check `status` in `new/success/failed/pending_retry/retry_exhausted`; defaults `status='new'`, `retry_count=0`, `max_retries=5`. FK `movement_id -> movements` отсутствует.

### `wms.receipt_items`

Snapshot поступлений из 1С: `receipt_item_id`, `guid`, `product_id`, `quantity`, `document_number`, `supplier_name`, `supplier_code`, timestamps. PK; unique `(guid, product_id)`; FK `product_id -> public.products(id) ON DELETE RESTRICT`; check `quantity >= 0`.

## `wms.operation_locations`

Назначение: список WMS-локаций, где разрешены конкретные доменные операции.

Поля: `operation_location_id`, `operation_code`, `location_id`, `location_code`, `scope`, `is_active`, `author`, `metadata`, `created_at`, `updated_at`.

Ограничения: PK `operation_location_id`; FK `location_id -> wms.locations(location_id)`; unique index `uq_operation_locations_operation_location_scope` on `(operation_code, location_id, scope)`; check `scope in ('direct')`.

Для комплектаций используется `operation_code='kit_operations'`, `scope='direct'`, `is_active=true`. Проверка `locations.level=5` не применяется; разрешенной может быть любая активная WMS location, если она явно добавлена в allow-list.

## `wms.kit_operations`

Назначение: журнал операций комплектации и разукомплектации комплектов.

Поля: `operation_id`, `operation_location_id`, `operation_type`, `kit_product_id`, `quantity`, `location_id`, `location_code`, `author`, `status`, `created_at`, `completed_at`.

Ограничения: PK `operation_id`; FK `operation_location_id -> wms.operation_locations(operation_location_id)`; FK `kit_product_id -> public.products(id)`; FK `location_id -> wms.locations(location_id)`; check `operation_type` in `assembly/disassembly`; check `status` in `processing/completed/failed`; check `quantity > 0`.

## `wms.kit_operation_items`

Назначение: строки операций комплектов и связь строк с созданными movements.

Поля: `item_id`, `operation_id`, `role`, `product_id`, `quantity_per_kit`, `total_quantity`, `movement_id`, `movement_created_at`, `created_at`.

Ограничения: PK `item_id`; FK `operation_id -> wms.kit_operations(operation_id) ON DELETE CASCADE`; FK `product_id -> public.products(id)`; check `role` in `component_consumption/kit_result/kit_consumption/component_result`; check `quantity_per_kit > 0`; check `total_quantity > 0`. FK на `movement_id` отсутствует из-за отсутствия PK/unique constraint на parent `wms.movements`.

Роли: `component_consumption` - списание компонента при `assembly`; `kit_result` - приход готового комплекта при `assembly`; `kit_consumption` - списание комплекта при `disassembly`; `component_result` - приход компонентов при `disassembly`.

## `wms.stock_reservation_orders`

Назначение: текущее состояние мягких резервов товара по `source_type + product_id + external_order_id`. Таблица ожидается созданной в БД вне кода приложения.

Ожидаемые поля по ТЗ: `reservation_order_id`, `source_type`, `product_id`, `external_order_id`, `external_status`, `is_reserved`, `reserved_qty`, `external_created_at`, `last_event_at`, `raw_payload`, `created_at`, `updated_at`.

Ожидаемое ограничение: unique `(source_type, product_id, external_order_id)`, используемый для идемпотентного UPSERT.

## `wms.stock_reservation_events`

Назначение: audit всех входящих событий мягких резервов, включая бизнес-ошибки и невалидные payload. Таблица ожидается созданной в БД вне кода приложения.

Ожидаемые поля по ТЗ: `reservation_event_id`, `source_type`, `product_id`, `external_order_id`, `external_status`, `reserved_qty`, `external_created_at`, `event_received_at`, `processing_result`, `error_message`, `raw_payload`.

## `wms.v_product_availability`

Назначение: read-only view доступности товара с учетом физического available остатка и активного мягкого резерва.

Ожидаемые поля: `product_id`, `physical_qty`, `reserved_qty`, `free_qty`, `shortage_qty`. `free_qty` может быть отрицательным.

## FBS source (2026-06-14)

`wms.fbs_shipments.source varchar(30) NOT NULL DEFAULT standard` различает standard и external-detected потоки. Constraint разрешает только `standard/external_detected`; добавлены индексы `(source, received_at DESC)` и `(source, status)`.
