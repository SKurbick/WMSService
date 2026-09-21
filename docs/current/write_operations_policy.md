# Write Operations Policy

Статус: `CURRENT`.

Эта политика обязательна для endpoint'ов и service-layer операций, которые меняют:

- `wms.movements`;
- `wms.inventory`;
- `wms.containers`;
- `wms.container_contents`;
- `wms.tasks`;
- `wms.fbs_shipment_items`.

Источник правил: [`wms_schema.sql`](../archive/snapshots/wms_schema.sql), [`functions.md`](../database/functions.md), [`triggers.md`](../database/triggers.md), [`invariants.md`](invariants.md), [`known_issues.md`](known_issues.md).

## Общие правила

1. `wms.inventory` нельзя менять напрямую из API/service layer, кроме явно выделенного системного пересчета.
2. Нормальный способ менять остатки - insert в `wms.movements`.
3. `wms.inventory` должен обновляться существующим trigger flow: `INSERT INTO wms.movements` -> `trg_update_inventory_from_movement` -> `wms.update_inventory_from_movement()`.
4. Endpoint/service не должен обходить существующие database functions/triggers, если операция уже выражена через них.
5. Любая write-операция с остатками, контейнерами, задачами или FBS retry должна быть транзакционной.
6. Операции `receive`, `ship`, `transfer`, `unpack` должны выполняться в транзакции от начала проверки инвариантов до записи movement/audit и обновления служебных статусов.
7. Read-then-write операции должны использовать row-level lock (`SELECT ... FOR UPDATE`) или advisory lock с документированным lock key.
8. `find_available_location` не резервирует место и не является финальной гарантией capacity. После его результата нужно повторно проверить capacity внутри write-транзакции под lock/reservation.

## Правила по таблицам

### `wms.movements`

- Вставка movement является audit event и источником изменения остатков.
- Новый write endpoint не должен создавать movement с `quantity <= 0`.
- Новый write endpoint не должен создавать movement без направления, где `from_location_id IS NULL` и `to_location_id IS NULL`.
- Для расхода (`ship`, исходящая часть `transfer`, исходящая часть `unpack`) нужно проверять доступный остаток в той же транзакции, где пишется movement.
- Для операций с контейнером нужно сохранять согласованность `container_code`, `batch_number`, `product_id`, location и quantity.

### `wms.inventory`

- Прямые `INSERT/UPDATE/DELETE` из API/service layer запрещены.
- Исключение: системный пересчет inventory из `wms.movements`, если он явно оформлен как maintenance operation, выполняется транзакционно и документирован.
- Сервисный код не должен пытаться вручную синхронизировать inventory после movement: это делает trigger.

### `wms.containers`

- `location_id` контейнера меняет только controlled B3 move; legacy projection trigger удаляется B4 migration.
- Операции с контейнером должны проверять статус контейнера до изменения.
- Для контейнерных read-then-write сценариев нужно блокировать строку контейнера и, при необходимости, active rows `container_contents`.
- Нельзя добавлять сценарии, которые меняют location/status контейнера и одновременно обходят movement/audit flow.

### `wms.container_contents`

- Active content создаётся только controlled fill; compatibility trigger подавляет legacy receive для авторизованной fill line, а B4 movement guard блокирует произвольный container receive.
- Изменение quantity/status active content должно быть согласовано с inventory через movement.
- Перед распаковкой или изменением content нужно блокировать relevant content rows.
- Full unpack выполняется только controlled `unpack-all`; legacy DB function удаляется B4 migration.

### `wms.tasks`

- Изменение статуса task должно быть транзакционно согласовано с изменением `task_items`, notifications и movements, если операция создает движение товара.
- Complete/approve/recount операции не должны создавать movements до проверки всех business invariants.
- Task row нужно блокировать при assign/start/complete/approve, если операция зависит от текущего статуса или исполнителя.
- Новый task write endpoint должен обновлять `docs/current/api_map.md` и `docs/current/business_rules.md`.

### `wms.fbs_shipment_items`

