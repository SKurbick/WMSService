# Документация WMSService

Статус: `CURRENT`.

Здесь разделены действующая документация, описание БД, отдельные flows, предложения, архив и служебные инструменты. Для знакомства с сервисом не нужно читать весь каталог.

## Быстрый маршрут

Читайте по порядку:

1. [`current/current_state.md`](current/current_state.md) — что сервис умеет сейчас.
2. [`current/architecture.md`](current/architecture.md) — основные компоненты и границы.
3. [`current/domain_model.md`](current/domain_model.md) — склады, адреса, товары, остатки и движения.
4. [`current/api_map.md`](current/api_map.md) — фактические HTTP endpoints.
5. [`current/business_rules.md`](current/business_rules.md) и [`current/invariants.md`](current/invariants.md) — действующие правила.
6. [`current/write_operations_policy.md`](current/write_operations_policy.md) — требования к изменениям остатков и движениям.
7. [`current/known_issues.md`](current/known_issues.md) и [`current/open_questions.md`](current/open_questions.md) — известные ограничения.
8. [`decisions/decisions.md`](decisions/decisions.md) — история важных решений, только когда нужен контекст.

## Production schema audit

**Production-схема уже получена и проверена 2026-08-08.** Итоги находятся в [`database/production_schema_audit_2026-08-08.md`](database/production_schema_audit_2026-08-08.md).

Исходные read-only артефакты сохранены отдельно:

- [`archive/runtime/2026-08-08-production/runtime_schema_audit.txt`](archive/runtime/2026-08-08-production/runtime_schema_audit.txt) — metadata и безопасные data-quality counts;
- [`archive/runtime/2026-08-08-production/runtime_wms_schema.sql`](archive/runtime/2026-08-08-production/runtime_wms_schema.sql) — полный schema-only dump `wms`;
- [`archive/runtime/2026-08-08-production/runtime_public_schema.sql`](archive/runtime/2026-08-08-production/runtime_public_schema.sql) — schema-only dump пяти используемых таблиц `public`.

Это production. Записывающие и concurrency-тесты на ней запрещены; план для отдельного development/stage окружения находится в [`tools/runtime_concurrency_plan.md`](tools/runtime_concurrency_plan.md).

## Разделы

| Каталог | Что внутри | Нужно читать сразу? |
|---|---|---|
| [`current/`](current/) | Действующее описание сервиса и правил | Да, по быстрому маршруту |
| [`database/`](database/) | Карта БД, functions, triggers, constraints и production audit | Только при работе с БД |
| [`flows/`](flows/) | Детали реализованных FBS и kit flows | Для соответствующей задачи |
| [`decisions/`](decisions/) | Датированные архитектурные решения | Для истории решения |
| [`proposals/`](proposals/) | Будущий дизайн, не текущий контракт | Только для проектирования |
| [`archive/`](archive/) | Старые аудиты, snapshots, завершённые ТЗ и runtime-артефакты | Обычно нет |
| [`tools/`](tools/) | Read-only SQL-аудит и планы проверок | Для DB-аудита |

## База данных

- [`database/map.md`](database/map.md) — таблицы и связи.
- [`database/functions.md`](database/functions.md) — PostgreSQL functions.
- [`database/triggers.md`](database/triggers.md) — triggers.
- [`database/indexes_constraints.md`](database/indexes_constraints.md) — indexes и constraints.
- [`database/production_schema_audit_2026-08-08.md`](database/production_schema_audit_2026-08-08.md) — подтверждённое состояние production на дату аудита.
- [`../scripts/migrations/README.md`](../scripts/migrations/README.md) — порядок применения миграций.

## Статусы документов

- `CURRENT` — сопровождаемое описание текущей реализации.
- `HISTORICAL` — датированный анализ, не текущий контракт.
- `PROPOSAL` — будущий дизайн.
- `DB SNAPSHOT` — историческая выгрузка схемы.
- `UTILITY` — вспомогательный SQL или процедура.

Приоритет источников истины: исполняемый код → runtime PostgreSQL → миграции → документы `CURRENT` → архив и proposals.
