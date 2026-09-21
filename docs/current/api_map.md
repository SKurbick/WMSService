# API Map

Статус: `CURRENT`.

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

- `POST /api/movements` - создать атомарный batch loose-only movements, 1-500 элементов. `container_code` должен быть `null`/omitted; container stock меняют только `/api/container-operations/*`.
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

- `POST /api/containers/register` - создать пустой контейнер; `contents` обязан быть пустым.
- `GET /api/containers/{qr_code}` - получить контейнер по QR.
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
- `POST /api/system/recalculate-inventory` - пересчитать available через UPSERT/obsolete DELETE с KIZ validation и container projection pre/post guard.
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

## KIZ v1

- GET /api/kiz — список с product_id/location_code/lifecycle_status, limit/offset.
- POST /api/kiz/assign — назначить новый КИЗ единице доступного россыпного остатка.
- GET /api/kiz/stock-summary — exact product_id/location_code summary.
- GET /api/kiz/{kiz_code} — current state.
- GET /api/kiz/{kiz_code}/events — paginated immutable audit.
- POST /api/kiz/{kiz_code}/mark-error — active → error, author/reason.
- POST /api/kiz/{kiz_code}/deactivate — active → deactivated, author/reason.
- GET /api/system/kiz-integrity — read-only нарушения: КИЗ-идентифицированный остаток превышает физический.
- POST /api/kiz-operations/transfer — атомарное идемпотентное перемещение доступного россыпного остатка и выбранных active КИЗ.

[Полные контракты и JSON-примеры](../flows/kiz_v1.md). KIZ guard и concurrency
конфликты существующих write endpoints возвращают HTTP 409; FBS журнал сохраняется.


## Стартовая страница

`GET /` возвращает HTML со ссылками на Swagger, ReDoc и health для браузера
(`Accept: text/html`). Для API-клиентов без этого Accept сохранён прежний JSON.
`GET /health` не изменён. Миграция для стартовой страницы не требуется.


## Описания Swagger (2026-09-07)

В `/docs` добавлены русские заголовки всех операций, справка по форматам и ошибкам,
описания разделов и поиск по операциям. Для KIZ описаны все поля и параметры,
добавлены примеры назначения, закрытия, карточки, списка, аудита и сводки остатков.
Движения и пересчёт содержат примеры успешных ответов и конфликтов KIZ/concurrency.
Типы полей, ограничения валидации и бизнес-логика не изменены; новых маршрутов и
миграций нет. Примеры проверяются тестами `tests/test_swagger_documentation.py`.

Проверка: 190 тестов прошли, 63 PostgreSQL-теста пропущены без тестовой БД.
Прежний локальный контейнер wms-kiz-v1-test на момент проверки отсутствовал.
В рамках улучшения Swagger изменены app/main.py, app/api/v1/openapi_kiz.py,
app/api/v1/endpoints/{kiz,movements,system,fbs_shipments}.py,
app/core/schemas/{kiz,inventory,movement,system}.py, tests/test_swagger_documentation.py
и этот документ. Стандартные кнопки Swagger (Try it out, Execute, Schema) остаются
английскими; русифицированы описания API, заголовки операций и новые примеры.

## KIZ Stage 2A Phase 5

- `POST /api/kiz-operations/ship` — atomic idempotent physical shipment выбранных
  active KIZ и/или неидентифицированного по КИЗ остатка.
- Request повторяет transfer envelope, но item не содержит destination.
- `quantity` — строго положительное целое JSON-число; `external_line_id` стабилен
  для одной строки внешней команды.
- Existing KIZ reads показывают shipped с NULL location и shipped event/movement_ref.
- Старые movement, FBS и KIZ v1 contracts не изменены.

## KIZ Stage 3A history

`GET /api/kiz-history?kiz_code=<KIZ>` читает current projection из `wms.kiz`, lifecycle
из `wms.kiz_events`, physical transfer/ship только через
`kiz_movement_links → movement_registry → movements`. Совпавшие по `movement_ref`
ship movement и shipped event объединяются в одну запись `ship`. Assignment не получает
искусственного receive. `quantity` в timeline относится ко всему movement, не к одному
linked КИЗ. [Полный контракт](../flows/kiz_stage3a_history.md).

## Container Stage 3B B1 contract normalization

Новых container endpoints нет. Все существующие `/api/containers` URLs сохранены.
Request/response enum совпадает с DB: status `empty/open/sealed/blocked`, type
`pallet/box/cage/trolley`. После B4 `register` создаёт только empty container,
а QR create input требует непустую identity без краевых пробелов.

## Container Stage 3B B2.1

- `POST /api/container-operations/fill` — additive idempotent loose-to-container fill.
- `201` используется и для new, и для exact replay с сохранённым response.
- `404`: container/product отсутствует; `409`: state/stock/invariant/idempotency conflict;
  `422`: request schema, duplicate line, invalid decimal/Unicode.
- После B4 `POST /api/containers/register` принимает только empty contents; receipt-to-container
  выполняется пока как receipt в loose stock с последующим controlled fill.

## Container Stage 3B B2.2

- `POST /api/container-operations/extract` — additive idempotent partial/full
  container-to-loose extract для `open` flat container.
- Request использует stable `container_id`, exact product/batch и Decimal quantity;
  destination всегда равен direct `container.location_id`.
- `201` используется для new и exact replay; response содержит итоговый
  `container_status` и два `movement_ref` на item.
- `404`: container/product отсутствует; `409`: status/stock/invariant/idempotency
  conflict; `422`: malformed request, duplicate line/scope, invalid Decimal/Unicode.
- B4 удаляет legacy unpack route; partial/full extraction выполняют B2.2/B3 endpoints.

## Container Stage 3B B3

- `POST /api/container-operations/move` — idempotent same-warehouse move; response
  фиксирует source/destination/status и один movement ref на фактически перемещённый scope.
- `POST /api/container-operations/unpack-all` — idempotent full open-container unpack;
  items формируются сервером и содержат два movement refs на scope.
- `201`: new/exact replay; `404`: container/destination отсутствует; `409`: status,
  warehouse, projection, concurrency или idempotency conflict; `422`: malformed request.
- Legacy move/unpack URLs и их public contracts не изменены.

## Container Stage 3B B4 Final

- Generic `POST /api/movements` остаётся loose-only; non-NULL `container_code` возвращает
  `400 GENERIC_CONTAINER_MOVEMENT_NOT_ALLOWED`, весь batch откатывается.
- Legacy container move/unpack routes удалены из runtime/OpenAPI.
- Empty register сохранён; non-empty contents возвращает `400 CONTAINER_CONTENTS_NOT_ALLOWED`.
- Tasks читают exact `available` loose product/batch scope.
- Recalculate проверяет ledger/contents/inventory/location container projection до и после rewrite.
- `GET /api/system/audit-summary` включает container mismatch, state и provenance counters.

## Container Stage 3C C1

- `fill`/`extract`: item additive принимает `kiz_codes`, default `[]`; response сохраняет
  canonical identity list. KIZ set входит в idempotency intent, порядок кодов — нет.
- `move`/`unpack-all`: request не изменён; response item additive возвращает server-derived
  `kiz_codes`.
- `GET /api/kiz` поддерживает `container_id`/`container_qr_code`; `location_code` остаётся
  direct loose-holder filter.
- `GET /api/kiz/stock-summary` требует ровно один loose/container holder scope.
- `GET /api/kiz-history` группирует paired legs container operation в logical item.
- `GET /api/system/kiz-integrity` проверяет loose и contained holder/quantity scopes.
