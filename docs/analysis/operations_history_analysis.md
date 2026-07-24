# Фактическая карта операций для `operations-history`

Дата аудита: 2026-07-22. Этап: анализ, без реализации API и изменений БД.

## 1. Executive summary

Текущая модель неоднородна: `wms.movements` является общим журналом физических
изменений остатков, но только kit operations и re-sorting имеют полную структурную
связь «business header → items → movements». FBS имеет надёжный header/items, однако
item ссылается только на `movement_id` без FK и без `movement_created_at`. Tasks и
receipts имеют business rows, но связь с movements текстовая. Контейнерные операции
создаются функциями/триггерами и не имеют отдельного operation id.

Поэтому журнал только из movements не сможет корректно представить все бизнес-события.
Рекомендуется гибридный read model: `UNION ALL` адаптеров business tables для надёжных
источников плюс самостоятельные movements, не поглощённые структурно связанными
операциями. Receipt/task/container эвристики должны либо оставаться отдельными событиями,
либо явно получать `link_confidence=heuristic`; незаметно склеивать их нельзя.

Read-only аудит текущей БД подтвердил:

- parent и все 12 partitions имеют одинаковый `chk_movement_type` из 10 значений;
- `wms.movements` остаётся partitioned table без PK/unique на parent;
- фактически есть 3 399 movements: `receive` 1 412, `ship` 1 667, `transfer` 236,
  `adjust` 32, `kit_assembly` 30, `kit_disassembly` 18, `re_sorting` 4;
- `putaway`, `pick`, `unpack` разрешены constraint, но строк этих типов сейчас нет;
- metadata keys присутствуют только у kit/re-sorting movements;
- `source_type/source_id/source_item_id` заполнены только для kit/re-sorting;
- DB functions `move_container_inventory`, `sync_container_to_inventory` и
  `unpack_from_container` актуально существуют и содержат соответственно 1, 1 и 2
  `INSERT INTO wms.movements`; `register_container` создаёт contents и полагается на trigger.

## 2. Полный список movement types

### 2.1 Расхождения между слоями

Текущий DB constraint и последняя миграция
`scripts/migrations/20260715_add_re_sorting_operations.sql` совпадают:

```text
receive, putaway, transfer, pick, ship, unpack, adjust,
kit_assembly, kit_disassembly, re_sorting
```

`app/core/enums.py::MovementType` содержит:

```text
receive, ship, transfer, adjust, write_off, unpack,
kit_assembly, kit_disassembly, re_sorting
```

Следствия:

- `putaway` и `pick` разрешены БД, но общий `POST /api/movements` их не принимает;
- `write_off` принимается Pydantic enum, но запрещён DB constraint и не является
  актуальным допустимым movement type;
- отдельного write flow с literal `write_off` в WMS Service не найдено;
- FBS «списание» фактически создаёт `ship`.

### 2.2 Карта типов

