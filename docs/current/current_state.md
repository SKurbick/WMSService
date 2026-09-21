# Current State

Статус: `CURRENT`. Документ описывает текущие возможности сервиса; история решений и изменений ведётся в `decisions.md`.

## Назначение

Проект является FastAPI-сервисом WMS для адресного хранения. Основной код находится в `app/`.

Сервис покрывает:

- иерархию складских локаций;
- контейнеры;
- остатки;
- движения товаров;
- заявки на складские операции;
- отчеты;
- системные операции сверки/пересчета;
- журнал и обработку ФБС-отгрузок из RabbitMQ;
- уведомления.

## Технологический стек

- Python, FastAPI.
- `asyncpg` и PostgreSQL.
- Схема БД `wms`, также используются таблицы `public.products`, `public.assembly_task`, `public.user_permissions`.
- Подключение к БД через глобальный pool `asyncpg.create_pool`.
- `search_path` для соединений: `wms,public`.
- RabbitMQ consumer через `aio_pika`.

## Структура приложения

- `app/main.py` - создание FastAPI-приложения, lifespan, middleware, подключение API router, запуск consumer/retry worker.
- `app/api/v1/endpoints/` - HTTP endpoints.
- `app/core/services/` - бизнес-логика.
- `app/core/schemas/` - Pydantic-схемы запросов/ответов.
- `app/core/enums.py` - доменные enum-типы.
- `app/infrastructure/database/repositories/` - репозитории поверх `asyncpg`.
- `app/infrastructure/database/queries/` - SQL-запросы.
- `app/handlers/write_off_fbs_handler.py` - обработка ФБС-списаний.
- `app/consumer.py` - RabbitMQ consumer.
- `app/retry_worker.py` - фоновая переобработка pending retry ФБС-позиций.

## Миграции и тесты

В репозитории есть SQL-миграции в `scripts/migrations/` и pytest-тесты в `tests/`. Alembic не используется. Карта БД составлена по SQL-запросам, схемам ответов, миграциям и вызовам PostgreSQL-функций.

## Важное текущее состояние

- ORM-моделей нет: проект работает напрямую с SQL через `asyncpg`.
- Остатки считаются производными от `wms.movements`; в коде есть системная сверка и пересчет `wms.inventory` из движений.
- Создание batch movements в публичном endpoint выполняется в транзакции.
- Часть операций атомарности делегирована PostgreSQL-функциям и триггерам:
  - `wms.register_container` (после B4 только empty creation);
  - `wms.find_available_location`;
  - триггер обновления inventory при вставке в `wms.movements`;
  - триггер генерации `location_code` и `path` для `wms.locations`.
- Конкурентный доступ явно обработан не везде; где нет явных блокировок в Python/SQL, это вынесено в открытые вопросы.

## Мягкие резервы товаров

Добавлен механизм мягкого резерва по `product_id + external_order_id` для сообщений RabbitMQ формата `wild/orders`. Резервы хранятся отдельно от физических остатков: код не пишет резервы в `wms.inventory` и не создает `wms.movements`.

Основные компоненты:

- `app/core/services/stock_reservation_service.py` - бизнес-логика статусов резервов и audit входящих событий;
- `app/infrastructure/database/repositories/stock_reservation_repository.py` - доступ к таблицам резервов и view доступности;
- `app/infrastructure/database/queries/stock_reservations.py` - SQL для резервов;
- `app/consumer.py` - содержит отдельные RabbitMQ consumers для FBS write-off и stock reservations.

## RabbitMQ consumers

RabbitMQ обработка разделена по очередям:

- FBS write-off consumer запускается через `settings.CONSUMER_ENABLED` и слушает `settings.RABBITMQ_QUEUE`;
- stock reservation consumer запускается через `settings.RESERVATION_CONSUMER_ENABLED` и слушает `settings.STOCK_RESERVATION_QUEUE`;
- пустая очередь резервов не создает записей в БД: consumer просто ожидает новое сообщение от RabbitMQ;
- ошибки БД при обработке резервов логируются с `exc_info=True` и приводят к `nack(requeue=True)`, чтобы отсутствие таблиц/view резервов не маскировалось.

## External FBS write-off

- Добавлен независимый consumer очереди `EXTERNAL_FBS_QUEUE` (`wms.fbs.external_write_off` по умолчанию), управляемый `EXTERNAL_FBS_CONSUMER_ENABLED`.
- Standard и external-detected сообщения используют общий `consume_fbs_queue` и `handle_write_off_fbs`.
- Источник хранится в `wms.fbs_shipments.source`: `standard`, `external_detected` или `http_api`.
- Добавлен ручной retry позиции: `POST /api/fbs-shipments/items/{item_id}/retry`.

