# Decisions

Статус: `CURRENT`.

## Формат записей

Каждая запись содержит дату и контекст/решение/последствия в основном тексте, а также
единый metadata-блок: статус решения, связанные endpoints, миграции и superseded status.
`active` означает действующее решение; `partially-superseded` — часть исходного контекста
устарела, но решение сохраняет историческую ценность.

## 2026-04-30 - Первичная документация текущего состояния

- Статус решения: `partially-superseded`
- Связанные endpoints: —
- Связанные миграции: —
- Superseded: Утверждение об отсутствии миграций устарело; принципы документирования остаются active.

Создана папка `docs/` и первичная карта проекта без изменения рабочего кода.

Основание:

- в репозитории отсутствовала контекстная документация;
- перед будущими изменениями AGENTS.md требует изучать и поддерживать `docs/`;
- проект содержит критичные операции с остатками, движениями и адресным хранением, где важно фиксировать текущие правила и неизвестные места.

Решения:

- Документировать только факты, найденные в коде, SQL-запросах, схемах и комментариях.
- Не выдумывать DDL, constraints, таблицы и endpoint'ы.
- Все неподтвержденные детали вынести в `open_questions.md`.
- Так как ORM-моделей и миграций нет, [`database/map.md`](../database/map.md) составлен по `queries`, `repositories` и Pydantic-схемам.

Код приложения не изменялся.


## 2026-05-21 - Recursive inventory summary по subtree локации

- Статус решения: `active`
- Связанные endpoints: `GET /api/inventory/location/{location_id}/recursive-summary`
- Связанные миграции: —
- Superseded: нет

Добавлен read-only endpoint `GET /api/inventory/location/{location_id}/recursive-summary`.

Решения:

- Возвращать список агрегированных остатков без метаданных исходной локации.
- Группировать только по `product_id`; `status`, `batch_number` и `container_code` не выделять отдельными строками.
- Включать остатки во всех статусах и в неактивных дочерних локациях.
- Не добавлять endpoint по `location_code`.
- Использовать `wms.locations.path <@ parent.path`, включая саму исходную локацию.
- Не добавлять миграцию: индекс `idx_locations_path USING gist(path)` уже описан в DDL-контексте.

## 2026-05-28 - Мягкие резервы товара по RabbitMQ

- Статус решения: `partially-superseded`
- Связанные endpoints: `GET /api/inventory/availability*`, reservations read API
- Связанные миграции: `scripts/migrations.sql`
- Superseded: Утверждение об отсутствии migration artifact устарело; доменная модель резервов остаётся active.

Добавлен MVP мягких резервов по `product_id + external_order_id`.

Решения:

- Не использовать `wms.inventory` и `wms.movements` для резервов, чтобы не смешивать обещанный внешний спрос с физическим складским остатком.
- Хранить текущее состояние в `wms.stock_reservation_orders`, а все входящие события и бизнес-ошибки - в `wms.stock_reservation_events`.
- Использовать UPSERT по `(source_type, product_id, external_order_id)` для идемпотентности повторных сообщений.
- Первичная реализация была разделена позднее: stock reservations обрабатываются отдельным consumer и отдельной очередью, чтобы не смешивать их с FBS write-off flow.
- Таблицы и view считаются внешне созданными объектами БД согласно ТЗ; миграция в проект не добавлялась, так как существующего механизма миграций в репозитории нет.

## 2026-05-28 - Отдельная RabbitMQ очередь для мягких резервов

- Статус решения: `active`
- Связанные endpoints: —
- Связанные миграции: `scripts/migrations.sql`
- Superseded: нет

Решено разделить RabbitMQ consumers для FBS списаний и мягких резервов.

Решения:

- FBS write-off consumer продолжает слушать `settings.RABBITMQ_QUEUE`.
- Stock reservation consumer слушает отдельную `settings.STOCK_RESERVATION_QUEUE`.
- Запуск reservation consumer управляется отдельным флагом `settings.RESERVATION_CONSUMER_ENABLED`.
- Ошибки БД при обработке резервов логируются и приводят к NACK/requeue, чтобы отсутствие таблиц или view резервов было видно в логах и не ACK-алось как успешная бизнес-ошибка.

