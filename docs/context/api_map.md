# API Map

Базовый префикс API: `settings.API_V1_PREFIX`, по умолчанию `/api`.

Также есть системные endpoints без этого префикса:

- `GET /` - информация о сервисе.
- `GET /health` - health check.

## Locations

Префикс: `/api/locations`.

- `POST /api/locations` - создать локацию.
- `GET /api/locations/zones` - список активных зон (`level = 1`).
- `GET /api/locations/zones/tree` - дерево локаций с ограничением `max_level`.
- `GET /api/locations/{location_id}` - локация по ID.
- `GET /api/locations/by-code/{location_code}` - локация по коду.
- `GET /api/locations/{location_id}/children` - дочерние локации, рекурсивно или только прямые.
- `PUT /api/locations/{location_id}` - обновить параметры локации.
- `PATCH /api/locations/{location_id}/deactivate` - деактивировать локацию.
- `GET /api/locations/find-available` - найти доступную ячейку через `wms.find_available_location`.
- `GET /api/locations/{zone_id}/qr-codes` - ZIP с PDF QR-ярлыками для зоны.
- `GET /api/locations/{location_id}/qr-code` - ZIP с QR-ярлыком одной локации.

## Inventory

Префикс: `/api/inventory`.

- `GET /api/inventory/product/{product_id}` - остатки товара по локациям.
- `GET /api/inventory/location/{location_id}` - остатки в локации.
- `GET /api/inventory/location/{location_id}/recursive-summary` - агрегированные остатки по локации и всем дочерним локациям.
- `GET /api/inventory/location/by-code/{location_code}` - остатки в локации по коду.
- `GET /api/inventory/summary` - агрегированные остатки через `wms.v_product_stock`.
- `GET /api/inventory/container/{qr_code}` - остатки в контейнере.
- `GET /api/inventory/location/{location_id}/loose` - россыпь в локации.
- `GET /api/inventory/search` - поиск по товару, названию, партии или контейнеру.
- `GET /api/inventory/availability` - список доступности товаров с фильтрами `product_id`, `only_shortage`, `only_reserved`, `limit` до 5000, `offset`.
- `GET /api/inventory/availability/totals` - агрегаты доступности по всем товарам.
- `GET /api/inventory/product/{product_id}/availability` - доступность товара: физический остаток, активный мягкий резерв, свободный остаток и нехватка.
- `GET /api/inventory/location/{location_id}/availability` - доступность товаров по subtree локации с глобальным мягким резервом.
- `GET /api/inventory/reservations` - read-only список текущих мягких резервов с фильтрами.
- `GET /api/inventory/reservation-events` - read-only audit входящих событий резервов с фильтрами.

## Movements

Префикс: `/api/movements`.

- `POST /api/movements` - создать batch movements, 1-500 элементов, атомарно. Используется также для ручной корректировки остатков через `movement_type="adjust"`: приходная корректировка задается через `to_location_code`, расходная - через `from_location_code`.
- `GET /api/movements` - история движений с фильтрами.
- `GET /api/movements/product/{product_id}` - история движений товара.

## Kit Operations

Префикс: `/api/kit-operations`.

- `GET /api/kit-operations/locations` - список разрешённых локаций комплектации с фильтрами `is_active`, `limit`, `offset`.
- `POST /api/kit-operations/locations` - добавить или реактивировать разрешённую direct-локацию для комплектаций.
- `PATCH /api/kit-operations/locations/{operation_location_id}/deactivate` - деактивировать разрешённую локацию.
- `POST /api/kit-operations` - выполнить комплектацию (`operation_type=assembly`) или разукомплектацию (`operation_type=disassembly`) комплекта. Принимает `location_code`, но это не произвольный адрес: код должен быть разрешен в `wms.operation_locations` для `operation_code='kit_operations'`, `scope='direct'`, `is_active=true`.
- `GET /api/kit-operations` - список операций с фильтрами `operation_type`, `kit_product_id`, `status`, `location_code`, `date_from`, `date_to`, `limit`, `offset`.
- `GET /api/kit-operations/{operation_id}` - детальная карточка операции, строки `wms.kit_operation_items` с ролями и созданные movement-связи.

