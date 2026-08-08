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

- Изменение `location_id` контейнера запускает trigger `trg_move_container_inventory`; endpoint должен учитывать, что это создаст transfer movements.
- Операции с контейнером должны проверять статус контейнера до изменения.
- Для контейнерных read-then-write сценариев нужно блокировать строку контейнера и, при необходимости, active rows `container_contents`.
- Нельзя добавлять сценарии, которые меняют location/status контейнера и одновременно обходят movement/audit flow.

### `wms.container_contents`

- Добавление active content запускает trigger `trg_sync_container_contents_to_inventory`; сервис не должен дублировать receive movement вручную для той же вставки.
- Изменение quantity/status active content должно быть согласовано с inventory через movement.
- Перед распаковкой или изменением content нужно блокировать relevant content rows.
- Нельзя добавлять новый сценарий полной распаковки, пока не решен конфликт `unpack_from_container` с constraints `quantity > 0` и допустимыми status.

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

- Полная распаковка контейнера через `wms.unpack_from_container`, пока не решен конфликт `container_contents.quantity = 0/status = empty` с constraints.
- Создание произвольных `wms.movements` без DB/service проверки `quantity > 0`.
- Создание movements без `from_location_id` и `to_location_id`.
- Write endpoint, который полагается на `find_available_location` как финальную гарантию свободного места.
- Перенос location subtree через изменение `parent_location_id`, пока не решено каскадное обновление `path` потомков или запрет такой операции.
- FBS endpoint/worker, который заполняет `fbs_shipment_items.movement_id` без гарантии существования movement.
- Операции с `container_code`, которые создают ссылки на несуществующий `containers.qr_code`.
- Параллельные retry/complete/unpack/ship сценарии без row lock или advisory lock.
- Прямые изменения `wms.inventory` из API/service layer, кроме системного пересчета.
