# Business Rules

Статус: `CURRENT`.

Этот файл разделяет правила, подтвержденные DDL `docs/archive/snapshots/wms_schema.sql`, и правила, которые должны обеспечиваться Python-кодом или внешними процессами.

## Подтверждено DDL

### Локации

- `location_code` уникален и генерируется `wms.generate_location_code()` при insert.
- `path` обязателен и генерируется `wms.generate_location_path()` при insert или смене `parent_location_id`.
- Иерархия хранится через `parent_location_id` и LTREE `path`.
- Parent location должен существовать; удаление parent с зависимостями ограничено FK.
- Допустимые `zone_type`: `receiving`, `storage`, `picking`, `packing`, `shipping`, `quarantine`, `NULL`.
- DDL не запрещает неактивного parent и не задает допустимый диапазон `level`.

### Остатки и движения

- `wms.movements` - event log; `wms.inventory` - materialized state.
- Insert в `movements` автоматически обновляет `inventory`.
- `to_location_id` увеличивает остаток; `from_location_id` уменьшает остаток.
- Inventory row удаляется при `quantity <= 0`.
- `inventory.quantity` не может быть отрицательным.
- Inventory уникален по `product_id/location_id/status/batch_number/container_code` с `NULLS NOT DISTINCT`.
- `movement_type` ограничен набором `receive`, `putaway`, `transfer`, `pick`, `ship`, `unpack`, `adjust`, `kit_assembly`, `kit_disassembly` после применения kit operations migration.
- DDL не требует положительного `movements.quantity` и не требует заполнения хотя бы одной стороны movement.

### Контейнеры

- `containers.qr_code` уникален.
- Допустимые типы: `pallet`, `box`, `cage`, `trolley`.
- Допустимые статусы: `empty`, `open`, `sealed`, `blocked`.
- Stage 3B B1 поддерживает только flat containers: `parent_container_id IS NULL`.
- Active содержимое контейнера при insert создает `receive` movement и через него inventory.
- Перемещение контейнера по `location_id` создает `transfer` movements для всех inventory rows с `container_code = qr_code`.
- `block_empty_container` блокирует контейнер только если нет active contents.

### Распаковка

- Legacy `unpack_from_container` удаляется B4 migration; partial extract выполняется B2.2, full server-snapshot unpack — B3.

### Заявки и FBS

- Допустимые `task_type` и `tasks.status` enforced check constraints.
- Task может иметь parent task; task items каскадно удаляются с task.
- FBS shipment/item statuses enforced check constraints.
- Retry state хранится в `retry_count`, `max_retries`, `next_retry_at`; для pending retry есть partial index.

## Зависит от Python-кода или внешнего процесса

- Запрет дочерней локации под неактивным parent.
- Запрет размещения в неактивную локацию.
- Batch movements all-or-nothing и лимит batch size.
- Создание task минимум с одной позицией и положительным `quantity_planned`.
- Жизненный цикл assign/start/complete/cancel/approve/recount и права пользователей.
- Уведомление approvers и логика `public.user_permissions`.
- FIFO/FEFO рекомендации.
- FBS consumer, RabbitMQ ACK/NACK, Pydantic validation, группировка по `product_id`, retry worker/backoff.
- HTTP FBS adapter сохраняет синтаксически корректный JSON до доменной валидации с `source=http_api`; невалидная схема фиксируется как `validation_failed`.
- FBS product group атомарно блокирует items/СЗ, создаёт movement (и trigger inventory),
  отмечает СЗ отгруженными, связывает все items с movement и пересчитывает shipment status.

## Мягкие резервы товаров

- Резерв является отдельной сущностью и не является физическим движением товара.
- Резерв нельзя записывать в `wms.inventory` и нельзя отражать через `wms.movements`.
- MVP резервируется только по `product_id + external_order_id`; location, container, batch/FIFO/FEFO не используются.
- Входящее RabbitMQ-поле `wild` трактуется как `product_id` и должно соответствовать `public.products.id`.
- Для MVP один `external_order_id` означает `reserved_qty = 1`.
- Статусы `new`, `processing`, `fictitious` делают резерв активным (`is_reserved=true`).
- Статусы `shipped`, `burned` снимают резерв (`is_reserved=false`).
- `shipped` только снимает мягкий резерв и не создает физическое списание. Физическое списание остается в существующем FBS `ship` movement flow.
- Неизвестный товар записывается в audit как `product_not_found` без изменения текущего состояния резервов.
- Неизвестный статус записывается в audit как `unknown_status` без изменения текущего состояния резервов.
- `free_qty` в доступности товара может быть отрицательным; это показывает нехватку под активные резервы.
- `older_than_hours` в списке резервов только фильтрует по `last_event_at`; автоснятие резерва по TTL не выполняется.