Роли строк: `component_consumption`, `kit_result`, `kit_consumption`, `component_result`. `scope='direct'` означает работу только с остатками на выбранной `location_id`; subtree дочерних адресов не используется.

## Containers

Префикс: `/api/containers`.

- `POST /api/containers/register` - зарегистрировать контейнер и содержимое через `wms.register_container`.
- `GET /api/containers/{qr_code}` - получить контейнер по QR.
- `PUT /api/containers/{container_id}/location` - переместить контейнер.
- `POST /api/containers/{container_id}/unpack` - извлечь товар из контейнера в россыпь через `wms.unpack_from_container`.
- `PATCH /api/containers/{container_id}/status` - изменить статус контейнера, кроме уже заблокированного.
- `GET /api/containers/{qr_code}/history` - история движений контейнера.
- `GET /api/containers/location/{location_id}` - контейнеры в локации.

## Tasks

Префикс: `/api/tasks`.

- `GET /api/tasks` - список заявок с фильтрами.
- `GET /api/tasks/my` - активные заявки сотрудника.
- `GET /api/tasks/available` - свободные pending-заявки.
- `GET /api/tasks/{task_id}` - детальная карточка заявки.
- `POST /api/tasks` - создать заявку с позициями.
- `PUT /api/tasks/{task_id}` - обновить pending/assigned заявку.
- `DELETE /api/tasks/{task_id}` - отменить pending/assigned заявку.
- `PUT /api/tasks/{task_id}/assign` - взять заявку в работу.
- `PUT /api/tasks/{task_id}/start` - начать выполнение заявки.
- `PUT /api/tasks/{task_id}/complete` - завершить заявку с фактическими данными.
- `GET /api/tasks/{task_id}/suggestions` - FIFO-подсказки по ячейкам.
- `PUT /api/tasks/{task_id}/approve-discrepancy` - подтвердить расхождение.
- `PUT /api/tasks/{task_id}/reject-discrepancy` - отклонить расхождение.
- `PUT /api/tasks/{task_id}/recount` - отправить на пересчет.
- `PUT /api/tasks/{task_id}/complete-recount` - завершить пересчет.

## Reports

Префикс: `/api/reports`.

- `GET /api/reports/zones` - отчет по зонам.
- `GET /api/reports/top-products` - топ товаров по движениям.
- `GET /api/reports/abc-analysis` - ABC-анализ.
- `GET /api/reports/turnover` - оборачиваемость.
- `GET /api/reports/batches` - отчет по партиям FIFO/FEFO.

## System

Префикс: `/api/system`.

- `GET /api/system/audit-summary` - read-only count-проверки известных рисков качества данных.
- `POST /api/system/validate-integrity` - сверить `inventory` с расчетом из `movements`.
- `POST /api/system/recalculate-inventory` - удалить и пересчитать остатки из `movements`.
- `POST /api/system/create-snapshot` - создать снимок остатков.
- `POST /api/system/refresh-materialized-views` - обновить `wms.mv_product_stock`.

## Notifications

Префикс: `/api/notifications`.

- `GET /api/notifications/unread` - непрочитанные уведомления пользователя.
- `PUT /api/notifications/{notification_id}/read` - пометить уведомление прочитанным.

## FBS Shipments

Префикс: `/api/fbs-shipments`.

- `POST /api/fbs-shipments` - принять непустой массив FBS-позиций по HTTP, сохранить с `source=http_api` и синхронно передать в общий pipeline списания.
- `GET /api/fbs-shipments/stats` - статистика по статусам.
- `GET /api/fbs-shipments` - список записей журнала.
- `GET /api/fbs-shipments/{shipment_id}` - детали записи с raw message и items.
- `POST /api/fbs-shipments/retry` - массовая переобработка validation_failed.
- `POST /api/fbs-shipments/{shipment_id}/retry` - переобработка одной записи.

## FBS source и item retry (2026-06-14)

- `GET /api/fbs-shipments?source=standard|external_detected|http_api` - фильтр истории по источнику.
- `GET /api/fbs-shipments/stats?source=standard|external_detected|http_api` - статистика по источнику.
- `GET /api/fbs-shipments/{shipment_id}` - возвращает `source`.
- `POST /api/fbs-shipments/items/{item_id}/retry` - ручной retry `failed/pending_retry/retry_exhausted` позиции.