## 2026-06-14 - Внешне обнаруженные FBS-отгрузки используют общий pipeline

- Статус решения: `active`
- Связанные endpoints: FBS shipment/retry API
- Связанные миграции: `20260614_add_fbs_shipment_source.sql`
- Superseded: нет

Решено принимать внешне обнаруженные отгрузки из отдельной RabbitMQ queue, но обрабатывать их существующим FBS pipeline. Источники разделяются через `wms.fbs_shipments.source`; отдельные shipment/item таблицы не создаются, чтобы сохранить общие retry, историю и movement flow.

В рамках решения атомарная граница product group расширена до `is_shipped + movement + inventory trigger + item success/movement_id`. Shipment status остается отдельным post-group пересчетом.

## 2026-06-16 - Тестовое отключение проверки assembly_task для FBS

- Статус решения: `active`
- Связанные endpoints: FBS shipment/retry API
- Связанные миграции: —
- Superseded: нет

Добавлен флаг `settings.FBS_VALIDATE_ASSEMBLY_TASKS` с дефолтом `True`.

Решения:

- По умолчанию сохранить прежнюю защиту: проверка существования assembly tasks, запрет уже отгруженных СЗ и атомарная отметка `public.assembly_task.is_shipped`.
- При `FBS_VALIDATE_ASSEMBLY_TASKS=False` не обращаться к `public.assembly_task` в общем FBS pipeline, включая standard, external-detected и retry/manual reprocessing flows.
- Не ослаблять Pydantic-контракт Rabbit payload: `assembly_tasks` остаются обязательными, `quantity` должен равняться их количеству.
- Миграция не нужна, потому что меняется только runtime-настройка Python-кода.

## 2026-07-01 - validate-integrity считает нетто-остаток по movements

- Статус решения: `active`
- Связанные endpoints: `POST /api/system/validate-integrity`
- Связанные миграции: —
- Superseded: нет

Исправлена логика сверки `POST /api/system/validate-integrity`: расчетная сторона должна строиться как ledger из `wms.movements`, где `to_location_id` дает `+quantity`, а `from_location_id` дает `-quantity`.

Решения:

- Сверять только `wms.inventory.status = 'available'` с нетто-остатком по ключу `(product_id, location_id, status, batch_number, container_code)`; `damaged` и `quarantine` не входят в movement-ledger сверку.
- Использовать `FULL OUTER JOIN`, чтобы видеть строки только в movements и строки только в inventory.
- Сравнивать nullable `batch_number` и `container_code` через `IS NOT DISTINCT FROM`.
- Не менять `POST /api/system/recalculate-inventory` в рамках этого исправления: текущий SQL пересчета использует старый `COALESCE(to_location_id, from_location_id)` подход и требует отдельной безопасной правки перед использованием как maintenance operation.

## 2026-07-01 - recalculate-inventory пересобирает только available по net ledger

- Статус решения: `active`
- Связанные endpoints: `POST /api/system/recalculate-inventory`
- Связанные миграции: —
- Superseded: нет

Исправлена логика `POST /api/system/recalculate-inventory`: maintenance-пересчет пересобирает только `status='available'` из `wms.movements` по той же ledger-модели, что и live trigger `wms.update_inventory_from_movement()`.

Решения:

- `from_date` временно запрещен, потому что частичный пересчет может удалить inventory шире, чем восстановить movements после даты.
- Перед удалением available inventory выполняется диагностика calculated net остатков; отрицательные значения останавливают транзакцию с диагностикой ключа остатка.
- Удаляются только строки `wms.inventory.status = 'available'`; `damaged` и `quarantine` не трогаются.
- Вставляются только calculated available rows с `quantity > 0.0001`; нулевые остатки не материализуются.
- Ключ пересчета: `(product_id, location_id, status, batch_number, container_code)` без нормализации nullable полей в пустую строку.

## 2026-07-07 - MVP операций комплектации и разукомплектации комплектов

- Статус решения: `active`
- Связанные endpoints: `/api/kit-operations*`
- Связанные миграции: `20260707_add_kit_operations.sql`
- Superseded: нет

Добавлен отдельный HTTP flow `POST /api/kit-operations` и read endpoints для журнала операций комплектов.

Решения:

