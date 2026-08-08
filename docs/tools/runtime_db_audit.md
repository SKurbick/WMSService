# Runtime PostgreSQL Audit

Статус: `CURRENT`.

Документ описывает передачу результатов runtime-аудита для актуализации документации.
Скрипт `runtime_schema_audit.sql` выполняет только read-only запросы к PostgreSQL system
catalogs и безопасные агрегаты без выборки payload, ФИО или товарных данных.

## Что требуется от владельца БД

1. Выбрать одно окружение и явно указать его: `development`, `stage` или `production`.
2. Зафиксировать commit приложения, с которым сравнивается схема:
   `git rev-parse HEAD`.
3. Выполнить audit через `psql` и сохранить полный stdout:

   ```bash
   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
     -f docs/tools/runtime_schema_audit.sql \
     > runtime_schema_audit.txt
   ```

4. Снять два schema-only dump: полный `wms` и только используемые WMS таблицы `public`. Перед передачей проверить файлы на чувствительные имена объектов.

   ```bash
   pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges \
     --schema=wms > runtime_wms_schema.sql

   pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges \
     --table=public.products --table=public.users \
     --table=public.user_permissions --table=public.assembly_task \
     --table=public.supply_to_sellers_warehouse \
     > runtime_public_schema.sql
   ```

5. Передать:

   - имя окружения;
   - commit приложения;
   - дату и часовой пояс выполнения;
   - `runtime_schema_audit.txt`;
   - `runtime_wms_schema.sql`;
   - `runtime_public_schema.sql`.

Пароль, `DATABASE_URL`, hostname при необходимости и содержимое прикладных таблиц
передавать не нужно. Скрипт возвращает только metadata и counts.

## Ограничения

- Audit не изменяет БД и не подтверждает транзакционность под конкурирующей нагрузкой.
- Concurrency/atomicity сценарии выполняются отдельно только на development/stage после
  согласования тестовых SKU, locations и containers.
- Schema-only dump подтверждает состояние только указанного окружения и времени.