## Re-sorting operations

- `GET /api/re-sorting-operations/locations` — allow-list пересортицы.
- `POST /api/re-sorting-operations/locations` — добавить/реактивировать direct-location.
- `PATCH /api/re-sorting-operations/locations/{operation_location_id}/deactivate` — деактивировать только re-sorting permission.
- `POST /api/re-sorting-operations` — атомарно выполнить пересортицу.
- `GET /api/re-sorting-operations` — журнал с фильтрами и pagination.
- `GET /api/re-sorting-operations/{operation_id}` — header и две item-строки.
# Дневная история остатков

`GET /api/inventory-history/daily-balances` — read-only восстановление дневного
`available`-остатка исключительно по `wms.movements`.

Обязательные query parameters: `date_from`, `date_to` (включительно, календарные даты
`Europe/Moscow`). Необязательные: `product_id`, `location_id`, `include_subtree=false`,
`limit=100` (1..500), `offset=0`. Максимальный период — 366 дней. Subtree допустим
только вместе с location; отсутствующая location возвращает 404.

Response содержит метаданные фильтра и пагинации, `total_products`, а также товары с
`product_id`, nullable `product_name` и полным календарным массивом `days`. День содержит
`opening_quantity`, `incoming_quantity`, `outgoing_quantity`, `closing_quantity`.
Пагинация и сортировка применяются к товарам (`product_id ASC`), внутри товара дни идут
по возрастанию. Пустая выборка возвращает 200 и `items=[]`.
# Единый список бизнес-операций

`GET /api/operations-history` — read-only список, нормализованный через `UNION ALL`
четырёх источников: `wms.kit_operations`, `wms.re_sorting_operations`,
`wms.fbs_shipments` и самостоятельных `wms.movements`.

Обязательные параметры: `date_from`, `date_to` — включительные календарные даты
`Europe/Moscow`, максимум 366 дней. Необязательные фильтры: `source_type`,
`operation_type`, `product_id`, точный `location_id`, точный `author`, `status`,
`limit=100` (1..200), `offset=0`. Сортировка стабильна: `created_at DESC, event_id DESC`;
пагинация выполняется после UNION.

Source discriminators: `kit_operation`, `re_sorting_operation`, `fbs_shipment`,
`movement`. Event IDs: `kit_operation:<id>`, `re_sorting:<id>`,
`fbs_shipment:<id>`, `movement:<movement_id>:<created_at_epoch_us>`.

Kit/re-sorting movements исключаются из самостоятельной ветки по структурному
`source_type`. FBS movement исключается только при ровно одном совпадении его
`movement_id` во всей partitioned `wms.movements`. Reason, время, author, document number,
container code и regex для группировки не используются. Failed/validation_failed FBS
headers и headers без items остаются в выдаче.

Receipt, task и container business headers в MVP не включены; их не поглощённые
физические effects видны как самостоятельные movements.

`GET /api/operations-history/{event_id}` открывает typed detail для тех же четырёх
источников. Форматы: `kit_operation:<id>`, `re_sorting:<id>`, `fbs_shipment:<id>` и
`movement:<movement_id>:<created_at_epoch_us>`. Некорректный ID возвращает 400,
отсутствующий объект — 404. Business detail сохраняет header/items даже при потерянной
или неоднозначной movement-ссылке и сообщает проблему в `warnings`.

# История документа поступления

GET /api/receipts/history — периодический список для frontend-таблицы. Одна строка
представляет legacy_revision либо wms_snapshot_only; один GUID может повторяться.
Период относится к revision/snapshot time в Europe/Moscow, undated legacy доступны
через include_undated=true. Фильтры и пагинация применяются после группировки.
Для открытия detail frontend передаёт item.guid в /api/receipts/{guid}/history;
row_id служит только глобальным ключом строки.

`GET /api/receipts/{guid}/history?limit=50&offset=0` — read-only история документа.
Legacy revisions читаются из `public.supply_to_sellers_warehouse`, current snapshot —
из `wms.receipt_items`. Пагинация применяется к revisions. GUID сравнивается как строка,
без UUID parsing. Документ, отсутствующий в обоих источниках, возвращает 404.
