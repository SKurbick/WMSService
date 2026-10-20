# Container Stage 3B — Phase B1

Статус: `COMPLETED`; по входным данным B2.1 migration применена владельцем БД, не агентом.

## Контракт

- `container_id` — стабильная внутренняя identity для будущих FK/domain links.
- `qr_code` — неизменяемая внешняя identity. UNIQUE сохраняется, rename и hard delete
  блокируются DB trigger, поэтому supported protocol не допускает reuse QR другой identity.
- Модель плоская: `parent_container_id IS NULL` enforced CHECK, колонка сохранена для
  будущей отдельной фазы nesting.
- Каждый контейнер имеет прямой `location_id NOT NULL`. Отдельный `warehouse_id` не
  добавлен: склад выводится по корневому ancestor дерева `wms.locations.path`.
- Будущий controlled move обязан проверить равенство warehouse-root source/destination;
  B1 старый move не перерабатывает и cross-warehouse protocol не добавляет.

Разрешённые типы: `pallet`, `box`, `cage`, `trolley`.

Разрешённые статусы:

- `empty` — active contents отсутствуют;
- `open` — контейнер может содержать товар и допускает изменение состава будущим protocol;
- `sealed` — контейнер может содержать товар, изменение состава требует controlled reopen;
- `blocked` — physical operations запрещены domain protocol.

Legacy `opened` однократно нормализуется в `open`; `in_transit` и `unit` удалены из
allow-list как неиспользуемые значения без реализованной semantics. Preflight останавливает
migration при иных несовместимых значениях.

## Состав и количественная модель

`container_contents.quantity > 0` сохраняется. UNIQUE scope теперь использует
`NULLS NOT DISTINCT (container_id, product_id, batch_number, status)`, поэтому одинаковые
scope с NULL batch больше не дублируются. Два представления — `container_contents` и
`inventory.container_code` — пока сохраняются как transition model и должны изменяться
единым controlled write protocol в последующих фазах.

DB guards обеспечивают только безопасную часть cross-table invariant: `empty` нельзя
назначить контейнеру с active contents, active content нельзя добавить в `empty`.
`open/sealed/blocked` могут иметь contents. Guards не подменяют будущую транзакционную
fill/extract state machine.

## Совместимость legacy API

Существующие URLs и response shapes не меняются. `POST /api/containers/register`
остаётся receipt-like операцией: создаёт новый stock через receive movements. Пустой
registration возвращает `empty`, непустой — `sealed`. Это не fill loose stock.

Старые move/unpack не переписаны. Для совместимости `unpack_from_container` использует
каноническое `open`, но full extraction по-прежнему упирается в positive-row constraint.

## Security boundary

Текущий production runtime role, наблюдавшийся аудитом, является superuser; B1 его не
меняет. Для следующих controlled functions нужна отдельная non-owner application role:
REVOKE direct DML на container tables, REVOKE PUBLIC EXECUTE у critical functions,
GRANT только SELECT и EXECUTE конкретных functions. SECURITY DEFINER functions должны
иметь fixed `search_path` и проверенные ownership/grants.

## Ручное применение

1. Остановить container writers или обеспечить maintenance window.
2. Выполнить read-only `scripts/migrations/20260915_container_b1_preflight.sql`.
3. Если все проверки прошли, вручную применить
   `scripts/migrations/20260915_add_container_b1_contract.sql`.
4. Проверить constraints/triggers и legacy container smoke tests до возврата трафика.

Migration использует transaction и `lock_timeout=10s`; она не меняет inventory/content
quantity, не создаёт movements и не выполняет business operations.

## Граница фазы

B1 реализован. B2.1 fill теперь реализован отдельным protocol; B2.2 extract, B3
move/unpack normalization, B4 bypass closure, B5 history redesign и Stage 3C KIZ
containers не реализованы. См. [B2.1](container_stage3b_phase_b21_fill.md).