### Availability API

- JSON-поля `physical_qty`, `reserved_qty`, `free_qty`, `shortage_qty` в availability responses отдаются числами, а не строками Decimal.
- `GET /api/inventory/availability` читает `wms.v_product_availability` и не изменяет `wms.inventory`, `wms.movements` или резервы.
- `only_shortage=true` показывает только строки с `shortage_qty > 0`.
- `only_reserved=true` показывает только строки с `reserved_qty > 0`.
- `GET /api/inventory/availability/totals` считает `shortage_qty` как `SUM(shortage_qty)` по строкам availability, а не как `GREATEST(SUM(reserved_qty) - SUM(physical_qty), 0)`.
- `GET /api/inventory/location/{location_id}/availability` считает physical quantity внутри subtree локации через `wms.locations.path <@ parent.path`, а reserved quantity берет глобально по `product_id`.
- Для location availability: `free_qty = physical_qty_in_location_subtree - reserved_qty_global`, `shortage_qty = GREATEST(reserved_qty_global - physical_qty_in_location_subtree, 0)`.

## External FBS write-off

- Standard и external-detected FBS используют одну бизнес-логику и одну FBS location.
- `fbs_shipments.source` задается consumer-ом, а не определяется из payload.
- Movement создается только если атомарно захвачены все уникальные assembly tasks группы через `UPDATE ... RETURNING`. Дубли assembly tasks запрещены.
- `assembly_task.is_shipped`, movement, inventory trigger и success/movement_id всех items группы атомарны.
- Повторное списание уже отгруженной assembly task запрещено.
- Already-shipped является обычным дублем только при наличии `success` item с
  `movement_id` для всех СЗ группы; отсутствие такой связи считается явной
  неконсистентностью и не запускает новое списание.
- Если `settings.FBS_VALIDATE_ASSEMBLY_TASKS = False`, FBS flow не читает и не обновляет `public.assembly_task`; это тестовый режим для контуров без таблицы/данных СЗ. Pydantic-контракт payload сохраняется: `assembly_tasks` обязательны и `quantity == len(assembly_tasks)`.

## Kit operations MVP

- Комплектация и разукомплектация комплектов выполняются без вызовов 1С и без RabbitMQ.
- Внешняя синхронизация с 1С для kit operations не выполняется.
- Источник состава комплекта - `public.products.kit_components`; комплект должен быть `is_active=true`, `is_kit=true`, с непустым составом.
- Все компоненты из состава должны существовать в `public.products`, быть активными, а `quantity_per_kit` должен быть больше 0.
- `location_code` для `POST /api/kit-operations` обязателен, должен существовать в `wms.locations`, быть активным и иметь активную настройку в `wms.operation_locations` с `operation_code='kit_operations'` и `scope='direct'`.
- Разрешённых локаций комплектации может быть несколько; они управляются через `GET/POST/PATCH /api/kit-operations/locations`.
- Проверка `level=5` для kit operations не применяется: разрешённой может быть зона или адрес другого уровня.
- `scope='direct'` означает, что операция использует только остатки непосредственно на `operation_locations.location_id`; дочерние адреса/subtree не учитываются.
- MVP поддерживает только россыпь на выбранной локации: `wms.inventory.status='available'`, `batch_number IS NULL`, `container_code IS NULL`. Если расходный остаток есть только в контейнере, операция возвращает conflict.
- Write flow выполняется в одной DB transaction: validation, проверка `operation_locations`, advisory lock, `SELECT inventory ... FOR UPDATE`, создание operation/items/movements, completion.
- Остатки не меняются напрямую через `wms.inventory`; создаются только `wms.movements`.
- Assembly создает `kit_assembly` movements: компоненты с `from_location_id`, результат-комплект с `to_location_id`.
- Disassembly создает `kit_disassembly` movements: комплект с `from_location_id`, компоненты с `to_location_id`.
- Все movements kit operations имеют `source_type='kit_operation'`, `source_id=operation_id`, `source_item_id=item_id` и metadata с ролью строки.
- Роли строк: `component_consumption` - списание компонента при `assembly`; `kit_result` - приход готового комплекта при `assembly`; `kit_consumption` - списание комплекта при `disassembly`; `component_result` - приход компонентов при `disassembly`.
- Retry/idempotency key для kit operations не реализован; повторный одинаковый запрос не дедуплицируется на уровне API/БД.