- Обновление FBS item status/retry должно быть транзакционно согласовано с созданием movement и внешними отметками, если они выполняются в той же операции.
- Retry worker должен избегать параллельной обработки одной позиции; рекомендуемый подход - row lock с `FOR UPDATE SKIP LOCKED` или advisory lock.
- Если `movement_id` заполняется, сервис должен гарантировать существование соответствующего movement, пока FK в DDL отсутствует.

## Требования к новым write endpoint'ам

Любой новый endpoint/service method, который пишет в перечисленные таблицы, должен:

- проверять бизнес-инварианты до записи;
- использовать транзакцию;
- писать audit/movement, если операция меняет остатки или физическое состояние товара;
- не обходить существующие triggers/functions;
- учитывать конкурентный доступ через row lock или advisory lock для read-then-write;
- иметь тесты на успешный сценарий и ключевые отказные сценарии;
- обновлять `docs/current/api_map.md`;
- обновлять `docs/current/business_rules.md`;
- при новых DB rules/constraints добавлять миграцию и обновлять `docs/current/invariants.md`.

## Операции, которые пока запрещено добавлять без решения known issues

Запрещено добавлять или расширять следующие write-сценарии без предварительного решения соответствующих пунктов в `known_issues.md`:

- Создание произвольных `wms.movements` без DB/service проверки `quantity > 0`.
- Создание movements без `from_location_id` и `to_location_id`.
- Write endpoint, который полагается на `find_available_location` как финальную гарантию свободного места.
- Перенос location subtree через изменение `parent_location_id`, пока не решено каскадное обновление `path` потомков или запрет такой операции.
- FBS endpoint/worker, который заполняет `fbs_shipment_items.movement_id` без гарантии существования movement.
- Операции с `container_code`, которые создают ссылки на несуществующий `containers.qr_code`.
- Параллельные retry/complete/unpack/ship сценарии без row lock или advisory lock.
- Прямые изменения `wms.inventory` из API/service layer, кроме системного пересчета.

## Согласованное исключение KIZ v1

Разрешён узкий служебный UPDATE уже заблокированной wms.inventory строки во время
assignment KIZ, исключительно для создания MVCC-версии. Запрос обновляет updated_at,
не меняет quantity/product/location/status/batch/container и не создаёт movement.
Порядок: SELECT inventory FOR UPDATE → touch → COUNT(active) → KIZ INSERT → assigned event.
Все шаги на одной connection в одной transaction; любой отказ откатывает touch и audit.
Это исключение явно согласовано пользователем после проверки REPEATABLE READ гонки.

KIZ terminal: сначала scope lookup без conflicting lock, затем inventory → KIZ FOR UPDATE
→ lifecycle update → event. Другие direct inventory writes из service по-прежнему
запрещены, кроме maintenance recalculate.

Recalculate работает в READ COMMITTED transaction: LOCK movements SHARE, затем
inventory EXCLUSIVE (включая блокирование assignment SELECT FOR UPDATE); проверка
calculated quantities против active KIZ; UPSERT positive rows; DELETE obsolete;
финальная KIZ integrity validation и commit. Смысл ledger SUM и порог 0.0001 сохранены.
Locks распространяются на весь ledger/projection даже при product_id filter; это
maintenance operation. Встречный legacy lock order может дать deadlock: полный rollback,
HTTP 409, повтор всей операции. Guards никогда не отключаются.

Raw KIZ SQL writes не являются альтернативой сервису. Перенос операции в другой writer
требует того же inventory lock/touch/count/atomic audit протокола.

## Movement identity Phase 1

Любой supported writer создаёт movement обычным `INSERT INTO wms.movements`; отдельный
application вызов registry запрещён и не требуется. `AFTER INSERT` trigger добавляет
immutable mapping в той же transaction, поэтому ошибка registry обязана откатить весь
physical write. Legacy kit/re-sorting/FBS/task links сохраняют текущий контракт.
Исторические mappings добавляет только resumable DB backfill; он не меняет ledger или
inventory. Coverage подтверждается `wms.check_movement_registry_integrity()`.

## KIZ association Phase 2

Публичного или общего CRUD writer для `kiz_movement_links` нет. INSERT предназначен
для будущих поддерживаемых physical operations внутри их transaction; UPDATE/DELETE
запрещены DB trigger. Старый assignment не создаёт movement или link.