| movement_type | Бизнес-название | Фактические создатели | Строк на операцию | Стороны |
|---|---|---|---:|---|
| `receive` | Поступление | общий MovementService; внешний receipt writer через `POST /api/movements`; trigger `sync_container_to_inventory` при регистрации контейнера | 1 на строку товара; batch receipt может содержать до 500; registration — 1 на inserted active content | `from=NULL`, `to=location` в подтверждённых flow |
| `putaway` | Размещение | текущий creator не найден; только DB constraint/comment | неизвестно | фактический контракт сторон кодом не установлен |
| `transfer` | Перемещение | общий MovementService/manual API; task completion; task discrepancy approval; trigger `move_container_inventory` | manual: 1 на request item; task: 1 на task item; container move: 1 на inventory row контейнера | обе стороны заполнены в найденных flow |
| `pick` | Отбор | текущий creator не найден; только DB constraint/comment | неизвестно | фактический контракт сторон кодом не установлен |
| `ship` | Отгрузка/списание | общий MovementService/manual API; FBS consumers и retry worker | manual: 1 на request item; FBS: 1 на `shipment_id + product_id` group | `from=location`, `to=NULL` |
| `unpack` | Распаковка контейнера | `wms.unpack_from_container()` | ровно 2 на вызов/product | container outgoing: `from=location,to=NULL,container_code=QR`; loose incoming: `from=NULL,to=location,container_code=NULL` |
| `adjust` | Корректировка | общий/manual API; receipt quantity correction; task discrepancy approval | 1 на корректируемый товар/delta | увеличение: только `to`; уменьшение: только `from` |
| `kit_assembly` | Комплектация комплекта | `KitOperationService` → kit repository/query | `N components + 1 kit` | component roles outgoing; kit result incoming |
| `kit_disassembly` | Разукомплектация комплекта | `KitOperationService` → kit repository/query | `1 kit + N components` | kit role outgoing; component results incoming |
| `re_sorting` | Пересортица | `ReSortingOperationService` → re-sorting repository/query | ровно 2 | source SKU outgoing; target SKU incoming в одной location |

Количество строк — фактическое поведение конкретного flow, а не общее правило типа.

## 3. Карта всех write flows

### 3.1 Прямые INSERT

В WMS Service найдены три SQL-точки прямого insert:

1. `app/infrastructure/database/queries/movements.py::CREATE_MOVEMENT` — общий insert,
   используемый `MovementService`.
2. `app/infrastructure/database/queries/kit_operations.py::CREATE_KIT_MOVEMENT` — kit
   movement с `source_type='kit_operation'`.
3. `app/infrastructure/database/queries/re_sorting_operations.py::CREATE_MOVEMENT` —
   re-sorting movement с `source_type='re_sorting_operation'`.

В актуальной БД дополнительно подтверждены insert внутри функций:

4. `wms.sync_container_to_inventory()` — receive при insert active content.
5. `wms.move_container_inventory()` — set-based transfer при смене location контейнера.
6. `wms.unpack_from_container()` — два unpack insert.

### 3.2 Flow matrix

| Flow / вход | Service / handler | Repository / SQL / DB function | Тип и число movements | Транзакционная граница | Business table |
|---|---|---|---|---|---|
| `POST /api/movements` | `MovementService.create_movement` | `MovementRepository.pool`; `queries.CREATE_MOVEMENT` | 1 на элемент batch, 1–500 | весь HTTP batch в одной transaction | нет |
| Receipt `POST /api/receipt_of_goods/update` в соседнем `1CRoutingAPI` | `WMSIntegrationService.process_receipts` | HTTP в WMS `POST /api/movements`, затем `WMSReceiptRepository` | new: 1 receive на product; correction: 0/1 adjust на product | WMS batch атомарен, но movement и receipt snapshot — разные commits | `wms.receipt_items`, header table в WMS нет |
| FBS standard/external RabbitMQ consumers | `consume_fbs_queue` → `handle_write_off_fbs` → `_process_shipment_group` | `FbsShipmentRepository`; общий `MovementService.create_movement_in_transaction` | 1 ship на product group | shipment/items создаются отдельно; каждая product group — своя transaction; shipment status отдельно | `fbs_shipments`, `fbs_shipment_items` |
| FBS retry worker | `process_pending_retries` | те же handler/repositories | 1 ship на успешно повторённую product group | одна transaction на product group | те же FBS tables |
| `POST /api/kit-operations` | `KitOperationService.create_operation` | `KitOperationRepository`; kit queries | assembly `N+1`; disassembly `1+N` | header, items, movements и completion в одной transaction | `kit_operations`, `kit_operation_items` |
| `POST /api/re-sorting-operations` | `ReSortingOperationService.create_operation` | `ReSortingOperationRepository`; re-sorting queries | ровно 2 | header, items, movements и completion в одной transaction | `re_sorting_operations`, `re_sorting_operation_items` |
| `POST /api/containers/register` | `ContainerService.register_container` | `register_container()` → insert contents → trigger `sync_container_to_inventory()` | 1 receive на active content | один function statement/DB transaction | `containers`, `container_contents`; operation table нет |
| `PUT /api/containers/{id}/location` | `ContainerService.update_container_location` | update container → trigger `move_container_inventory()` | 1 transfer на matching inventory row | один update statement/DB transaction; Python prechecks вне него | container row есть, move header нет |
| `POST /api/containers/{id}/unpack` | `ContainerService.unpack_container` | `unpack_from_container()` | ровно 2 | один function statement/DB transaction | container/content есть, unpack header нет |
| успешное completion task | `TaskService.complete_task` → `_create_movements_for_task` | общий `MovementService` | 1 transfer на task item | movement batch атомарен; task updates/completion не входят в ту же transaction | `tasks`, `task_items` |
| approve task discrepancy | `TaskService.approve_discrepancy` | общий `MovementService` | 1 transfer на item, иногда ещё 1 adjust | отдельная transaction на каждый вызов MovementService; status updates отдельно | parent/child tasks и task items |
| recount task | `TaskService.complete_recount` | task repository | movements не создаёт | отдельные repository calls | tasks/task items/metadata |

