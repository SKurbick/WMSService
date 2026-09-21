# KIZ Stage 2A — Phase 1 movement identity

Статус: код и SQL подготовлены; production migration/backfill агентом не выполнялись.
Phase 2 и KIZ association в этом изменении отсутствуют.

## Модель

`wms.movements` остаётся единственным quantitative ledger. Stable identity задаётся
непартиционированной `wms.movement_registry`:

```text
movement_ref -> (movement_id, movement_created_at) -> wms.movements
```

На partitioned parent действует `UNIQUE (movement_id, created_at)`. Registry имеет
настоящий composite FK к этой координате. Mapping запрещено обновлять и удалять.
`AFTER INSERT` trigger на parent создаёт registry row в той же транзакции для Python,
PL/pgSQL и direct supported writers. Ошибка регистрации откатывает movement и его
inventory projection. Trigger-функция использует `SECURITY DEFINER` и фиксированный
`search_path`, поэтому least-privilege writers не получают прямые права на registry.

Публичные movement responses и legacy ссылки kit/re-sorting/FBS не изменены.

## Безопасный deployment

1. Сделать backup, schema-only dump и проверить восстановление.
2. Выполнить read-only preflight:

   ```bash
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
     -f scripts/migrations/20260910_movement_registry_preflight.sql
   ```

   Результат duplicate query должен быть пустым. Проверить поддерживаемую версию и
   наличие актуальных partitions.
3. Остановить или кратко дренировать movement writers. Применить migration:

   ```bash
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
     -f scripts/migrations/20260910_add_movement_registry.sql
   ```

   Migration берёт fail-fast `SHARE ROW EXCLUSIVE` lock с `lock_timeout=10s`.
   PostgreSQL не строит parent partitioned unique index concurrently, поэтому время
   constraint build нужно измерить на stage с production-подобным объёмом.
4. Снова включить writers. Новые rows уже регистрируются trigger-ом.
5. Backfill выполнять небольшими отдельными транзакциями до результата `0`:

   ```sql
   SELECT wms.backfill_movement_registry(10000);
   ```

   Функция использует `FOR UPDATE SKIP LOCKED` и `ON CONFLICT DO NOTHING`, поэтому
   вызов resumable и допускает несколько workers. Размер batch: 1..100000.
6. После результата `0` выполнить:

   ```sql
   SELECT * FROM wms.check_movement_registry_integrity();
   ```

   Требуется `is_complete=true`, равенство `movement_rows=registry_rows` и нули во
   всех четырёх problem counters. Проверку повторить после legacy smoke/regression.

## Backfill properties

Backfill вставляет только отсутствующие registry mappings. Он не создаёт movements,
не меняет inventory, KIZ, legacy links или physical history. Каждая успешная batch
transaction остаётся зафиксированной при сбое следующего вызова.

## Проверенное покрытие writers

PostgreSQL integration tests восстанавливают production DDL snapshot, применяют KIZ v1
и Phase 1 migration, затем проверяют обычный Movement API/adjust, FBS, kit,
re-sorting, task movement writer, container registration/move/unpack и recalculate.
Отдельно проверяются concurrent inserts, concurrent backfill + inserts, duplicate
coordinate rejection, immutable mapping, FK и полный rollback при ошибке registry.

## Rollback

Автоматический destructive down отсутствует. После появления registry rows удаление
registry/FK/identity history не является штатным rollback. Для rollback приложения
достаточно версии, которая игнорирует additive registry objects; trigger продолжает
регистрировать movements. Изменение схемы назад требует отдельного согласованного плана.