## Пересортица

- From/to существуют, активны и различаются; kit обрабатывается как обычный SKU.
- Quantity — строго положительное целое; outgoing и incoming равны, направленный net delta равен нулю.
- Только точная active direct-location из allow-list `re_sorting_operations`; subtree не читается.
- Расходуется только доступный россыпной остаток без партии и контейнера; мягкие резервы
  не участвуют в этом расчёте.
- Операция атомарна, создаёт две item-строки и два movements; inventory напрямую не изменяется.
- Нет вызовов 1С/RabbitMQ и нет idempotency key.

## KIZ v1

- Assignment нового глобально уникального case-sensitive кода разрешён при
  physical_quantity - COUNT(active KIZ) >= 1 в точной available/NULL/NULL строке.
- Код не нормализуется; пустой код и окружающие пробелы запрещены. Duplicate всегда
  conflict, в том числе после terminal. Inactive product/location сами по себе
  не запрещают assignment существующего physical stock.
- Physical 1.5 допускает один KIZ; terminal KIZ не участвует в active count.
- Assignment создаёт только KIZ и одно assigned event; movement не создаётся.
- Только active → error/deactivated, с author/reason и одним audit event.
  Повторный terminal, hard delete, изменение кода и повторное использование запрещены.
- Количественные расходы без явных КИЗ могут использовать только неидентифицированную
  по КИЗ часть; автоматического
  выбора KIZ нет. KIZ-конфликт — 409, FBS item failed без автоматического retry.
- 40001/40P01 — конкурентный conflict 409. FBS использует ограниченный max_retries.
- Пересчёт запрещён, если рассчитанный физический остаток меньше КИЗ-идентифицированного, в том числе при
  обнулении scope; операция полностью откатывается с diagnostics.


### KIZ v1: входная валидация D1/D2 (2026-09-08)

Assignment отклоняет с 422 код `stock-summary`, коды с окончанием `/events` и
сегменты пути `.`/`..` и перевод строки: они конфликтуют с маршрутизацией карточки или нормализацией URL.
Metadata assignment и terminal проверяется рекурсивно, включая ключи объектов:
NUL и некорректные Unicode surrogate-символы дают 422 до записи в БД.

## KIZ Stage 2A Phase 2

- `shipped` означает успешный физический выход конкретной единицы со склада и не
  заменяется состояниями `error` или `deactivated`.
- У active KIZ есть текущая location; у shipped текущей location нет.
- Связь KIZ с physical movement хранит только identities и не дублирует физические поля.
- Phase 2 не предоставляет способ переместить или отгрузить KIZ и не создаёт links при
  warehouse assignment.

## KIZ Stage 2A Phase 3

- Идемпотентность применяется только к explicit KIZ transfer/ship.
- Совпавшие key и fingerprint означают replay сохранённого результата; другой
  fingerprint при том же key означает idempotency conflict.
- Порядок KIZ и operation items не меняет fingerprint; duplicate KIZ и
  external_line_id отклоняются canonicalizer-ом.
- `result_payload` — response snapshot, не источник inventory/current state.

## Explicit KIZ transfer

Flow переносит только доступный россыпной остаток точной локации без партии и контейнера.
KIZ выбираются явно; сервис не выбирает их автоматически. Пустой список переносит
неидентифицированный по КИЗ остаток. Каждая
строка создаёт один movement независимо от числа KIZ. Request атомарна и идемпотентна
по `(source_system, transfer, external_operation_id)`.

## Explicit KIZ shipment

Flow списывает доступный россыпной остаток точной локации без партии и контейнера.
KIZ выбираются явно; пустой список расходует только неидентифицированный по КИЗ
остаток. Для `P/I/U/Q/N` обязательны `0 <= N <= Q` и `Q-N <= U`.
Каждая строка создаёт один ship movement полного Q. Selected KIZ атомарно переходят
`active/source → shipped/NULL`, получают link и shipped event. Idempotency key:
`(source_system, ship, external_operation_id)`.