`putaway` и `pick` task types существуют, но task completion всё равно создаёт literal
`transfer`; movement types `putaway`/`pick` этот flow не использует.

## 4. Матрица способов группировки

| Сценарий | Ключ | Дополнительные связи | Оценка | Основание/ограничение |
|---|---|---|---|---|
| Kit | `(source_type='kit_operation', source_id)` | `source_item_id`; item хранит `(movement_id,movement_created_at)` | **Надёжная** | operation/item PK/FK; обе стороны связи записываются в одной transaction; source-поля без FK, но согласованы write flow |
| Re-sorting | `(source_type='re_sorting_operation', source_id)` | item role и `(movement_id,movement_created_at)` | **Надёжная** | ровно две unique roles; одна transaction |
| FBS shipment | `shipment_id` | items имеют FK к shipment; успешные items хранят `movement_id` | **Надёжная для header/items; возможная для movements** | нет FK к partitioned movements и нет `movement_created_at`; несколько items могут ссылаться на один movement |
| Receipt document | `receipt_items.guid` | `(guid,product_id)` unique; document number/reason | **Возможная для current items; ненадёжная для movements** | guid не записан в movement; reason — текст; revisions перезаписывают snapshot |
| Task | `task_id` | item FK; reason `Task #id`; `related_movement_id` | **Возможная** | header/items устойчивы, но source fields не ставятся; `related_movement_id` не заполняется (0 из 26 current tasks) и не покрывает набор |
| Container registration | `container_id`/`qr_code` | receive `container_code`, reason | **Возможная** | регистрация уникальна, но movement не хранит container_id/source id; QR может связывать строки |
| Container move | нет operation id | `container_code`, reason, from/to, created_at | **Ненадёжная** | один контейнер перемещается много раз; безопасно отделить конкретный set без времени невозможно |
| Container unpack | нет operation id | outgoing имеет QR, incoming QR только в reason | **Ненадёжная** | у второй строки `container_code=NULL`; объединение зависит от текста/времени/product/quantity |
| Manual HTTP batch | нет | общий transaction/близкий created_at | **Ненадёжная** | batch id не сохраняется; после commit строки неотличимы от отдельных calls |
| Standalone movement | `(movement_id,created_at)` | — | **Надёжная идентичность одной строки** | pair нужен из-за отсутствия global PK/unique на partitioned parent |

`metadata` нельзя использовать как универсальный ключ: в текущих данных keys есть только у
kit (`role`, `operation_type`, `kit_product_id`) и re-sorting (`role`,
`operation_code`, `from_product_id`, `to_product_id`, `location_code`). Receipt, FBS,
task, manual и container movements имеют пустую metadata.

