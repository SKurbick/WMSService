# KIZ Stage 2A — Phase 2

Статус реализации на 2026-09-10:

- Phase 1 — completed и применена владельцем БД; production integrity `is_complete=true`;
- Phase 2 — completed и применена владельцем БД;
- Phase 3 — completed в отдельной infrastructure migration;
- Phase 4/5 — not implemented.

## Контракт

`wms.kiz_movement_links(kiz_id,movement_ref)` — immutable many-to-many association.
Composite PK запрещает duplicate pair, два FK RESTRICT защищают identities, reverse
index поддерживает поиск KIZ movement-а. Физические поля остаются только в movements.

KIZ lifecycle принимает shipped. Active сохраняет non-NULL location; shipped имеет
NULL current location; error/deactivated сохраняют прежнюю location-семантику.
`kiz_events.movement_ref` nullable для KIZ v1 events. Для shipped event он обязателен
и composite FK требует link на тот же KIZ. Event location хранит source location.

Read queries карточки и списка используют LEFT JOIN locations. Existing assignment,
mark-error, deactivate, stock-summary и integrity работают по прежнему request contract.
Assignment не создаёт movement/link.

## Ручное применение

Production SQL агентом не выполняется.

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_kiz_phase2_preflight.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_add_kiz_movement_links.sql
```

Preflight проверяет наличие KIZ v1/registry, PK movement_ref и совместимость существующих
KIZ/event rows. Migration берёт fail-fast table locks на KIZ tables и применяется одной
transaction. Backfill links отсутствует: старые assignment не являются movements.

После применения проверить наличие таблицы/constraints/trigger и повторить KIZ read/
write regression на stage. Не вставлять synthetic links или movements в production.

## Граница

Прямой UPDATE location и active→shipped по-прежнему запрещает `guard_kiz_identity`.
Общего bypass-флага нет. Phase 3 добавляет только idempotency infrastructure. Transfer
и ship должны получить отдельный атомарный и авторизованный write path в Phase 4/5.
