# Known Issues

Источник: [`wms_schema.sql`](../archive/snapshots/wms_schema.sql), [`risk_map.md`](../archive/audits/risk_map.md), [`functions.md`](../database/functions.md), [`indexes_constraints.md`](../database/indexes_constraints.md), [`invariants.md`](invariants.md).

## 1. `unpack_from_container` конфликтует с constraints

- Triage: `confirmed-in-snapshot`, runtime verification required
- Verified: 2026-08-07 по `wms_schema.sql` function и checks

- Severity: high
- Affected tables/functions: `wms.unpack_from_container`, `wms.container_contents`, constraints `container_contents_quantity_check`, `chk_content_status`.
- Possible impact: полная распаковка товара из контейнера может падать check violation, потому что функция делает `quantity = 0` и затем пытается поставить `status = 'empty'`, а DDL требует `quantity > 0` и разрешает только `active`, `replaced`, `removed`.
- Recommended next action: выбрать целевую модель распаковки: разрешить `quantity = 0/status = empty` миграцией или изменить функцию на существующие статусы `removed/replaced` без нулевого active content; после решения добавить regression test на полную и частичную распаковку.

## 2. Positive quantity constraint остаётся `NOT VALID`

- Triage: `confirmed-in-runtime`, cleanup and validation required
- Verified: 2026-08-08; runtime содержит constraint на parent/partitions и одну legacy violation

- Severity: high
- Affected tables/functions: `wms.movements`, partitions `wms.movements_2026_01` ... `wms.movements_2026_12`, `wms.update_inventory_from_movement`.
- Possible impact: constraint защищает новые и изменяемые строки, но `NOT VALID` не доказывает отсутствие legacy movements с `quantity <= 0`; runtime-аудит обнаружил одну legacy violation.
- Recommended next action: идентифицировать legacy violation на согласованном окружении и выполнить `VALIDATE CONSTRAINT` только после очистки или принятия исторических данных.

## 3. Movement side constraint остаётся `NOT VALID`

- Triage: `confirmed-in-runtime`, validation pending
- Verified: 2026-08-08; runtime содержит constraint на parent/partitions, legacy violations не найдены

- Severity: high
- Affected tables/functions: `wms.movements`, `wms.update_inventory_from_movement`.
- Possible impact: constraint защищает новые и изменяемые строки, но `NOT VALID` не доказывает отсутствие legacy movements без обеих сторон; runtime legacy violations не обнаружены.
- Recommended next action: после подтверждения владельцем БД выполнить `VALIDATE CONSTRAINT` после решения по историческим данным.

## 4. Parent `wms.movements` без PK

- Triage: `confirmed-in-snapshot`, open design decision DB-02
- Verified: 2026-08-07 по parent DDL и source-link consumers

- Severity: medium
- Affected tables/functions: `wms.movements`, all movement partitions, consumers of `movement_id`, `wms.fbs_shipment_items.movement_id`, `wms.tasks.related_movement_id`.
- Possible impact: нет DB-level уникальности `movement_id` на partitioned table; сложнее и небезопаснее ссылаться на movement из FBS/tasks/аудита.
- Recommended next action: проверить требования PostgreSQL к unique/PK на partitioned table; спроектировать PK/unique с учетом partition key `created_at` или отдельную стабильную ссылочную модель.

## 5. `container_code` без FK на `containers.qr_code`

- Triage: `confirmed-in-snapshot`, open design decision DB-03
- Verified: 2026-08-07 по constraints snapshot

- Severity: medium
- Affected tables/functions: `wms.inventory.container_code`, `wms.movements.container_code`, `wms.containers.qr_code`, `wms.sync_container_to_inventory`, `wms.move_container_inventory`, `wms.unpack_from_container`.
- Possible impact: в остатках и движениях могут появиться orphan container codes; связь `containers`, `container_contents`, `inventory` и `movements` может разойтись.
- Recommended next action: решить, является ли `container_code` обязательной ссылкой на container или историческим текстовым снимком; если это ссылка, добавить FK или перейти на `container_id` с сохранением QR в audit metadata.

## 6. `find_available_location` не резервирует место

- Triage: `accepted-limitation`, open design decision LOC-06
- Verified: 2026-08-07 по PL/pgSQL snapshot и advisory-only API contract

- Severity: medium
- Affected tables/functions: `wms.find_available_location`, `wms.locations`, `wms.inventory`, `public.products`.
- Possible impact: параллельные операции могут выбрать одну и ту же ячейку; функция учитывает вес, но не резервирует capacity и не учитывает объем, `is_pickable` или блокировки адреса.
- Recommended next action: определить модель резервирования/блокировки адреса; для размещения использовать транзакционный сценарий с повторной проверкой capacity под lock или отдельную таблицу reservations/locks.

## 7. `generate_location_path` не обновляет потомков при смене parent

- Triage: `confirmed-in-snapshot`, open design decision LOC-04
- Verified: 2026-08-07 по trigger function snapshot

- Severity: medium
- Affected tables/functions: `wms.locations`, `wms.generate_location_path`, `trg_generate_location_path`, LTREE queries через `locations.path`.
- Possible impact: после смены `parent_location_id` у узла его потомки сохранят старые `path`; дерево через FK и дерево через LTREE начнут расходиться.
- Recommended next action: либо запретить смену parent для локаций с потомками, либо реализовать каскадное обновление `path` потомков в одной транзакции и покрыть тестами.