## 5. Ключевые сценарии

### 5.1 Поступление

Фактический writer находится не в WMS Service, а в соседнем локальном проекте
`/home/skurbick/PROJECTS/1CRoutingAPI`:

- endpoint: `POST /api/receipt_of_goods/update`;
- service: `WMSIntegrationService.process_receipts`;
- новые строки отправляются batch HTTP-запросами в WMS `POST /api/movements`;
- затем `WMSReceiptRepository` отдельно inserts/updates `wms.receipt_items`.

Receive movement содержит product, quantity, receipt location, `user_name` автора и
reason `Поставка {document_number} от {supplier_name}`. Он **не содержит** guid,
receipt_item_id, `source_type`, `source_id`, `source_item_id` или metadata.

Текущая `receipt_items` — расширенный current snapshot: кроме guid/product/quantity и
document/supplier полей в actual DB есть document/supply/update timestamps,
`event_status`, `author_of_the_change`, organization, `order_guid`, currency. Unique
`(guid,product_id)` позволяет объединить current items документа, но не его исторические
применения.

Коррекции создают `adjust` с reason, содержащим document number и old/new quantities,
после чего snapshot обновляется. Нулевой diff movement не создаёт. Удалённая из новой
версии документа строка отдельным историческим событием в этом flow не представлена.

Итог:

- guid в movement отсутствует;
- все current товары документа объединяются по guid надёжно внутри receipt table;
- связь с receive/adjust movements только эвристическая по product, document number,
  reason и времени, причём document number не объявлен unique;
- 1 408 current receipt rows соответствуют 666 guid; 1 408 receive и 9 adjust имеют
  узнаваемый reason, но это не доказывает one-to-one link;
- `receipt:<guid>` может адресовать current snapshot документа, но не конкретную revision.

### 5.2 FBS-отгрузка

Один RabbitMQ message создаёт `fbs_shipments` header. Items группируются по product_id;
на каждую product group создаётся один `ship`. Все исходные items этой группы атомарно
получают один `movement_id` вместе с захватом `public.assembly_task.is_shipped`.

В actual data 2 069 items связаны с 1 667 distinct movements; 93 movement IDs
используются более чем одним item. Следовательно, event item ≠ movement; минимальная
единица stock effect — shipment product group, а пользовательская операция — весь shipment.

Доступны: shipment source (`standard`/`external_detected`), status, received/completed,
raw message, item statuses/errors/retries, product, quantity, author, supply_id, account,
assembly_tasks, warehouse/delivery fields и shipment date.

Связь items→shipment надёжна FK. Связь item→movement не имеет FK и хранит только bigint
movement_id. При отсутствии parent PK теоретически join может быть неоднозначен; detail
adapter обязан проверять число совпадений и не выбирать произвольную строку.

### 5.3 Комплектация и разукомплектация

ID всей операции — `kit_operations.operation_id`.

Assembly roles:

- по одному `component_consumption` на компонент, outgoing;
- один `kit_result`, incoming.

Disassembly roles:

- один `kit_consumption`, outgoing;
- по одному `component_result` на компонент, incoming.

Каждый item хранит product, quantity-per-kit, total quantity и пару
`movement_id + movement_created_at`. Movement хранит `source_type='kit_operation'`,
`source_id=operation_id`, `source_item_id=item_id` и metadata role/type/kit product.
Header, items, movements и completion создаются в одной transaction. Операцию можно
безопасно показать одной строкой и полно раскрыть в details.

### 5.4 Пересортица

ID всей операции — `re_sorting_operations.operation_id`. Header содержит исходный и
целевой SKU, одинаковое quantity, exact location snapshot, reason, author, status и
timestamps.

Items имеют ровно две роли, защищённые unique `(operation_id,role)`:

- `source_outgoing` → outgoing movement исходного SKU;
- `target_incoming` → incoming movement целевого SKU.