## Kit operations

Добавлен HTTP flow комплектации и разукомплектации комплектов без вызовов 1С, без RabbitMQ и без внешней синхронизации. Состав комплекта читается из `public.products.kit_components`.

Поддерживаемые типы операции:

- `assembly` - сборка комплекта из компонентов;
- `disassembly` - разборка комплекта обратно на компоненты.

Основные компоненты:

- `POST /api/kit-operations` - атомарно создает запись операции, строки операции и movements;
- `GET /api/kit-operations` и `GET /api/kit-operations/{operation_id}` - read endpoints журнала;
- `GET/POST/PATCH /api/kit-operations/locations` - управление разрешёнными direct-локациями комплектации;
- `wms.operation_locations` - список WMS-локаций, где разрешены kit operations;
- `wms.kit_operations` и `wms.kit_operation_items` - журнал операций и строк;
- `wms.movements.source_type/source_id/source_item_id` связывает movement с источником `kit_operation`;
- `POST /api/kit-operations` требует активную `wms.operation_locations` строку с `operation_code='kit_operations'`, `scope='direct'`, `is_active=true`;
- расходные остатки проверяются и блокируются через `SELECT ... FOR UPDATE`;
- параллельные операции одного `kit_product_id + location_id` сериализуются advisory lock внутри transaction.

MVP работает только с россыпью на выбранной direct-локации: `inventory.location_id = operation_locations.location_id`, `status='available'`, `batch_number IS NULL`, `container_code IS NULL`. Дочерние адреса не учитываются.

Роли строк `wms.kit_operation_items`: `component_consumption` - списание компонента при `assembly`; `kit_result` - приход готового комплекта при `assembly`; `kit_consumption` - списание комплекта при `disassembly`; `component_result` - приход компонентов при `disassembly`.

Retry/idempotency key для `POST /api/kit-operations` не реализован: повторный одинаковый запрос создаст новую операцию, если пройдет валидацию и остатков достаточно.

## Re-sorting operations

Добавлен синхронный HTTP flow пересортицы loose physical stock между двумя разными SKU в одной разрешённой direct-локации. Операция создаёт два movements (`source_outgoing` и `target_incoming`) одинакового целого количества и не вызывает 1С/RabbitMQ.
## Дневная история остатков

Добавлен read-only endpoint `GET /api/inventory-history/daily-balances`. Источник расчёта —
только `wms.movements`; `wms.inventory` и `wms.inventory_snapshots` не читаются. Каждая
сторона movement становится ledger-строкой (`to_location_id` — плюс/incoming,
`from_location_id` — минус/outgoing), без allow-list по `movement_type`.

Дневные границы и группировка используют `Europe/Moscow`, период полуоткрытый на уровне
timestamp и включительный на уровне query dates. Партии, контейнеры, source и movement
types агрегируются до `product_id + day`. Календарь создаётся `generate_series`, поэтому
дни без операций присутствуют. Пагинация выполняется по товарам, а не по строкам дней.
Location scope поддерживает exact и LTREE subtree (`child.path <@ root.path`). Count и
данные читаются в одной read-only repeatable-read транзакции.

Новых таблиц, snapshots, индексов и миграций не добавлено. Write-сценарии, movements,
inventory, функции и триггеры не изменялись.
## Единый список операций

Реализован read-only `GET /api/operations-history`. Endpoint объединяет kit operation,
re-sorting operation, FBS shipment и standalone movement adapters. Все branches приводят
данные к общему контракту до `UNION ALL`, фильтры pushdown-ятся, итоговая пагинация идёт
после объединения. Count и page читаются одним DB snapshot в read-only
`REPEATABLE READ` transaction.

Movement response type остаётся строкой и не зависит от расходящегося write enum.
Известные DB types отображаются русскими именами, неизвестное историческое значение
возвращается без ошибки с `operation_name=operation_type`. Количества остаются
PostgreSQL numeric/Python Decimal до JSON serialization.

Дедупликация только структурная: kit/re-sort по source fields, FBS по однозначному
movement_id match. Legacy receipt/task/container эвристики отсутствуют. Receipt/task/container headers
не входят в unified operations list; отдельные receipt-history endpoints реализованы.
Detail adapters kit/re-sorting/FBS/
movement доступны через `GET /api/operations-history/{event_id}`. Связи kit/re-sorting
разрешаются по точной паре `(movement_id, movement_created_at)`, FBS — по movement_id с
обязательным подсчётом кандидатов. Missing/ambiguous links дают typed warning, а не 500.
DB schema, migrations, triggers, movements/inventory и write flows не изменялись.