- Не вызывать 1С и не использовать RabbitMQ: операция выполняется полностью внутри WMS service.
- Состав комплекта читать из `public.products.kit_components`; отдельную таблицу состава не вводить.
- Остатки менять только через `INSERT INTO wms.movements`, без прямых изменений `wms.inventory`.
- Добавить `wms.kit_operations` и `wms.kit_operation_items` для audit операции и связи строк с movements.
- Расширить `wms.movements` полями `source_type/source_id/source_item_id` и разрешить `kit_assembly/kit_disassembly` в check constraint.
- Для конкурентности использовать одну DB transaction, advisory lock по `kit_product_id + location_id`, и `SELECT ... FOR UPDATE` для расходной россыпи.
- FK от `kit_operation_items.movement_id` к `wms.movements` не добавлять, пока у parent `wms.movements` нет PK/unique constraint.

## 2026-07-07 - Разрешённые локации kit operations

- Статус решения: `active`
- Связанные endpoints: `/api/kit-operations/locations*`
- Связанные миграции: `20260707_add_kit_operations.sql`
- Superseded: нет

Рефакторинг kit operations перевел выбор места выполнения с произвольного `location_code` на явный allow-list `wms.operation_locations`.

Решения:

- `location_code` в `POST /api/kit-operations` сохранить, но требовать активную строку `wms.operation_locations` с `operation_code='kit_operations'`, `scope='direct'`, `is_active=true`.
- Убрать требование `locations.level=5`: разрешённой локацией может быть зона или адрес другого уровня.
- Для текущего MVP реализовать только `scope='direct'`: использовать `inventory.location_id = operation_locations.location_id`, без subtree дочерних адресов.
- В `wms.kit_operations` сохранять `operation_location_id`, `location_id`, `location_code` как audit-связь с разрешённой локацией.
- Добавить endpoints управления разрешёнными локациями под `/api/kit-operations/locations`.
- Зафиксировать unique index `uq_operation_locations_operation_location_scope` на `(operation_code, location_id, scope)`, чтобы одна и та же локация не дублировалась в рамках операции и scope.
- Не реализовывать subtree mode в MVP: direct scope читает только остатки выбранной `location_id`.

## 2026-07-15 - MVP пересортицы товара

- Статус решения: `active`
- Связанные endpoints: `/api/re-sorting-operations*`
- Связанные миграции: `20260715_add_re_sorting_operations.sql`
- Superseded: нет

Решено переидентифицировать одинаковое целое количество физического россыпного остатка
двумя movements типа `re_sorting` в одной direct-локации. Комплекты считаются обычными
SKU, состав не читается. Мягкие резервы, 1С и RabbitMQ не участвуют. Встречные A→B/B→A
сериализуются lock key по location и отсортированной паре SKU. Idempotency не вводится.
## 2026-07-22 — MVP дневной истории остатков

- Статус решения: `active`
- Связанные endpoints: `GET /api/inventory-history/daily-balances`
- Связанные миграции: —
- Superseded: нет

Дневная история физического available-остатка восстанавливается исключительно из
`wms.movements` по универсальной ledger-семантике сторон. Решение намеренно не ветвится
по `movement_type`, поэтому новые типы автоматически учитываются при корректно заданных
`from_location_id`/`to_location_id`.

Зафиксированы: timezone `Europe/Moscow`; агрегация партий, контейнеров и россыпного остатка до
товара; отсутствие status-разреза damaged/quarantine; opening из всей ledger-истории до
начала периода; заполнение пустых дней календарём; пагинация по товарам. Snapshots и
текущая проекция `wms.inventory` не используются. Согласованность count и страницы при
конкурентных inserts обеспечивается read-only `REPEATABLE READ` транзакцией.

Новые DB-объекты и индексы не вводятся до фактического EXPLAIN на рабочем объёме.
## 2026-07-22 — MVP единого списка бизнес-операций

- Статус решения: `active`
- Связанные endpoints: `GET /api/operations-history`
- Связанные миграции: —
- Superseded: нет

Для `GET /api/operations-history` выбран гибридный read model из четырёх adapters:
kit operations, re-sorting operations, FBS shipments и standalone movements. Business
headers receipt/tasks/container operations намеренно не включены из-за отсутствия
структурной связи с movements; их эффекты не скрываются по эвристикам.