Movements содержат `source_type='re_sorting_operation'`, operation/item ids и metadata
обоих SKU, role и location code. Detail endpoint может получить полный header, оба items,
product names, location и обе movement rows. Группировка надёжная.

### 5.5 Контейнерные операции

Registration создаёт container header и по receive на каждую active content row через
trigger. `container_code=qr_code`, reason=`Container registered`. Это несколько rows без
operation id; уникальный container id/QR делает post-factum grouping возможным, но связь
не закреплена FK/source fields.

Move обновляет location container, trigger создаёт set-based transfer по каждой matching
inventory row с reason=`Container moved`. QR одинаков, но у контейнера может быть много
перемещений. Created_at/from/to помогают эвристике, но не являются operation identity.

Unpack создаёт ровно две строки. Только outgoing содержит `container_code=QR`; incoming
содержит QR в reason `Unpacked from {QR}`. Повторные unpack одного SKU/quantity могут
пересекаться, поэтому автоматическая склейка по времени/тексту небезопасна.

На момент аудита containers table пуста, поэтому data-level проверка групп невозможна.

### 5.6 Складские заявки

Нормальное completion создаёт один batch transfer movements по всем task items. Reason —
`Task #{task_id}`. Approve discrepancy создаёт transfer отдельно для каждого item и при
необходимости отдельный adjust; reason содержит task id. Эти calls и последующие status
updates не охвачены одной общей transaction.

`tasks.related_movement_id` существует, но код его не записывает; в actual DB поле пусто
у всех 26 tasks. `source_type/source_id/source_item_id` также не заполняются. Одна task
может создавать много movements. Header/items можно адресовать по task id, но effects
можно присоединить только regex по reason, что является возможной/эвристической, а не
гарантированной связью.

### 5.7 Ручные movements

`POST /api/movements` принимает 1–500 элементов и inserts их в одной transaction.
Никакой batch/request/event id не сохраняется, metadata/source fields общий query не
записывает. Transaction boundary после commit не восстанавливается. Поэтому каждая
строка должна быть самостоятельным `movement` event. Склеивание по author/reason/time
создаст ложные объединения.

## 6. Предлагаемая модель `event_id`

Формат: ASCII prefix, colon, безопасный canonical payload. Парсер использует allow-list
prefix и строгую валидацию payload; неизвестные prefix отклоняются.

| Источник | event_id | Комментарий |
|---|---|---|
| Kit | `kit_operation:<operation_id>` | bigint PK |
| Re-sorting | `re_sorting:<operation_id>` | bigint PK |
| FBS | `fbs_shipment:<shipment_id>` | bigint PK; весь shipment — одна операция |
| Task | `task:<task_id>` | bigint PK; movement links могут быть heuristic |
| Receipt current document | `receipt:<base64url-utf8-guid>` | base64url без padding исключает delimiter ambiguity; это current snapshot, не revision |
| Container registration | `container_registration:<container_id>` | допустимо для отдельной registration карточки |
| Standalone movement | `movement:<movement_id>:<created_at_epoch_us>` | составной identity; epoch microseconds вычисляется из UTC timestamptz |

Для container move/unpack отдельный composite operation ID из текущих данных предложить
нельзя: отсутствует устойчивый operation identifier. До изменения write model их rows
остаются `movement:*`.

`movement_id` без created_at использовать нельзя: partitioned parent не имеет global
PK/unique. ISO timestamp в path менее удобен из-за `:`/timezone escaping; UTC epoch
microseconds детерминирован и безопасно разбирается как integer. Detail query должен
фильтровать **оба** поля точным равенством.

## 7. Предлагаемый контракт списка

Это анализ доступности, не зафиксированный endpoint contract.

