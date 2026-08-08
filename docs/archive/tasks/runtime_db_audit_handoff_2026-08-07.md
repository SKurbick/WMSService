# Handoff: финальный runtime-аудит документации WMSService

Дата подготовки: 2026-08-07.

## Текущий статус

Простой и средний этапы `DOCUMENTATION_ACTUALIZATION_TZ.md` завершены. Остаётся
финальный этап, требующий доступа к runtime PostgreSQL.

Подготовлены:

- `docs/tools/runtime_db_audit.md` — подробная инструкция;
- `docs/tools/runtime_schema_audit.sql` — read-only SQL-аудит metadata и безопасных
  data-quality counts.

SQL не содержит DDL/DML и выполняется в `REPEATABLE READ READ ONLY` transaction.

## Что выполнить позже

Выбрать окружение (`development`, `stage` или `production`) и выполнить из корня
репозитория:

```bash
git rev-parse HEAD

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f docs/tools/runtime_schema_audit.sql \
  > runtime_schema_audit.txt

pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges \
  --schema=wms --schema=public \
  > runtime_schema.sql
```

## Что передать для продолжения

- имя окружения;
- commit из `git rev-parse HEAD`;
- дату и часовой пояс выполнения;
- `runtime_schema_audit.txt`;
- `runtime_schema.sql`.

Не передавать пароль, `DATABASE_URL` и иные секреты подключения. Schema-only dump
следует предварительно просмотреть на предмет чувствительных имён ролей или объектов.

## Что сделать после получения результатов

1. Сравнить runtime schema с `docs/archive/snapshots/wms_schema.sql` и всеми миграциями.
2. Подтвердить `ltree`, constraints и их validation status, functions, triggers,
   indexes, views и партиции `wms.movements`.
3. Подтвердить поддержку `http_api` в `chk_fbs_shipments_source`.
4. Актуализировать DB-документы, `invariants.md`, `known_issues.md`, `risk_map.md` и
   `open_questions.md`.
5. Отдельно согласовать безопасные concurrency/atomicity тесты только для
   development/stage.
6. Обновить и закрыть оставшиеся пункты `DOCUMENTATION_ACTUALIZATION_TZ.md`.

Последняя локальная проверка перед паузой:

```text
9 passed
git diff --check: успешно
```

Бизнес-логика, endpoint'ы и runtime БД в рамках актуализации документации не менялись.
