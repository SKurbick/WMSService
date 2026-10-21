# КИЗ: буквальные первые 24 символа — 21.10.2026

Ветка: `feature/kiz-fbs-integration`. Изменения не закоммичены.

## Реализовано

- Общий нормализатор `app/core/kiz_code.py`: Unicode code points, буквально `code[:24]`;
  короткие строки сохраняются целиком, без trim/case/Unicode normalization.
- Импорт из очереди нормализует mark_codes до проверки дублей и записи identity.
  B1 raw ingestion сохраняет исходное сообщение, как и раньше.
- FBS kiz_bindings нормализуются до проверок уникальности/конфликтов и записи bindings.
- Ручные assignment, activation, transfer/ship, container fill/extract и поиск карточки,
  событий, terminal transition и истории используют тот же код.
- Нормализация выполняется до idempotency fingerprint. Для старых операций сравнивается
  исходный fingerprint с полными кодами из сохранённого результата; все остальные поля
  intent также проверяются. Возвращаемые КИЗ нормализованы, исторические записи не меняются.
- Обновлены русские описания схем/параметров Swagger, бизнес-правила и решения.
- NUL и некорректный Unicode внутри первых 24 символов отклоняются: PostgreSQL не может
  сохранить их буквально. Остальные управляющие символы не удаляются.
- Существующие ограничения assignment для кодов, пересекающихся с URL-маршрутами,
  не отменяются; произвольные коды для истории передаются query-параметром.

## База данных и rollout

Добавлены read-only preflight и атомарная миграция:
- `scripts/migrations/20261021_kiz_code_prefix_preflight.sql`;
- `scripts/migrations/20261021_normalize_kiz_code_prefix.sql`.

Миграция сокращает существующие `wms.kiz.kiz_code` и
`wms.fbs_kiz_bindings.kiz_code`, сохраняя IDs, holders, lifecycle и physical stock.
Она проверяет коллизии внутри обеих таблиц и противоречия между binding и identity.
При конфликте — STOP и rollback, без объединения записей. Identity guards отключаются
только внутри транзакции под ACCESS EXCLUSIVE locks и восстанавливаются в ней же.
Новые CHECK допускают 1–24 символа без запрета крайних пробелов.

Production миграция НЕ применялась. Перед применением остановить writers/retry workers,
сделать backup, выполнить preflight, миграцию и развернуть новый код перед возобновлением
writers. Старый код не должен продолжать писать длинные значения. Подробности —
`scripts/migrations/README.md`. Автоматического восстановления исходных хвостов нет.

## Проверки

- Полный regression на изолированном локальном PostgreSQL 18: **729 passed, 1 failed**.
- Единственный failure:
  `test_concurrent_ship_and_assignment_preserve_touch_protocol` — известный ранее
  deadlock assignment против explicit ship. Он уже зафиксирован в
  `docs/audit/kiz_merge_result_20261020.md` и `docs/current/open_questions.md`;
  повторный целевой прогон снова его воспроизвёл. Эта доработка его не исправляет.
- Целевой прогон: **115 passed, 1 failed** (тот же конкурентный сценарий).
- Новые проверки: длины 1/23/24/25/32/94/96/300; кириллица/emoji; буквальные пробелы,
  GS/переводы строк; дубли префиксов; legacy fingerprints и конфликты изменённого intent;
  реальный old activation replay после миграции; import → activation → FBS → replay;
  migration collision rollback; восстановление DB guards после миграции.
- Обычная KIZ fixture использует новую схему; migration-тесты явно выбирают старую.
- `git diff --check` проходит. Production БД и RabbitMQ не использовались.
- Новых endpoints нет; изменена семантика входных КИЗ в существующих endpoints.
- Бизнес-вопросов для уточнения не осталось; production preflight/smoke ещё не выполнялся.

## Изменённые файлы

- `app/api/v1/endpoints/kiz.py`
- `app/api/v1/endpoints/kiz_history.py`
- `app/api/v1/openapi_kiz.py`
- `app/api/v1/openapi_kiz_import.py`
- `app/core/container_operation_idempotency.py`
- `app/core/kiz_operation_idempotency.py`
- `app/core/schemas/container_operations.py`
- `app/core/schemas/kiz.py`
- `app/core/schemas/kiz_activation.py`
- `app/core/schemas/kiz_operations.py`
- `app/core/schemas/write_off_fbs.py`
- `app/core/services/container_operation_idempotency_service.py`
- `app/core/services/kiz_activation_service.py`
- `app/core/services/kiz_history_service.py`
- `app/core/services/kiz_import_service.py`
- `app/core/services/kiz_operation_idempotency_service.py`
- `app/core/services/kiz_service.py`
- `docs/current/business_rules.md`
- `docs/current/invariants.md`
- `docs/current/kiz_api.md`
- `docs/decisions/decisions.md`
- `scripts/migrations/README.md`
- `tests/conftest.py`
- `tests/test_fbs_kiz_stage1.py`
- `tests/test_kiz_integration.py`
- `tests/test_kiz_merged_flow.py`
- `tests/test_kiz_unit.py`
- `app/core/kiz_code.py`
- `scripts/migrations/20261021_kiz_code_prefix_preflight.sql`
- `scripts/migrations/20261021_normalize_kiz_code_prefix.sql`
- `tests/test_kiz_code_prefix.py`