| Поле | Источник | Доступность/null | Характер |
|---|---|---|---|
| `event_id` | adapter-specific identity | всегда | вычисляемое из устойчивого ключа |
| `operation_type` | source adapter / movement_type | всегда | фактический discriminator, не UI label |
| `operation_name` | server mapping operation_type→локализованное имя | всегда | вычисляемое |
| `status` | kit/re-sort/FBS/task headers | null у movement/receipt/container registration, если нет доказанного source status | фактическое только при наличии source field |
| `created_at` | operation.created_at; FBS received_at; task/receipt/container timestamps; movement.created_at | всегда для включаемых sources | фактическое; receipt требует выбранной семантики document/created timestamp |
| `completed_at` | kit/re-sort/FBS/task | null у остальных и незавершённых | фактическое |
| `author` | operation.author; task users; receipt author; movement.user_name | nullable | фактическое, но тип identity различается |
| `warehouse/location` | operation location; task from/to; movement from/to; receipt fixed writer location | nullable | фактическое кроме receipt fixed config inference |
| `document_number` | receipt items | null у остальных | фактическое snapshot field |
| `external_reference` | receipt guid/order_guid; FBS supply/account/assembly tasks; source IDs | nullable, возможно несколько значений | фактическое/нормализованное представление требует решения |
| `product_count` | count distinct items/products or 1 movement product | nullable только для malformed/empty header | вычисляемое |
| `total_quantity` | source items/movements | nullable/неоднозначно для kit/re-sort/transfer | вычисляемое; нельзя складывать input+output как физический объём без правила |
| `source_type` | movement.source_type либо adapter literal | всегда рекомендуется | фактическое для movement, вычисляемое для business adapter |
| `link_confidence` | adapter rule | `structural`, `heuristic`, `none` | вычисляемое; рекомендуется для честного отражения legacy links |

Нельзя вводить synthetic общий status вроде `completed` для standalone movements,
receipts или container events: у этих источников такого поля нет.

`total_quantity` требует отдельного определения: для receive/ship/adjust это сумма effects;
для transfer сумма одной стороны; для kit/re-sort сумма всех строк удваивает/смешивает
разные SKU и не выражает одно бизнес-количество. Без решения лучше возвращать null либо
source header quantity только там, где оно существует.

## 8. Предлагаемый контракт details

Общая оболочка:

```json
{
  "event_id": "kit_operation:42",
  "operation_type": "kit_assembly",
  "header": {},
  "items": [],
  "movements": [],
  "metadata": {},
  "link_confidence": "structural"
}
```

`header` остаётся typed union по operation type, а не бесконтрольным общим JSON.
`metadata` возвращает сохранённую source metadata; нельзя смешивать её с raw FBS message.

| Источник | Откуда items | Доступные item fields |
|---|---|---|
| Kit | `kit_operation_items` + product/location + exact movement pair | role, product id/name, total quantity, location sides из movement, batch/container null, movement identity, created_at |
| Re-sorting | `re_sorting_operation_items` + exact movement pair | role, product id/name, quantity, sides, movement identity, created_at |
| FBS | `fbs_shipment_items`; movements через checked join | product, quantity, movement id; role можно вычислить `shipment_item`; location/batch/container из movement; FBS-specific fields в typed item extension |
| Task | `task_items` | product, planned/actual quantity, from location; target из task; movement identity только heuristic |
| Receipt | `receipt_items` current snapshot | product, name, current quantity; role `receipt_snapshot_item`; movement identity ненадёжен |
| Container registration | `container_contents` current rows и возможные receive rows | product, quantity, batch, container QR; current contents могут уже отличаться от registration snapshot |
| Standalone movement | сама movement row | role вычисляется из sides, product/name, quantity, from/to, batch, container, composite movement identity, created_at |

Для `movements[]` минимально нужны `movement_id`, `created_at`, type, product,
quantity, both locations, batch, container, author, reason, source triple и metadata.
Kit/re-sort detail должен связывать по `(movement_id,movement_created_at)`, а не только ID.

## 9. Фильтры и индексы

### 9.1 Применимость