Идентичность standalone movement задаётся парой movement id и UTC epoch microseconds
created_at, поскольку partitioned parent не имеет global PK/unique по movement_id.
FBS header является одной list operation независимо от числа items/product groups и
присутствует также при failed/validation_failed/no-items. Связанный FBS movement
поглощается только если один movement_id разрешается ровно в одну строку всей таблицы.

Общий synthetic status для movements не вводится. FBS location остаётся null и FBS branch
не участвует в location filter, поскольку relational location в FBS tables отсутствует.
Transfer movement с двумя сторонами возвращает null primary location, но location filter
проверяет обе стороны. Неизвестный строковый movement type не ломает read response.

## 2026-07-22 — Детальная карточка операции

- Статус решения: `active`
- Связанные endpoints: `GET /api/operations-history/{event_id}`
- Связанные миграции: —
- Superseded: нет

List и detail используют общий read-only codec event ID и mapping названий. Movement
identity включает `movement_id` и точные UTC epoch microseconds без binary float.
Prefix выбирает только статический adapter и никогда не становится SQL identifier.
Kit/re-sorting links проверяются по ID и timestamp, FBS links получают статус
`not_linked/resolved/missing/ambiguous`. Повреждённая ссылка не скрывает business header:
ответ остаётся 200 с warnings. Receipt detail, новые FK/индексы и write-изменения отложены.

## 2026-07-23 — Read-only история документа поступления

- Статус решения: `active`
- Связанные endpoints: `GET /api/receipts/{guid}/history`
- Связанные миграции: —
- Superseded: нет

Legacy `supply_to_sellers_warehouse` является источником immutable source rows истории,
а `wms.receipt_items` — только current WMS snapshot. Ревизии группируются исключительно
по точному source timestamp; округление и temporal proximity запрещены. Naive legacy
timestamps интерпретируются в `Europe/Moscow`. Current revision определяется legacy
`is_valid`, fallback rows без дат не склеиваются. Receipt-to-movement linking отсутствует.
Пагинация выполняется по revision headers, items страницы загружаются set-based.

## 2026-07-27 — List read model поступлений

- Статус решения: `active`
- Связанные endpoints: `GET /api/receipts/history`
- Связанные миграции: —
- Superseded: нет

Список строится UNION ALL двух нормализованных веток: одна строка на точную legacy
revision и одна строка на WMS-only GUID. WMS snapshot не дублирует legacy document.
Product filter определяет включение целой revision, а totals всегда считаются по всем
source rows. Глобальный row_id содержит unpadded base64url GUID; открывать detail по нему
нельзя — frontend использует исходный guid. Сортировка хронологическая, undated в конце.
Эвристическое связывание с movements и поля physical effect не вводятся.

## 2026-07-31 - HTTP FBS использует общий pipeline

- Статус решения: `active`
- Связанные endpoints: `POST /api/fbs-shipments`
- Связанные миграции: `20260614_add_fbs_shipment_source.sql`; ручное расширение constraint для `http_api`
- Superseded: нет

Решено принимать FBS-отгрузки через `POST /api/fbs-shipments` в том же контракте
`WriteOffAccordingToFBS`, что RabbitMQ consumers. HTTP является тонким адаптером:
shipment сохраняется до доменной валидации, получает отдельный `source='http_api'`, а
валидный payload передаётся в существующий `handle_write_off_fbs`.

Транзакционная граница product group, movement/inventory flow, статусы и retry не
изменяются. Отдельный idempotency key не добавляется; защита повторного физического
списания остаётся основанной на атомарном захвате assembly tasks. Ограничение
`chk_fbs_shipments_source` владелец БД изменяет вручную до выкладки приложения.

## 2026-08-07 - Current State остаётся снимком текущих возможностей

- Статус решения: `active`
- Связанные endpoints: —
- Связанные миграции: —
- Superseded: нет

Контекст: `current_state.md` одновременно содержал описание текущих возможностей и
датированные заголовки этапов внедрения, из-за чего документ мог восприниматься как
changelog.

Решение:

- использовать `current_state.md` только как сопровождаемый `CURRENT` snapshot;
- убрать из заголовков даты и временную маркировку MVP, сохранив подтверждённые текущие
  ограничения и возможности;
