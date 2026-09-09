# Продолжение KIZ v1 — 2026-09-07

Ветка feature/kiz, исходный HEAD fb9688c. Изменения оставлены в рабочем дереве без commit.

## Уже было сделано

Предыдущий запуск подготовил весь основной runtime: KIZ schemas/API/service/repository/SQL,
assignment с согласованным inventory MVCC touch, terminal lifecycle и audit,
stock summary/integrity, SQL-миграцию guards, безопасный пересчёт через UPSERT,
HTTP/FBS conflict mapping и bounded concurrency retry. Были подготовлены документация,
локальная PostgreSQL, fixtures и 57 DB acceptance/lock-protocol cases.
История ветки и незавершённый diff изучены; архитектура не перепроектировалась.

## Доделано сейчас

- Обновлены два FBS mock-репозитория, вызывавшие падение существующих retry tests.
- Добавлены 10 regression/concurrency cases, включая ручной retry и восстановление,
  HTTP single/mass retry conflicts, assignment/spend в обоих порядках, terminal/spend,
  stale-snapshot terminal и ожидание assignment при recalculate.
- Дополнен OpenAPI контракт 409 для KIZ и четырёх FBS write/retry endpoints.
- Полный suite проверен на локальной disposable PostgreSQL: **239 passed, 0 skipped**,
  68 Pydantic deprecation warnings. `git diff --check` прошёл.

Изменены в этом продолжении: app/api/v1/endpoints/kiz.py,
app/api/v1/endpoints/fbs_shipments.py, tests/test_kiz_integration.py,
tests/test_fbs_external_mvp.py, tests/test_fbs_http_ingestion.py,
docs/flows/kiz_v1.md и этот отчёт. Остальные файлы списка ниже — работа предыдущего запуска.

## API и миграция

Новые API текущего diff: POST /api/kiz/assign; GET /api/kiz;
GET /api/kiz/stock-summary; GET /api/kiz/{kiz_code};
GET /api/kiz/{kiz_code}/events; POST /api/kiz/{kiz_code}/mark-error;
POST /api/kiz/{kiz_code}/deactivate; GET /api/system/kiz-integrity.
Изменены recalculate-inventory и обработка KIZ/concurrency conflicts существующих writers,
включая FBS create, single/mass shipment retry и item retry. В этом продолжении
новых маршрутов не добавлено; уточнена OpenAPI документация.

Нужна scripts/migrations/20260906_add_kiz_v1.sql, подготовленная предыдущим запуском.
Она успешно применялась тестовой fixture. На рабочей БД не применялась:
согласно согласованному процессу владелец проверяет и применяет её вручную до deployment.

## Остаток и риски

Незавершённых изменений реализации в пределах KIZ v1 по результатам проверки не найдено.
Остаётся эксплуатационный этап: stage/deployment и ручная миграция владельцем.
Новых блокирующих вопросов нет. Сохранены ранее описанные ограничения:
superuser может обходить guards; maintenance использует глобальные table locks;
неатомарность task orchestration и отсутствие FBS retry claim остаются вне scope.
URL codes имеют ограничения при совпадении со статическими маршрутами.
Полный список ограничений — в kiz_v1.md и ../current/known_issues.md.

## Все изменённые и новые файлы текущего рабочего дерева

- `app/api/v1/dependencies.py`
- `app/api/v1/endpoints/fbs_shipments.py`
- `app/api/v1/endpoints/kiz.py`
- `app/api/v1/endpoints/system.py`
- `app/api/v1/router.py`
- `app/core/kiz_errors.py`
- `app/core/schemas/kiz.py`
- `app/core/services/kiz_service.py`
- `app/core/services/system_service.py`
- `app/handlers/write_off_fbs_handler.py`
- `app/infrastructure/database/queries/kiz.py`
- `app/infrastructure/database/queries/system.py`
- `app/infrastructure/database/repositories/fbs_shipment_repository.py`
- `app/infrastructure/database/repositories/kiz_repository.py`
- `app/infrastructure/database/repositories/system_repository.py`
- `app/middleware/error_handler.py`
- `app/retry_worker.py`
- `docs/current/api_map.md`
- `docs/current/business_rules.md`
- `docs/current/current_state.md`
- `docs/current/domain_model.md`
- `docs/current/invariants.md`
- `docs/current/known_issues.md`
- `docs/current/open_questions.md`
- `docs/current/write_operations_policy.md`
- `docs/database/functions.md`
- `docs/database/indexes_constraints.md`
- `docs/database/kiz_v1_runtime_check.md`
- `docs/database/map.md`
- `docs/database/triggers.md`
- `docs/decisions/decisions.md`
- `docs/flows/kiz_v1.md`
- `docs/flows/kiz_v1_completion.md`
- `docs/proposals/kiz_tracking_architecture.md`
- `docs/proposals/kiz_v1_design_review.md`
- `docs/proposals/kiz_v1_lock_protocol_review.md`
- `scripts/migrations/20260906_add_kiz_v1.sql`
- `scripts/migrations/README.md`
- `tests/conftest.py`
- `tests/test_fbs_external_mvp.py`
- `tests/test_fbs_http_ingestion.py`
- `tests/test_kiz_integration.py`
- `tests/test_kiz_lock_protocol.py`
- `tests/test_kiz_unit.py`
- `tests/test_system_integrity.py`
