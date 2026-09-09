# KIZ v1: read-only runtime check

Проверено 2026-09-06 через конфигурацию приложения, соединение принудительно read-only.
Никаких DDL/DML в рабочей БД не выполнялось.

| Проверка | Результат |
|---|---|
| PostgreSQL | 17.4 |
| default_transaction_isolation | read committed |
| current_user | vector_admin |
| rolsuper / rolbypassrls | true / true |
| membership владельца inventory | true |
| inventory UPDATE / DELETE / TRUNCATE | разрешены |
| SET session_replication_role | разрешён |
| user triggers inventory | trg_inventory_updated_at |
| wms.kiz / wms.kiz_events / guard_kiz_inventory | отсутствуют до применения миграции |

Тела update_inventory_from_movement и update_inventory_timestamp совпадают с сохранённым
production snapshot 2026-08-08. Inventory PK/FK/status/nonnegative/UNIQUE NULLS NOT DISTINCT
по product/location/status/batch/container подтверждены live; конфликтов новых trigger names
на inventory нет. Эта проверка не является полным новым schema dump всего проекта.

## Риск и минимальное ужесточение

Сейчас runtime-role может отключить пользовательские triggers, менять session_replication_role,
TRUNCATE и вставлять KIZ вне сервисного протокола. Guards защищают нормальные writers,
но не намеренные действия superuser или произвольный direct KIZ INSERT.

Рекомендуется отдельный application login NOSUPERUSER NOBYPASSRLS без membership в
owner/migration roles. Владение схемой/таблицами/functions оставить deployment role.
Не предоставлять runtime TRUNCATE, DDL или SET session_replication_role. Выдать только
нужные SELECT/INSERT/UPDATE, sequence и schema privileges; DELETE inventory оставить
только там, где оно необходимо существующему projection/maintenance. Для KIZ audit
нужны SELECT/INSERT, UPDATE/DELETE не нужны; lifecycle UPDATE KIZ можно ограничить
status/closed_at/updated_at columns. Доступ людей к runtime credentials ограничить:
INSERT privilege не enforce-ит сервисный assignment протокол.

Отдельно проверить privileges для существующих trigger functions и LOCK TABLE maintenance;
по возможности вынести maintenance в отдельную роль. Полное разделение ролей и запрет
произвольных KIZ inserts через DB-only write gateway — отдельная эксплуатационная задача.
Роли автоматически не менялись. Владелец применяет SQL после своей проверки.