Прямые location/lifecycle updates запрещены. Transfer и shipment используют отдельные
узкие SECURITY DEFINER functions, transaction-local authorization и deferred
completeness validation; общий bypass guard отсутствует.

## KIZ operation idempotency Phase 3

Internal flow вызывается только внутри уже открытой asyncpg transaction:
`operation acquire → inventory locks → KIZ locks → future physical changes → result`.
Repository не получает connection из pool и не выполняет commit.

Acquire использует INSERT ON CONFLICT DO NOTHING под UNIQUE business key. Конкурентный
loser ждёт winner, затем берёт operation row FOR UPDATE и возвращает replay либо
fingerprint conflict. Для этого write path используется READ COMMITTED; 40001/40P01
остаются отдельными concurrency conflicts. Advisory lock не используется.

## KIZ operation transfer

Порядок: operation identity, canonical locations/inventory scopes, canonical KIZ rows,
physical writes, result. Все items выполняются в одной transaction. 40001/40P01 и любой
domain failure откатывают operation, KIZ, movement, registry, links и inventory вместе.

## KIZ operation shipment

Порядок: idempotency operation, canonical locations, source inventory scopes, KIZ rows,
revalidation, controlled shipped transition, outgoing movement, registry/ref, item
attachment, links, shipped events, final integrity, result. Все items принадлежат одной
transaction. Legacy movement/FBS writers не создают KIZ operations и не выбирают KIZ.

## Container Stage 3B write boundary

Application code не должен напрямую менять `containers.location_id` или
`parent_container_id`, удалять container, переименовывать QR либо выполнять произвольный
UPDATE/DELETE quantity в `container_contents`. Исключение — B2.2 extract через
`wms.apply_container_extract_content(operation_item_id)` внутри caller-owned transaction
после registry-backed movements. Новые bypass writers запрещены.

Future runtime: отдельная non-owner role, без direct table DML; EXECUTE только конкретных
controlled functions, PUBLIC EXECUTE отозван, SECURITY DEFINER имеет fixed search_path.
Production superuser role в B1 не меняется и остаётся operational risk.

## Container B2.1 controlled fill

Новый application write выполняется только через `POST /api/container-operations/fill`
и одну caller-owned DB transaction. Direct creation of `container_operation` provenance,
ручная установка `wms.container_fill_item_id` и отдельные inventory/contents writes не
являются supported protocol. Transaction-local authorization существует только для
подавления legacy receive-trigger у подтверждённой незавершённой fill line.

## Container B2.2 controlled extract

`POST /api/container-operations/extract` использует одну caller-owned transaction:
idempotency operation → container lock → product validation/items → all inventory scope
locks в canonical order → all active content locks в том же scope order → validation →
contained outgoing/loose incoming movements → movement refs → controlled contents
mutation → whole-container status → final invariants → saved result.

`wms.apply_container_extract_content` принимает только незавершённый extract item с
двумя attached refs. До UPDATE/DELETE функция требует, чтобы разница old contents и уже
спроецированного contained inventory была ровно item quantity; это делает mutation
one-shot. Full extract удаляет current active row. B4 закрывает generic container movement bypass.

## Container B3 controlled writes

Move/unpack-all выполняются одной caller-owned transaction после idempotency resolution.
Общий lock boundary — container row; далее contents/inventory scopes берутся в canonical
order. B4 migration удаляет legacy location trigger; controlled move остаётся единственным
поддерживаемым location writer и создаёт ровно один movement на contained scope.

## Container B4 final write boundary

- `POST /api/movements` поддерживает только loose inventory и обязан отклонять весь batch при
  любом non-NULL `container_code`.
- Единственные supported container quantity/location writers: fill, extract, move, unpack-all.
- Register создаёт только empty container; legacy move/unpack запрещены.
- Container-coded movement обязан иметь полный `container_operation` provenance; B4 DB trigger
  является дополнительной защитой, а не заменой service transaction/completeness guards.
- Maintenance recalculate сначала и после rewrite проверяет ledger/contents/inventory/location;
  mismatch приводит к rollback без автоматического repair.
- Runtime superuser migration описана в B4 runbook и остаётся отдельной infrastructure задачей.