- не создавать отдельный changelog: датированная история важных изменений уже ведётся
  в `decisions.md`;
- при утрате актуальности обновлять или удалять описание возможности в
  `current_state.md`, а историческое решение сохранять в `decisions.md`.

Последствия: новый разработчик читает `current_state.md` как текущее состояние, а
`decisions.md` — как хронологию причин и изменений. Endpoint'ы, бизнес-логика и БД не
изменялись.

## 2026-08-07 - FBS physical write-off и journal link имеют одну транзакцию

Контекст: production orphan movement показал исторически возможный commit movement и
`assembly_task.is_shipped` до отдельного обновления `fbs_shipment_items`.

Решение: standard consumer, external consumer, HTTP ingestion, retry worker и manual
item retry используют `_process_shipment_group`. Внутри переданного `conn` блокируются
items и assembly tasks, создаётся movement, обновляются item links и parent shipment.
Location validation также использует этот `conn`. Ошибка на любом шаге откатывает всю
product group. Existing orphan movements автоматически не восстанавливаются.

## 2026-09-06 — KIZ v1: остановка реализации на подтверждённом MVCC-конфликте

- Статус решения: `partially-superseded` (остановка снята согласованием поправки ниже).
- Связанные endpoints: новые KIZ endpoints ещё не реализованы.
- Связанные миграции: отсутствуют.
- Superseded: нет.

В реальной PostgreSQL 16 подтверждён обход предлагаемого inventory guard обычной
REPEATABLE READ транзакцией со старым snapshot. Сохранены исполняемый тест и
[предложение минимальной поправки](../proposals/kiz_v1_lock_protocol_review.md).
До уточнения протокола application code и DDL не меняются. SQL для рабочей БД
передаётся пользователю на проверку и применяется им вручную.

## 2026-09-06 — KIZ v1 и согласованный MVCC touch

- Статус решения: `active`.
- Связанные endpoints: KIZ API и отдельный system/kiz-integrity; existing write conflict mapping.
- Связанные миграции: 20260906_add_kiz_v1.sql.
- Superseded: первоначальная остановка реализации на MVCC-конфликте снята.

Пользователь явно разрешил служебный UPDATE уже заблокированной inventory row без
изменения physical quantity/key и без movement. KizService делает lock → touch → count;
вся операция, включая assigned event, атомарна. Реальные RR расход/assignment со старым
snapshot получают 40001. Event при assignment один, registered не вводится.

Inventory guard P7501 защищает physical projection всех writers. 40001/40P01 возвращают
409 и повторяются целиком; FBS использует bounded retry, KIZ shortage — failed без retry.
Recalculate сохраняет ledger semantics, но работает через locked validation/UPSERT/
obsolete DELETE/final integrity. Для стабильного ledger и concurrent assignment нужны
table locks; их maintenance-стоимость и потенциальные deadlocks задокументированы.

[Действующий контракт и ограничения](../flows/kiz_v1.md).
Текущая DB role superuser: [read-only проверка](../database/kiz_v1_runtime_check.md).
SQL только подготовлен на проверку; в рабочей БД пользователь применяет его вручную.


## 2026-09-08 — точечное устранение KIZ D1/D2

Для D1 выбран ранний запрет конфликтующих кодов при assignment (422), без изменения
статических маршрутов или добавления endpoint. Для D2 входная metadata проверяется
до транзакции; KIZ validation response экранирует Unicode, чтобы неподдерживаемый
символ в возвращаемом input ошибки тоже не приводил к 500. Lock/touch протокол,
SQL и миграция не меняются.

## 2026-09-10 — KIZ Stage 2A Phase 1: stable movement identity

- Статус решения: `active` после ручного применения migration владельцем БД.
- Связанные endpoints: публичные endpoints не изменены.
- Связанные migrations: `20260910_add_movement_registry.sql`.

Для partitioned ledger выбрана непартиционированная registry с identity bigint PK и
composite coordinate `(movement_id, movement_created_at)`. Parent movements получает
`UNIQUE (movement_id, created_at)`, registry — настоящий composite FK. Общая DB boundary
реализована parent `AFTER INSERT` trigger, поэтому все существующие writers получают
mapping атомарно без изменений их контрактов.