## История поступления

Добавлен `GET /api/receipts/{guid}/history`. Revision key — точный `COALESCE(
update_document_datetime, document_created_at, supply_date)`; legacy timestamp считается
московским local time. Строки без всех дат образуют отдельные `legacy:<id>` revisions.
Current определяется через `is_valid IS TRUE`, не максимальной датой. Current snapshot
читается отдельно из `wms.receipt_items`; movements не используются и эвристически не
связываются. Count, page и items читаются в read-only `REPEATABLE READ` transaction.

## Список ревизий поступлений

Добавлен GET /api/receipts/history для вкладки документов. Legacy branch группируется
по тому же точному revision key, что detail; WMS-only branch включает только GUID,
полностью отсутствующие в legacy. Snapshot существующего legacy GUID отражается через
has_current_snapshot и snapshot_updated_at, но отдельной строкой не становится.
Undated rows по умолчанию исключены. Переход list → detail выполняется только по GUID.
Changed/physical/movement fields намеренно отсутствуют: структурной receipt→movement
связи нет. Count и page читаются одним read-only repeatable-read snapshot.

## HTTP FBS ingestion

- `POST /api/fbs-shipments` принимает непустой JSON-массив существующей схемы
  `WriteOffAccordingToFBS`.
- Синтаксически корректный payload сохраняется до доменной валидации с
  `source=http_api`; ошибка схемы сохраняется как `validation_failed`.
- Валидный payload синхронно использует общий `handle_write_off_fbs`, включая
  группировку, транзакционную обработку product group, movements и retry.
- DB constraint для `http_api` применяется владельцем БД вручную.

## KIZ v1

Реализован KIZ v1 в коде; перед deployment владелец БД вручную применяет
[миграцию](../../scripts/migrations/20260906_add_kiz_v1.sql).
Scope: точная location, available, batch/container NULL. Assignment идентифицирует
существующую единицу без physical delta и movement; registry/audit, terminal lifecycle,
stock-summary и отдельная KIZ integrity проверка доступны через API.
Inventory guard защищает КИЗ-идентифицированную часть от расхода всеми writers. Assignment делает
согласованный MVCC touch; recalculate использует UPSERT и удаление obsolete scopes.
[Контракт, примеры и ограничения](../flows/kiz_v1.md).

## KIZ Stage 2A — Phase 1

Подготовлена movement identity infrastructure: composite unique coordinate на
partitioned `wms.movements`, непартиционированная `wms.movement_registry`, автоматическая
регистрация каждого нового movement на DB boundary, resumable batch backfill и read-only
integrity check. Публичные API и legacy movement links не изменены. Phase 1 применена
владельцем БД; [порядок deployment и backfill](../flows/kiz_stage2a_phase1.md).

## KIZ Stage 2A — Phase 2

Добавлен immutable слой `wms.kiz_movement_links` между KIZ и `movement_ref`.
Lifecycle/read schemas допускают `shipped` с `location_id=NULL`; card/list используют
LEFT JOIN и не теряют такую запись. `kiz_events.movement_ref` nullable для старых
событий и обязателен для будущего shipped event, причём composite FK требует link того
же KIZ. Assignment/error/deactivate/stock/integrity contracts не изменены.

Phase 1 — completed. Phase 2 — completed и применена владельцем БД.
[Контракт и deployment](../flows/kiz_stage2a_phase2.md).

## KIZ Stage 2A — Phase 3

Добавлены изолированные `wms.kiz_operations` и `wms.kiz_operation_items` для будущих
transfer/ship. UNIQUE business key и row lock дают new/replay/fingerprint conflict без
advisory locks. Internal service работает только с caller-owned transaction; deferred
constraint требует сохранить result до commit. Fingerprint — SHA-256 canonical JSON.

Phase 1 — completed. Phase 2 — completed. Phase 3 — completed.

## KIZ Stage 2A — Phase 4

Добавлен `POST /api/kiz-operations/transfer`: один atomic idempotent request переносит
доступный россыпной остаток точной локации, explicit active KIZ и создаёт один movement
на item, registry reference, operation attachment и immutable links. Поддерживаются
КИЗ-идентифицированные, смешанные и неидентифицированные по КИЗ transfers. Старые
endpoints не изменены.

Phase 4 — completed и применена владельцем БД.
[Контракт и deployment](../flows/kiz_stage2a_phase4.md).

## KIZ Stage 2A — Phase 5