## 8. FBS `movement_id` без FK

- Triage: `confirmed-in-snapshot`, open design decision DB-02
- Verified: 2026-08-07 по FBS/source-link DDL

- Severity: medium
- Affected tables/functions: `wms.fbs_shipment_items.movement_id`, `wms.movements`.
- Possible impact: FBS item может ссылаться на несуществующее или неверное движение; аудит списаний и retry-result reconciliation становятся ненадежными.
- Recommended next action: определить ссылочную модель на partitioned `movements`; после решения добавить FK/тип `bigint` или хранить устойчивый movement reference в отдельной таблице/metadata.

## 9. Read-then-write операции без `SELECT FOR UPDATE`/advisory locks

- Triage: `partially-mitigated`, runtime verification required
- Verified: 2026-08-07; kit/re-sorting используют locks, перечисленные legacy DB functions в snapshot — нет

- Severity: high
- Affected tables/functions: `wms.unpack_from_container`, `wms.block_empty_container`, `wms.find_available_location`, `wms.move_container_inventory`, `wms.container_contents`, `wms.containers`, `wms.inventory`, `wms.locations`.
- Possible impact: параллельные распаковки, блокировка контейнера одновременно с добавлением contents, выбор свободной ячейки и перемещение контейнера могут принимать решения на устаревших данных.
- Recommended next action: определить критические секции и порядок lock acquisition; добавить row-level locks или advisory locks для операций, где проверка и изменение должны быть атомарны.

## 10. `inventory_snapshots` без FK и unique

- Triage: `confirmed-in-snapshot`, open design decision DB-03
- Verified: 2026-08-07 по snapshot constraints

- Severity: low
- Affected tables/functions: `wms.inventory_snapshots`, `public.products`, `wms.locations`.
- Possible impact: snapshots могут содержать ссылки на несуществующие товары/локации или дубли одного и того же снимка, что искажает исторические отчеты.
- Recommended next action: решить, являются ли snapshots immutable audit data или derived cache; добавить FK/unique constraints либо documented cleanup/rebuild process.

## 11. `notifications.severity` без check constraint

- Triage: `confirmed-in-snapshot`, low priority
- Verified: 2026-08-07 по snapshot constraints

- Severity: low
- Affected tables/functions: `wms.notifications`.
- Possible impact: UI/API могут получить неизвестное значение severity, несмотря на комментарий `info`, `warning`, `critical`.
- Recommended next action: добавить check constraint или явно документировать, что severity свободный текст.

## 12. Дублирующие индексы на unique columns

- Triage: `confirmed-in-snapshot`, runtime plan verification required
- Verified: 2026-08-07 по snapshot indexes/constraints

- Severity: low
- Affected tables/functions: `wms.containers.qr_code`, `wms.locations.location_code`, indexes `idx_containers_qr`, `idx_locations_code`, unique constraints `containers_qr_code_key`, `locations_location_code_key`.
- Possible impact: лишняя стоимость insert/update и обслуживания индексов без очевидной пользы для чтения.
- Recommended next action: проверить query plans и удалить явные дублирующие индексы миграцией, если они не нужны для особых сценариев.

## External FBS MVP: оставшийся техдолг (2026-06-14)

- Triage: `confirmed-in-code`, runtime integration verification required
- Verified: 2026-08-07 по consumer/retry code и FBS transaction documentation

Исправлены гонка из-за игнорирования результата UPDATE `assembly_task.is_shipped` и разрыв между movement и item success/movement_id.

Остаются: ранний ACK; отсутствие message-level event_id/inbox/outbox; recovery зависших `processing/new`; retry worker без `FOR UPDATE SKIP LOCKED`; группировка только по product; `movement_id` без устойчивого FK и типа bigint; lookup location через отдельное соединение; отсутствие сверки product_id с assembly task; отсутствие integration-тестов транзакции с реальным PostgreSQL trigger.


## Kit operations MVP: ограничения (2026-07-08)

- Triage: `accepted-limitation`
- Verified: 2026-08-07 по schemas, service и SQL queries

- Severity: medium
- Affected endpoints/tables: `POST /api/kit-operations`, `wms.operation_locations`, `wms.kit_operations`, `wms.kit_operation_items`, `wms.movements`, `wms.inventory`.
- Subtree mode не реализован: `scope='direct'` использует только остатки на выбранной `location_id`, дочерние адреса не учитываются.
- Container stock для kit operations не поддерживается: расход возможен только из loose stock с `container_code IS NULL`; при наличии остатка только в контейнере endpoint возвращает conflict.
- Batch stock для расхода kit operations не поддерживается: MVP расходует только строки с `batch_number IS NULL`.
- Retry/idempotency key для kit operations не реализован: повторный одинаковый HTTP-запрос не дедуплицируется.
- Внешняя синхронизация с 1С для kit operations не выполняется.
- Recommended next action: перед расширением MVP отдельно спроектировать subtree semantics, расход из контейнеров/партий, idempotency key и внешнюю интеграцию, чтобы не ломать event log `wms.movements`.

## Re-sorting verification limits

- Triage: `runtime-verification-required`
- Verified: 2026-08-07 по migration и unit/contract test boundary

Unit/SQL-contract тесты не заменяют integration verification на PostgreSQL с фактическими movement triggers и partitions. Перед production rollout требуется применить миграцию на stage и прогнать atomicity/concurrency scenarios на реальной БД.
