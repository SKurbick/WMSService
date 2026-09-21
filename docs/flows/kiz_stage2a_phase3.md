# KIZ Stage 2A — Phase 3

Статус:

- Phase 1 — completed;
- Phase 2 — completed;
- Phase 3 — completed в коде/migration;
- Phase 4 — not implemented;
- Phase 5 — not implemented.

## Idempotency contract

Business key: `(source_system, operation_type, external_operation_id)`.
Operation type ограничен transfer/ship. Internal service принимает normalized physical
intent, строит fingerprint и пытается INSERT ON CONFLICT DO NOTHING.

- INSERT вернул row: `new`;
- conflict row имеет тот же fingerprint и result: `replay`;
- conflict row имеет другой fingerprint: domain idempotency conflict для будущего 409.

UNIQUE index является арбитром concurrent requests. Loser ждёт winner на unique key,
после чего берёт committed operation row FOR UPDATE. Lock order будущего flow:
`operation → inventory → KIZ → physical changes → result`. Предполагается READ COMMITTED.

Repository/service используют переданный asyncpg connection и требуют открытую
transaction. Они не открывают pool connection и не делают commit. Status отсутствует:
result_payload NULL допустим только до конца owner transaction. Deferred constraint
запрещает commit без result. Ошибка вызывает rollback intent/items/result и освобождает
business key для корректного retry.

## Fingerprint

SHA-256 вычисляется от compact UTF-8 canonical JSON полного normalized intent, включая
operation_type и все поля items: product, source/destination, Decimal quantity,
explicit KIZ и external line. Ключи объектов сортируются. Decimal `1.00` и `1.0`
становятся `"1"`. Порядок KIZ и items незначим. Duplicate KIZ/external_line_id и float
отклоняются. Unicode сохраняется без ASCII escaping; NUL и surrogate запрещены общим
JSON validator, также используемым KIZ metadata.

`result_payload` — сохранённый response snapshot для replay, не источник inventory.
Operation item хранит только external_line_id и будущий movement_ref. Один movement_ref
может принадлежать максимум одной item.

## Ручное применение

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_kiz_phase3_preflight.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_add_kiz_operations.sql
```

Production SQL и write smoke агентом не выполняются.

## Граница

Phase 3 не добавляет HTTP endpoints, не создаёт movement/KIZ link, не меняет inventory
или KIZ location/status. Transfer и ship будут отдельными Phase 4/5.