Историческая coverage заполняется отдельными resumable batches с SKIP LOCKED; migration
не держит одну транзакцию на весь backfill. Read-only integrity function проверяет 1:1.
Phase 2, KIZ links, новые lifecycle и endpoints в решение не входят.

## 2026-09-10 — KIZ Stage 2A Phase 2: immutable association

- Статус решения: `active`; migration подготовлена для ручного применения.
- Связанные endpoints: существующие KIZ read contracts расширены shipped/null location;
  write endpoints не добавлены.
- Связанные migrations: `20260910_add_kiz_movement_links.sql`.

Association хранит только `kiz_id + movement_ref`, защищена PK/FK и immutable trigger.
В event выбран composite FK на association: shipped event не может сослаться на чужой
или непривязанный movement. Legacy events оставляют movement_ref NULL.

Current state shipped имеет location NULL; event location остаётся source snapshot.
Identity guard намеренно не получает общий bypass. Будущий controlled transfer/ship
должен в отдельной фазе определить атомарную write operation и её узкую авторизацию.

## 2026-09-10 — KIZ Stage 2A Phase 3: transaction-owned idempotency

- Статус решения: `active`; migration подготовлена для ручного применения.
- Связанные endpoints: отсутствуют.
- Связанные migrations: `20260910_add_kiz_operations.sql`.

Выбран специализированный business key
`(source_system,operation_type,external_operation_id)`. INSERT ON CONFLICT и последующий
row lock классифицируют new/replay/conflict и сериализуют concurrent replay до любых
будущих stock/KIZ locks. Advisory locks и интеграция legacy flows не добавлены.

Status не хранится. NULL result существует только внутри owner transaction; deferred
constraint запрещает commit без result. Поэтому rollback physical flow удалит intent,
items и result, а повтор останется доступен. Result и item movement_ref заполняются
однократно. Один movement_ref принадлежит максимум одной operation item.

Fingerprint строится SHA-256 от UTF-8 canonical JSON всех полей normalized physical
intent. Object keys сортируются; Decimal записывается без лишних нулей; KIZ и items
сортируются; duplicates и float запрещены. Unicode identifiers сохраняются точно.

## 2026-09-10 — Controlled KIZ location update через transaction authorization

Для Phase 4 выбран SECURITY DEFINER protocol с узкой authorization row и deferred
проверкой полного operation→movement→link graph. Глобальный bypass не используется:
обычный UPDATE location остаётся запрещён. Authorization удаляется до commit и не
дублирует physical history. Provenance использует `source_type/source_id/source_item_id`.

## 2026-09-11 — Controlled explicit KIZ shipment

Phase 5 использует отдельную SECURITY DEFINER function и transaction-local authorization,
сохраняя Phase 4 guard без общего bypass. KIZ сначала перестаёт быть active в source
внутри transaction, затем outgoing movement уменьшает physical stock. Deferred trigger
требует до commit полный operation/result → item → movement/registry → link → shipped
state/event graph.

Один item создаёт один movement полного Q. Пустой список KIZ расходует только
неидентифицированный по КИЗ остаток. FBS и receipt не подключаются к этому endpoint.

## 2026-09-15 — KIZ Stage 3A: unified history без нового ledger

- Статус решения: `active`.
- Связанный endpoint: `GET /api/kiz-history?kiz_code=<KIZ>`.
- Связанные migrations: отсутствуют.

Выбран отдельный query namespace, чтобы не пересекаться с catch-all KIZ code route.
Read model собирается в одной read-only repeatable-read transaction: current KIZ row и
единая timeline из lifecycle events и immutable movement graph. Поиск physical history
по reason/metadata запрещён.

Ship movement и shipped event объединяются только по точному `movement_ref`; stable IDs
обоих источников сохраняются. Deterministic order использует timestamp, source rank и
`kiz_event_id`/`movement_ref`; materialization и schema changes не добавляются.

## 2026-09-15 — Container Stage 3B Phase B1 identity contract

