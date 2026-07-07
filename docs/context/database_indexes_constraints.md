# Database Indexes and Constraints

Источник: `docs/context/wms_schema.sql`.

## Primary keys

PK есть на `container_contents(content_id)`, `containers(container_id)`, `fbs_shipment_items(item_id)`, `fbs_shipments(shipment_id)`, `inventory(inventory_id)`, `inventory_snapshots(snapshot_id)`, `locations(location_id)`, `notifications(notification_id)`, `receipt_items(receipt_item_id)`, `task_items(item_id)`, `tasks(task_id)`.

На parent table `wms.movements` PK в DDL не задан.

## Unique constraints

- `containers(qr_code)` - уникальный QR.
- `locations(location_code)` - уникальный код адреса.
- `container_contents(container_id, product_id, batch_number, status)` - уникальность состава с учетом обычной PostgreSQL NULL-семантики.
- `inventory UNIQUE NULLS NOT DISTINCT (product_id, location_id, status, batch_number, container_code)` - ключ upsert для materialized stock.
- `receipt_items(guid, product_id)` - уникальность поступления из 1С по документу и товару.

## Check constraints

Есть checks для container type/status, content quantity/status, inventory quantity/status, location zone type, movement type, task type/status, FBS shipment/item status, receipt quantity.

Отсутствуют: `movements.quantity > 0`; movement side requirement; `task_items.quantity_planned > 0`; `notifications.severity`; `locations.level` range; FK/check для корректности `container_code`.

## Foreign keys

Есть FK от containers к locations/self, container_contents к containers/products, inventory к products/locations, movements к products/from/to locations, locations к parent location, tasks к users/locations/parent task, task_items к tasks/products/locations, notifications к users/tasks, FBS items к FBS shipments, receipt_items к products.

Отсутствуют FK: `movements.from_container_id/to_container_id -> containers`; `movements.container_code -> containers.qr_code`; `fbs_shipment_items.movement_id -> movements`; `inventory_snapshots.product_id/location_id -> products/locations`; `tasks.related_movement_id -> movements`.

## LTREE/path indexes

- `idx_locations_path USING gist(path)` - нужен для LTREE operators, в частности `get_child_locations` с `path <@ v_path`.
- `idx_locations_parent(parent_location_id)` - прямой обход дерева.
- `idx_locations_code(location_code)` - поиск по коду, дублирует access path unique constraint.
- `idx_locations_active(is_active) WHERE is_active=true` - активные адреса.
- `idx_locations_zone_type(zone_type) WHERE zone_type IS NOT NULL` - фильтр зон, используется в `find_available_location`.

## Inventory indexes

`idx_inventory_product`, `idx_inventory_location`, `idx_inventory_product_location`, `idx_inventory_status`, `idx_inventory_batch`, `idx_inventory_container WHERE container_code IS NOT NULL`. Они покрывают поиск остатков по товару, адресу, статусу, партии и контейнеру; `uq_inventory` нужен для upsert из movement trigger.

## Movements indexes

Parent indexes: `created_at`, `product_id`, `(product_id, created_at)`, `from_location_id`, `to_location_id`, `movement_type`, `container_code`. Для каждой партиции `movements_2026_01` ... `movements_2026_12` созданы local indexes и attached к parent indexes.

Вероятные запросы: история товара/контейнера, отчеты по датам, пересчет/аудит movements, история адресов, FIFO/FEFO по `(product_id, created_at)`.

## Tasks indexes

`idx_tasks_status` partial для активных статусов; `idx_tasks_priority(priority, created_at) WHERE status='pending'` для очереди; `idx_tasks_assigned`, `idx_tasks_created_by`, `idx_tasks_from_location`, `idx_tasks_to_location`, `idx_tasks_parent`; `idx_task_items_task`, `idx_task_items_product`, `idx_task_items_from_location`.

## FBS retry indexes

- `idx_fbs_shipment_items_next_retry(next_retry_at) WHERE status='pending_retry'` - основной индекс retry worker.
- `idx_fbs_shipment_items_status(status)` - фильтр по статусу.
- `idx_fbs_shipment_items_shipment_id(shipment_id)` - позиции shipment.

## Containers and other indexes

Containers: QR, location, parent, status partial `status <> 'empty'`, type. Contents: container, product, batch, active status partial.

Notifications: `(user_id, is_read)`, `created_at DESC`, `notification_type`. Snapshots: `(snapshot_date, product_id)`, product, location. Receipts: guid, product, supplier partial. Materialized stock: unique `mv_product_stock(product_id)` for concurrent refresh.

## FBS source (2026-06-14)

- `chk_fbs_shipments_source`: `source IN (standard, external_detected)`.
- `idx_fbs_shipments_source_received_at(source, received_at DESC)`.
- `idx_fbs_shipments_source_status(source, status)`.

## Kit operations (2026-07-07)

- `wms.movements` check `chk_movement_type` расширен значениями `kit_assembly` и `kit_disassembly`.
- `wms.movements` добавлены nullable поля `source_type`, `source_id`, `source_item_id` и индекс `idx_movements_source(source_type, source_id, source_item_id)`.
- `wms.operation_locations`: PK `operation_location_id`; FK на `wms.locations(location_id)`; unique `(operation_code, location_id, scope)`; check `scope IN (direct)`.
- Индексы для разрешённых локаций: `idx_operation_locations_code_active`, `idx_operation_locations_location_active`.
- `wms.kit_operations`: PK `operation_id`; FK на `wms.operation_locations(operation_location_id)`, `public.products(id)` и `wms.locations(location_id)`; checks для `operation_type`, `status`, `quantity > 0`.
- `wms.kit_operation_items`: PK `item_id`; FK на `wms.kit_operations(operation_id)` и `public.products(id)`; checks для `role`, `quantity_per_kit > 0`, `total_quantity > 0`.
- Индексы: `idx_kit_operations_created_at`, `idx_kit_operations_filters`, `idx_kit_operations_operation_location`, `idx_kit_operation_items_operation`.
- FK `kit_operation_items.movement_id -> wms.movements` не добавлен, потому что parent `wms.movements` не имеет PK/unique constraint.
