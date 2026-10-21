# Сводка КИЗ по организациям — 21.10.2026

Ветка: `feature/kiz-fbs-integration`.

## Реализация

- Добавлен `GET /api/kiz-reports/summary`; контракт существующего `/api/kiz` не меняется.
- Все параметры необязательны; group_by по умолчанию organization,product_id.
- Семь допустимых измерений: organization, product_id, lifecycle_status, location_code,
  container_id, container_qr_code, origin_type. Поддерживаются CSV и повторные параметры.
- Фильтры: organization, organization_missing, product_id, lifecycle_status,
  location_code, container_id, container_qr_code, origin_type, origin_reference.
- organization_missing=true/false/отсутствует означает NULL/NOT NULL/все. Сочетание
  organization и organization_missing=true отклоняется с 422.
- Счётчики всех lifecycle, отдельные active loose/container. Registered не считается
  физически размещённым КИЗ; pending inbox и неидентифицированная россыпь не учитываются.
- location_code учитывает прямой адрес либо текущий адрес контейнера, без descendants
  и реконструкции истории. Сохранённый holder закрытой identity не означает physical stock.
- Пагинация групп, стабильная сортировка NULL FIRST, итоги по всей выборке. Пустой
  результат — 200 с нулевыми счётчиками, не 404. Неизвестные параметры запрещены.
- Три SELECT выполняются в одной read-only REPEATABLE READ транзакции. Значения
  параметризованы, имена группировок берутся только из фиксированного whitelist.
- Полные схемы запросов/ответов, русские описания и пример 200 опубликованы в Swagger.

## Проверки

- Новые unit/API/PostgreSQL проверки: 55 passed.
- Целевой прогон с существующими Swagger-тестами: 73 passed.
- Полный regression: 784 passed, 1 failed, 66 Pydantic deprecation warnings.
- Failure: test_concurrent_transfer_and_assignment_preserve_touch_protocol — ранее
  известный deadlock assignment/transfer. Он зафиксирован до этой задачи в
  docs/current/open_questions.md и docs/audit/kiz_merge_result_20261020.md.
  Протокол физических writers этим endpoint не изменён и здесь не исправлялся.
- Проверены NULL organization, все состояния, фильтры, CSV/repeated group_by,
  pagination за концом, неизвестные значения, параметризованные SQL values,
  контейнерный holder, нулевые результаты, 422/500 и отсутствие побочных записей.
- При конкурентном INSERT из другой транзакции totals, total_groups и items
  остаются согласованными; следующий HTTP запрос видит обновлённый реестр.
- Тесты выполнялись на изолированном локальном PostgreSQL 18, disposable clones.
  Production БД и RabbitMQ не использовались, миграции на хост не применялись.
- `git diff --check` и Black для новых Python-файлов проходят.

## Файлы

Новые:
- app/api/v1/endpoints/kiz_reports.py
- app/core/schemas/kiz_reports.py
- app/core/services/kiz_report_service.py
- app/infrastructure/database/queries/kiz_reports.py
- app/infrastructure/database/repositories/kiz_report_repository.py
- tests/test_kiz_reports.py
- tests/test_kiz_reports_postgres.py
- docs/audit/kiz_reports_summary_20261021.md

Обновлены:
- app/api/v1/dependencies.py
- app/api/v1/router.py
- tests/test_swagger_documentation.py
- docs/current/api_map.md
- docs/current/business_rules.md
- docs/current/kiz_api.md
- docs/decisions/decisions.md

## Условия и риски

Новых миграций, пакетов и env-переменных нет. Нужна существующая схема КИЗ с organization.
Не применялись SQL writes к production. Список и карточки КИЗ не изменены.
На больших выборках точные totals и total_groups требуют агрегации всех подходящих
identity: limit ограничивает ответ, а не стоимость общего подсчёта. Отдельные HTTP
запросы могут видеть разные состояния. Фильтры организации не являются механизмом ACL.
Бизнес-вопросов для уточнения не осталось. Коммит и deployment в этой задаче не выполнялись.