- Статус: `active`; по входным данным B2.1 migration применена владельцем БД.
- Stable internal identity — `container_id`; external `qr_code` immutable и не reuse.
- Flat model и required direct location выбраны до отдельной nesting phase.
- Канонические statuses: `empty/open/sealed/blocked`; legacy `opened -> open`,
  `in_transit` исключён как неиспользуемый. Types: `pallet/box/cage/trolley`; legacy
  DB-only `unit` исключён после empty-production preflight.
- Warehouse не дублируется в container: определяется root ancestor location; future move
  обязан сравнивать source/destination roots.
- Dual `container_contents + inventory.container_code` сохранён до controlled protocols.
  Register остаётся receipt-like; B1 не меняет quantity и не создаёт movements.

## 2026-09-15 — Container Stage 3B Phase B2.1 fill ledger

- Статус: `active`; implementation/migration готовы, production apply не выполнялся.
- Выбрана пара transfer movements на line, потому что одна legacy movement имеет один
  `container_code` и не может одновременно выразить loose source и contained destination.
- Existing empty register переиспользуется; новый create-empty endpoint не добавлен.
- Container-specific idempotency отделена от `kiz_operations`; fingerprint включает
  container, stable lines, product, exact batch и Decimal quantity.
- Container lock и canonical inventory locks задают общий порядок с будущими B2.2/B3.
  Legacy unpack изменён только добавлением lock; quantitative extract не переработан.

## 2026-09-16 — Container Stage 3B Phase B2.2 controlled extract

- Статус: `active`; implementation, read-only preflight и migration готовы. Production
  apply и business extract агентом не выполнялись.
- Endpoint additive: `POST /api/container-operations/extract`; legacy unpack не меняется.
- Ledger зеркален fill: contained outgoing и loose incoming `transfer` movement на item.
- Full extract удаляет active current content row; отдельный contents history ledger не
  создаётся, история сохраняется operation graph и movements.
- Duplicate exact product/batch scopes в request запрещены. NULL batch сравнивается через
  `IS NOT DISTINCT FROM`; Decimal/float policy совпадает с fill.
- Existing container-operation idempotency обобщена по `operation_type`; extract result
  включает финальный `container_status`.
- Controlled DB function применяет contents mutation только после пары movement refs и
  проверяет one-shot delta. Container/inventory/content locks совместимы с fill order.
- B3/B4/B5 и Stage 3C остаются за границей решения.

## 2026-09-17 — Container Stage 3B Phase B3 move и unpack-all

- Move использует один exact transfer на contained scope; empty/same-location не создают
  fake items/movements. Warehouse выводится из root location ancestor.
- Structural operation `container_id` выбран для replayable itemless move.
- Legacy move trigger сохраняется; duplicate projection подавляется только проверенным
  transaction-local GUC текущей unfinished move operation.
- Unpack-all переиспользует B2.2 paired movement/content mutation protocol и формирует
  items из locked server snapshot. B4/B5/Stage 3C не включены.

## 2026-09-17 — Container Stage 3B B4 final writer closure

- Статус: `active`; production migration применяется владельцем БД вручную.
- Связанные endpoints: `POST /api/movements`, `/api/containers/register`,
  `/api/container-operations/*`, `/api/system/recalculate-inventory`,
  `GET /api/system/audit-summary`.
- Связанные миграции: `20260917_container_b4_final_preflight.sql`,
  `20260917_add_container_b4_final.sql`.
- Generic movements сохранены как loose-only contract; container-code batch rejected атомарно.
- Legacy container move/unpack удалены, register ограничен empty creation.
- Простой DB guard разрешает container-coded movement только с complete container-operation provenance.
- Recalculate не repair-ит contents и откатывается при container projection mismatch.
- Runtime non-superuser role и будущие container shipment/kit/re-sorting/receipt/nesting rules
  сознательно не входят в B4.

## 2026-09-18 — Container Stage 3C C1 KIZ holder

- Статус: `active`; production migration применяется вручную владельцем БД.
- Active holder выбран как exact XOR `location_id/container_id`; QR не хранится в KIZ.
- Physical quantity остаётся только в movement ledger; один item quantity связывается с N
  KIZ identities без synthetic per-KIZ movements.
- `kiz_codes=[]` означает расход unidentified units, а не автоматический выбор КИЗ.
- Pair legs fill/extract/unpack группируются в один history item; move сохраняет holder.
- Canonical lock order: container/projections до ordered KIZ locks.