| Фильтр | Реализация |
|---|---|
| `date_from/date_to` | применяется отдельно к timestamp каждого adapter до UNION; обязательный bounded range желателен |
| `operation_type` | pushdown в выбранные UNION branches; movement branch — movement_type/source classification |
| `product_id` | EXISTS по operation items/FBS/task/receipt; прямой predicate для movement; требует разных branches |
| `location_id` | operation/location columns, task from/to, movement both sides; receipt_items location не хранит |
| `author` | разные columns/types; exact normalized comparison возможен через branches, но общего индекса нет |
| `source_type` | branch discriminator; movement source_type; не одно SQL column во всех sources |
| `document_number/external_reference` | receipt column; FBS supply/account/assembly fields; часть references — arrays/JSON; требует source-specific predicates |
| `limit/offset` | применять только после UNION и global sort; offset на большой глубине дорогой |

Единый SQL возможен как `UNION ALL` нормализованных CTE/subqueries, но почти каждый filter
должен pushdown-иться в branches. Один scan movements не покрывает business headers,
failed FBS shipments и tasks без movements.

JSON search нужен для FBS `assembly_tasks`/raw message и произвольной metadata. Основные
kit/re-sort filters не должны читать JSON: для них есть relational columns/items.

### 9.2 Существующие полезные индексы

- movements: created_at, movement_type, product_id, `(product_id,created_at)`, обе
  locations, container_code, `(source_type,source_id,created_at)` и partial kit source;
- kit: created_at, type, product, status, location; items operation/product/movement;
- re-sort: created_at, `(status,created_at)`, from/to product, location; items
  operation/product/movement;
- FBS: shipment PK, `(source,received_at)`, `(source,status)`; items shipment/status/retry;
- receipt: guid, product, unique `(guid,product)`, partial supply/update datetime;
- tasks: PK, from/to, created_by, partial status/priority; task items task/product/location;
- containers: PK/QR/location/type/status.

### 9.3 Доказанные пробелы, но не предложения к немедленному DDL

- FBS list по времени без source не имеет standalone `received_at` index в actual audit;
- FBS product filter идёт через items, но index на `fbs_shipment_items.product_id` не найден;
- task global history sort по `created_at` не имеет общего created_at index;
- receipt document number/author filters не индексированы;
- author/user_name filters во всех movement partitions не индексированы;
- JSON external-reference filters не имеют GIN.

Это только кандидаты для `EXPLAIN (ANALYZE, BUFFERS)` на согласованных запросах и
реальных селективностях. Без будущего SQL и измерений создавать индексы нельзя.

## 10. Рекомендуемая архитектура

### Вариант A: журнал только из movements

Плюсы:

- единый ledger source и общий набор location/product/quantity полей;
- существующие date/product/location indexes;
- простая стабильная identity `(movement_id,created_at)`;
- покрывает manual и container effects.

Минусы:

- kit/re-sort превращаются во много строк вместо одной операции;
- FBS shipment status, failures и items теряются;
- failed business operations без movement не видны;
- receipt guid/document snapshot и task lifecycle отсутствуют;
- grouping legacy rows по reason/time небезопасен.

### Вариант B: UNION business tables + standalone movements

Branches списка:

1. `kit_operations`;
2. `re_sorting_operations`;
3. `fbs_shipments`;
4. после бизнес-решения — receipt current documents и tasks;
5. container registration как отдельный ограниченный branch, если нужна именно она;
6. standalone movements.

Для предотвращения дублей standalone branch исключает:

- movements с structural source `kit_operation`/`re_sorting_operation`;
- FBS movements, найденные через успешные shipment items, только если join дал ровно одну
  movement row; ambiguity должна логироваться/показываться отдельно;
- receipt/task/container rows **не исключаются по эвристике молча**.

Источник списка — нормализованный UNION headers/events. Источник details — профильный
adapter: business header/items плюс связанные movements; для standalone — movement.

