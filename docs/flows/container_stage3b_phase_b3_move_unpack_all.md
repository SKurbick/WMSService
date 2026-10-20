# Container Stage 3B Phase B3 — controlled move и unpack-all

Статус: реализовано в коде 2026-09-17. Production migration и business operations
агентом не выполнялись. B4/B5/Stage 3C не входят в эту фазу.

## API

- `POST /api/container-operations/move` — перемещает flat container между active
  locations одного warehouse. Разрешены `empty/open/sealed`, `blocked` возвращает 409.
- `POST /api/container-operations/unpack-all` — переносит все active contents открытого
  flat container в loose stock его direct location. `empty/sealed/blocked` возвращают 409.
- New и exact replay возвращают 201 и один сохранённый response. Business key:
  `(source_system, operation_type, external_operation_id)`.

Move request содержит `source_system`, `external_operation_id`, `author`, `container_id`,
`to_location_code`. Fingerprint не включает current source location. Unpack-all request
не содержит items: server фиксирует их snapshot после row locks; fingerprint включает
только operation type и container identity.

## Move protocol

Warehouse вычисляется как root ancestor `wms.locations.path` с
`parent_location_id IS NULL`. Cross-warehouse move отклоняется. Same-location move —
успешный replayable no-op. Empty move меняет только container location и сохраняет
operation с `items=[]`; fake product/zero movement не создаются.

Для каждого active product/NULL-safe batch scope непустого контейнера создаётся один
`transfer` movement со старой location в новую и тем же `container_code`. Movement
получает global `movement_ref` и provenance `container_operation / operation_id /
operation_item_id`. `container_contents` не изменяется, status сохраняется, inventory
переносится movement projection. До commit проверяется точное равенство contents и
contained inventory на новой location и отсутствие container inventory на старой.

Legacy `PUT /api/containers/{container_id}/location` сохранён. Transaction-local GUC
`wms.container_move_operation_id` разрешает legacy trigger пропустить projection только
для текущей unfinished B3 move operation; обычный legacy caller продолжает прежний path.

## Unpack-all protocol

Для каждого locked active content scope создаётся immutable operation item. Используется
тот же protocol, что B2.2: contained outgoing плюс loose incoming transfer, два registry
refs, затем `wms.apply_container_extract_content(operation_item_id)`. Функция допускает
`extract` и `unpack_all`. После удаления всех current content rows container становится
`empty`; physical total каждого product/batch неизменен.

## Transaction и locks

Canonical order обеих операций:

```text
idempotency operation
→ container row
→ source/destination location rows по location_id (move)
→ active contents по product/batch/content_id
→ operation items
→ inventory scopes по product/batch и location_id
→ revalidation
→ movements / registry refs / links
→ controlled container location или contents/status mutation
→ final invariants
→ saved result
→ commit
```

Container row сериализует fill, extract, move, unpack-all и DB-backed legacy writes одного
container. Scope rows берутся только в deterministic order. Операции разных containers
не используют global lock. Любая exception откатывает operation graph, movements,
registry, inventory, contents и location/status.

## Migration runbook

1. Остановить container writers или выбрать maintenance window.
2. Выполнить read-only
   `scripts/migrations/20260916_container_b3_move_unpack_all_preflight.sql`.
3. Вручную применить
   `scripts/migrations/20260916_add_container_b3_move_unpack_all.sql`.
4. Выполнить B3 API/PostgreSQL smoke и B2.1/B2.2 regressions.

Migration добавляет structural `container_operations.container_id`, single
`container_operation_items.movement_ref`, новые operation types, guards/completeness и
controlled legacy-trigger branch. Для metadata backfill старый B2 immutable guard
снимается и новый guard восстанавливается внутри той же transaction после table lock.
Она не создаёт business movements, не меняет inventory quantities и не перемещает containers.

## Ограничения

Generic `/api/movements` bypass не закрывается до B4. Legacy unpack сохраняет известные
ограничения и не является новым unpack-all. B5 history, nesting и KIZ containers не
реализуются этой фазой.