## KIZ history

- Current state — только projection `wms.kiz`; исторические location берутся из events
  и movements.
- Physical timeline строится только по KIZ movement links и stable `movement_ref`, без
  reason/metadata heuristics.
- Assignment остаётся lifecycle event без movement; artificial receive запрещён.
- Shipped event и ship movement одного KIZ/`movement_ref` показываются одной записью
  `ship`; `quantity` относится ко всему movement, а GET не исправляет anomalies.

## Container Stage 3B B1

- `container_id` и `qr_code` после создания неизменяемы; hard delete и QR reuse другой
  identity запрещены supported protocol.
- Контейнер flat, `parent_container_id IS NULL`, direct `location_id` обязателен.
- `empty` не имеет active contents; `open/sealed/blocked` могут иметь contents.
- `sealed` требует будущего controlled reopen перед изменением состава; `blocked`
  запрещает physical operations domain protocol.
- Один exact container/product/batch/status scope, включая NULL batch, существует не
  более одного раза. Current content quantity строго положительна.
- Register создаёт только empty container; товар поступает через loose receipt и controlled fill.
- Будущий move проверяет один warehouse через общий root location; B1 move не меняет.

## Container Stage 3B B2.1 fill

- Fill — conservation operation, не receipt: `loose - Q`, `contained + Q`, total unchanged.
- Источник: exact product/batch, `available`, direct container location,
  `container_code IS NULL`.
- Target container обязан быть flat, иметь direct location и status `empty` либо `open`.
- Успех переводит `empty -> open`; `sealed/blocked` возвращают business conflict.
- Один request атомарен; суммарный расход повторяющегося loose scope валидируется после locks.
- Decimal quantity соответствует `numeric(10,2)`; float и скрытое округление запрещены.
- Exact idempotent replay не создаёт повторных movements/contents increment.

## Container Stage 3B B2.2 extract

- Extract — conservation operation: `contained - Q`, `loose + Q`, total unchanged.
- Source и destination имеют один direct `container.location_id`, status `available`,
  exact product и NULL-safe exact batch; container обязан быть flat и `open`.
- Duplicate product/batch scope в одном request запрещён; все разные items применяются
  атомарно.
- Partial extract уменьшает active current content. Full extract удаляет active current
  row, не записывая запрещённое `quantity=0`.
- После всех items container остаётся `open`, пока существует любой active content;
  после удаления последнего active content становится `empty`.
- Legacy unpack не поддерживается после B4; используется exact B2.2 extract.

## Container B3 move и unpack-all

- Move разрешён для `empty/open/sealed`, запрещён для `blocked` и между root warehouses.
- Same-location move успешен как no-op; empty move не создаёт quantitative ledger.
- Move сохраняет status/contents и переносит каждый contained scope одним transfer.
- Unpack-all разрешён только для `open`; items задаёт locked server snapshot.
- Unpack-all удаляет все current active contents, переводит stock в loose и status в empty.
- Exact replay всегда возвращает сохранённый result до повторной state validation.

## Container Stage 3B B4 Final

- Generic movements являются только loose-stock contract: `container_code` всегда NULL.
- Container quantity/location меняют только fill, extract, move и unpack-all.
- Container создаётся только пустым; поступление выполняется `receipt → loose → fill`.
- Legacy move/unpack и register-with-contents больше не поддерживаются.
- Shipment, kit operations и re-sorting внутри container не поддерживаются: сначала extract.
- Task availability/suggestions/recount используют exact `available`, NULL-container, NULL-safe batch scope.
- Recalculate не исправляет container contents: любое расхождение ledger/contents/inventory/location
  останавливает и откатывает maintenance transaction.

## Container Stage 3C C1

- Active KIZ имеет exact XOR direct holder: location либо container.
- Empty `kiz_codes` расходует только unidentified quantity; selected identities не
  выбираются автоматически. `len(kiz_codes) <= quantity`.
- KIZ-aware container item поддерживает только `batch_number=NULL`.
- Fill переносит selected loose KIZ в container; extract возвращает выбранные identities
  в direct location контейнера; unpack-all возвращает все; move holder не меняет.
- mark-error/deactivate сохраняют последний holder snapshot без physical movement.
- Loose transfer/ship не принимают contained KIZ; сначала нужен extract/unpack-all.
