# Production Schema Audit — 2026-08-08

Статус: `CURRENT`. Read-only аудит выполнен 2026-08-08 10:18 UTC (13:18 Europe/Moscow) для БД `vector_db`, PostgreSQL 17.4, commit `c97a5acb0ebbd9142c89c7ecfab2ab50b7c70171`. Окружение: `production` (подтверждено владельцем 2026-08-08); рабочая директория содержала незакоммиченные изменения.

Исходные артефакты: [`runtime_schema_audit.txt`](../archive/runtime/2026-08-08-production/runtime_schema_audit.txt), [`runtime_wms_schema.sql`](../archive/runtime/2026-08-08-production/runtime_wms_schema.sql) — полная схема `wms`, и [`runtime_public_schema.sql`](../archive/runtime/2026-08-08-production/runtime_public_schema.sql) — пять используемых таблиц `public`. Аудит выполнялся в `REPEATABLE READ READ ONLY`; concurrency-сценарии не запускались.

## Подтверждено

- `ltree` 1.3 установлена в `wms`; существуют `wms.ltree`, `wms.lquery`, `wms.ltxtquery`.
- Присутствуют объекты мягких резервов, kit operations и re-sorting.
- Валидированный `chk_fbs_shipments_source` разрешает `standard`, `external_detected`, `http_api`.
- Существуют partitions `movements_2026_01` … `movements_2026_12`, inventory triggers и актуальные movement types.
- Audit зафиксировал 97 функций, 26 пользовательских triggers и 6 views/materialized views.
- Нет отрицательного inventory, movements без стороны, orphan container/FBS references и duplicate movement IDs.

## Осталось

- Оба movement checks остаются `NOT VALID`; найдена одна legacy movement в `wms.movements_2026_03` с неположительным quantity (не `NULL`), timestamp `2026-03-12 14:38:39.640816+00`.
- Происхождение старого `wms_schema.sql` не установлено; runtime-аудит относится к production.
- Atomicity/concurrency тесты требуют отдельного согласования и development/stage.