Рекомендация: Вариант B. Он единственный показывает одну строку на реальную kit,
re-sorting или FBS business operation и одновременно не теряет manual/container ledger
effects. Реализацию лучше разделить на source adapters/repositories и orchestration
service; SQL не помещать в endpoint/service.

## 11. Неразрешимые или ненадёжные места

1. Receipt movements не содержат guid/receipt item id/source triple; revision history нет.
2. Task movements не содержат task/item source triple; `related_movement_id` не используется.
3. Manual batch transaction не имеет сохранённого batch id.
4. Container move/unpack не имеют operation id; unpack incoming не хранит container_code.
5. FBS item хранит movement_id без created_at/FK; join теоретически неоднозначен.
6. Parent movements не имеет global PK/unique; `movement:<id>` недостаточно.
7. `putaway`/`pick` разрешены DB, но недоступны через MovementType и creator не найден.
8. `write_off` есть в Python enum, но запрещён DB и не должен попадать в актуальный список.
9. Receipt table — current snapshot: `receipt:<guid>` не означает одну неизменяемую
   историческую операцию коррекции.
10. Общий смысл `total_quantity` для multi-effect kit/re-sort/transfer не определён.
11. Не определено, должен ли task lifecycle быть отдельной операцией рядом со stock effect
    или только оболочкой над ним.
12. В actual data container operations отсутствуют, поэтому исторические edge cases
    нельзя подтвердить выборкой.

## 12. Пошаговый план реализации

1. Зафиксировать бизнес-решения из раздела 11: scope tasks/receipts, semantics quantity,
   отображение heuristic links и failed/no-movement operations.
2. Утвердить strict event-id codec и typed operation/source enums.
3. Описать response schemas как common envelope + discriminated typed headers/items.
4. Реализовать read-only source adapters для kit и re-sort с composite movement lookup.
5. Реализовать FBS adapter с aggregation product groups и ambiguity check movement join.
6. Реализовать standalone movement adapter с composite identity.
7. Собрать global list через filter pushdown + `UNION ALL`; сначала ограничить диапазон дат
   и offset, затем измерить actual plan.
8. Добавить receipt/task adapters только в согласованном режиме: header-only или
   `link_confidence=heuristic`; не скрывать standalone movements по недоказанной связи.
9. Решить, включать ли container registration header; move/unpack оставить standalone до
   появления operation identity.
10. Реализовать detail dispatcher по allow-listed prefix без dynamic SQL identifiers.
11. Добавить tests на отсутствие дублей, composite movement identity, FBS many-items→one
    movement, incomplete/failed sources, ambiguous legacy links и pagination stability.
12. Выполнить `EXPLAIN (ANALYZE, BUFFERS)` по каждому branch и итоговому UNION на
    production-like объёме; только после этого предлагать конкретные индексы.

## 13. Вопросы, требующие бизнес-решения

1. Показывать task как самостоятельную lifecycle-операцию, как оболочку stock movements
   или показывать оба события?
2. Показывать receipt `guid` как одну current-документ операцию, если последующие
   корректировки не имеют immutable revision, либо до новой модели показывать только
   receive/adjust movements?
3. Допустима ли в публичном API эвристическая связь с явным `link_confidence`, или нужны
   только структурно доказанные связи?
4. Что означает `total_quantity` для transfer, kit assembly/disassembly и re-sorting:
   business header quantity, outgoing total, incoming total или null?
5. Должны ли failed/validation_failed FBS и processing operation headers без movements
   попадать в общий журнал?
6. Считать ли container registration одной бизнес-операцией; нужны ли move/unpack одной
   строкой до появления operation id?
7. Нужны ли `putaway` и `pick` как реальные movement types/API operations, и что делать с
   ошибочным Python enum `write_off`? Это отдельная write-model задача, не часть history API.
8. Какой внешний reference приоритетен для FBS (`shipment_id`, `supply_id`, account,
   assembly tasks) и receipt (`guid`, `order_guid`, document number)?
