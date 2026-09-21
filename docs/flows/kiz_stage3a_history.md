# KIZ Stage 3A — история конкретного КИЗ

Статус: реализовано в коде как read-only read model. Новая migration не требуется.

## Endpoint

`GET /api/kiz-history?kiz_code=<KIZ>` возвращает существующую `KizState` в поле
`current_state` и непагинированную хронологическую `timeline`. Query-параметр выбран,
чтобы не пересекаться с существующим catch-all route `/api/kiz/{kiz_code:path}`.
Неизвестный регистрозависимый код возвращает существующий KIZ 404 contract.

## Источники

- current state: `wms.kiz` с `LEFT JOIN wms.locations`, поэтому shipped/NULL location
  не теряется;
- identity/lifecycle: `wms.kiz_events`;
- physical history: только
  `wms.kiz_movement_links → wms.movement_registry → wms.movements` с точной composite
  coordinate `(movement_id, movement_created_at)`;
- location snapshots physical operation: стороны `wms.movements`, а не текущий
  `wms.kiz.location_id`.

Запрос не использует `reason`, metadata или текстовые эвристики для поиска movement.
Warehouse assignment остаётся только событием `assigned` с `movement_ref=null`;
artificial receive не создаётся и исторический receive не подбирается.

## Merge и ordering

Linked ship movement и `shipped` event того же KIZ с тем же `movement_ref` становятся
одной timeline entry `event_type=ship`. Entry использует время physical movement,
from/to/quantity из movement и сохраняет `kiz_event_id`, lifecycle statuses, author и
reason события. Transfer приходит только из linked movement.

Ordering: `occurred_at`, затем фиксированный source rank (lifecycle перед physical),
затем stable source identity (`kiz_event_id` или `movement_ref`). Случайный SQL-порядок
не используется.

`quantity` — полное количество physical movement. Оно не означает количество данного
КИЗ: каждый link обозначает одну identified unit внутри движения. Поэтому два КИЗ,
связанные с movement quantity 5, получают одинаковые `movement_ref` и `quantity=5`.

## Query и integrity

Карточка и timeline читаются двумя запросами в одной `READ ONLY REPEATABLE READ`
transaction. N+1, pagination, materialized view и отдельный history ledger отсутствуют.
LEFT JOIN физической цепочки позволяет обнаружить невозможный missing registry/movement;
сервис завершает GET ошибкой и не пытается исправлять данные. Несколько shipped events
для одной связи и shipped event на non-ship movement также считаются integrity anomaly.

Путь поддержан существующими индексами/constraints: unique lookup `kiz_code`, PK
`kiz_movement_links(kiz_id,movement_ref)`, PK registry, composite movement FK/unique и
`idx_kiz_events_history(kiz_id,occurred_at,kiz_event_id)`. DB migration и новые индексы
не требуются.

## Граница

Stage 3A не добавляет writes и не меняет assignment, transfer, ship, KIZ v1, movements,
FBS, tasks, kit, re-sorting, containers или inventory. Production SQL не выполняется.