Добавлен POST /api/kiz-operations/ship: atomic idempotent shipment создаёт один
outgoing movement на item, уменьшает physical quantity, переводит явно выбранные КИЗ
из active/source в shipped/NULL, связывает их с movement_ref и создаёт shipped events.
Mixed и unidentified-only semantics совпадают с transfer; автоматического выбора КИЗ
нет. Direct lifecycle UPDATE остаётся запрещён DB guard.

Phase 1 — completed. Phase 2 — completed. Phase 3 — completed.
Phase 4 — completed. Phase 5 — completed. Stage 2A внутренне завершён.
Stage 2B FBS KIZ и Stage 2C receipt KIZ не реализованы.
[Контракт и deployment](../flows/kiz_stage2a_phase5.md).

## KIZ Stage 3A — unified history

Добавлен read-only `GET /api/kiz-history?kiz_code=<KIZ>`. Endpoint возвращает
существующую KIZ card как current state и единую chronological timeline из
`kiz_events` и linked physical movements. Shipment lifecycle event и ship movement с
одним `movement_ref` объединяются; transfer берётся только из movement link, assignment
не получает synthetic receive. История shipped KIZ читается при current location NULL.

Используются два фиксированных запроса в одной read-only repeatable-read transaction.
Новых таблиц, индексов, migration и write flows нет.
[Контракт и merge semantics](../flows/kiz_stage3a_history.md).

## Container Stage 3B Phase B1 — completed (2026-09-15)

Подготовлены preflight и migration стабильной container identity. `container_id` —
внутренняя identity, `qr_code` — неизменяемая внешняя identity без supported reuse.
Контейнеры плоские, имеют direct `location_id`, а API/DB используют statuses
`empty/open/sealed/blocked` и types `pallet/box/cage/trolley`.

Legacy register остаётся receipt-like; существующие URLs не изменены. B1 migration
применена владельцем БД. Агент production writes не выполнял.
[Полный контракт и runbook](../flows/container_stage3b_phase_b1.md).

## Container Stage 3B Phase B2.1 — completed (2026-09-15)

Добавлен идемпотентный `POST /api/container-operations/fill`: existing available loose
stock переносится в flat `empty/open` container на той же direct location без изменения
physical total. На строку создаётся связанная пара transfer movements и два stable
movement refs; contained inventory и active `container_contents` проверяются до commit.

Existing register с `contents=[]` используется как zero-effect create-empty flow.
B2.1 migration применена владельцем БД, ручной acceptance прошёл успешно.
[Контракт и runbook](../flows/container_stage3b_phase_b21_fill.md).

## Container Stage 3B Phase B2.2 — completed in code (2026-09-16)

Добавлен `POST /api/container-operations/extract`: available contained stock переносится
в loose scope direct location контейнера с сохранением physical total. Partial extract
уменьшает active content; full extract удаляет current row без `quantity=0`. Статус
становится `empty` только после удаления последнего active content.

B2.2 preflight/migration подготовлены, но production apply и production extract агентом
не выполнялись. Следующая реализованная фаза B3 описана ниже; актуальный статус B4/C1 приведён далее.
[Контракт и runbook](../flows/container_stage3b_phase_b22_extract.md).

## Container Stage 3B Phase B3 — completed in code (2026-09-17)

Добавлены идемпотентные `POST /api/container-operations/move` и
`POST /api/container-operations/unpack-all`. Move переносит empty/open/sealed flat
container только внутри root warehouse; empty/same-location не создают fake movements.
Unpack-all делает server-side locked snapshot всех active contents и зеркально B2.2
переводит их в loose stock, после чего status становится `empty`.

B3 preflight/migration подготовлены; production apply и production operations агентом не
выполнялись. B4 closure и актуальный статус Stage 3C приведены следующими разделами.
[Контракт и runbook](../flows/container_stage3b_phase_b3_move_unpack_all.md).

## Container Stage 3B Phase B4 Final — completed in code (2026-09-17)

Generic movements ограничены loose stock; legacy container move/unpack удалены из API,
register стал empty-only. Task availability использует exact loose scope. Recalculate получил
transactional container pre/post guard, а audit-summary — container mismatch/provenance counters.
Подготовлены `20260917_container_b4_final_preflight.sql` и
`20260917_add_container_b4_final.sql`; production apply выполняет владелец БД вручную.
Runtime superuser остаётся отдельным infrastructure debt и не блокирует Stage 3C.

## Container Stage 3C Phase C1 — completed in code (2026-09-18)

Реализованы mixed identified/unidentified fill/extract, KIZ-aware whole-container move и
unpack-all, container holder read models/history/integrity и terminal transitions.
Подготовлены C1 preflight/migration/runbook; production migration и production writes
агентом не выполнялись. Следующие Stage 3C phases не начаты.
