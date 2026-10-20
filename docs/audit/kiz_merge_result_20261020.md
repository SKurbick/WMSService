# Объединение веток КИЗ — 20.10.2026

## Результат

В `feature/kiz-fbs-integration` объединены исходные состояния:
- `643e50e` — FBS task-level, receipt import, activation, organization и аудиты;
- `8047d45` (`feature/kiz`) — registry, карточки/история, explicit transfer/ship,
  controlled container operations B1–B4/C1 и русская документация API.

`feature/fbs-task-level-processing` не требуется вливать повторно: соответствующая
task-level линия уже включена в исходную integration-ветку. Ветки-источники не удалены.

## Проверка сохранности

- Сверены tracked-файлы обоих родителей: отсутствующих файлов нет.
- Сверены маршруты обоих родителей с итоговым OpenAPI: приложение содержит 107
  HTTP-операций. Неожиданно потерянных маршрутов нет.
- Два legacy маршрута `POST /api/containers/{container_id}/unpack` и
  `PUT /api/containers/{container_id}/location` намеренно не возвращены: в feature/kiz
  они уже запрещены этапом B4. Их функции доступны через controlled
  `/api/container-operations/move`, `/extract`, `/unpack-all`; запрет legacy bypass
  проверяется существующими B4-тестами.
- Сохранены зарегистрированные receipt identities без holder, activation и active
  guard; FBS bindings, per-СЗ movement_ref, multi-source списание, legacy flow и retry.
- Повторная обработка duplicate-only сохраняет существующий task-level контракт:
  shipment не становится новой успешной отгрузкой, дополнительных движений нет.

## Разрешение пересечений

- Объединены роутеры, зависимости, обработчики ошибок, consumer/retry и документация.
- У read-схем КИЗ добавлена совместимость с registered/receipt_import,
  registered/activated events и organization.
- Два import-пути KizConflictError приведены к одному классу с diagnostics/error_code.
- Сохранена отдельная обработка KizGuardError и ограниченных конкурентных retries;
  при terminal item status next_retry_at очищается.
- Русская документация registry/container и import/activation подключена одновременно.
- Тестовый шаблон БД применяет обе существующие цепочки миграций последовательно.
- Добавлен `tests/test_kiz_merged_flow.py`: совместный receipt → activation → transfer
  → multi-source FBS → replay, guard и атомарный rollback при нехватке остатков.
- Тест RESTRICT удаления допускает обе подходящие PostgreSQL exception classes,
  сохраняя обязательную проверку запрета удаления.

## Проверки

- Полный pytest: **690 passed, 3 skipped**, 66 предупреждений Pydantic deprecation.
- Три пропущенных design-теста блокировок затем выполнены отдельно с явно заданным
  `KIZ_LOCK_TEST_DSN`: **3 passed**. Итого выполнены все 693 теста.
- Использован отдельный локальный PostgreSQL 18 на `127.0.0.1:55439`, только disposable
  БД `kiz_test`, `kiz_b2_test`, `kiz_activation_test` и тестовые клоны.
- Production БД, очереди и runtime consumers не запускались и не изменялись.
- SQL-файлы существующих миграций сохранены; новый production SQL при слиянии не
  создан и не применён. Перед deployment требуется сверка фактической схемы с
  объединённой цепочкой, описанной в `scripts/migrations/README.md`.

## Оставшиеся риски

Слияние и тесты не доказывают отсутствие всех возможных конкурентных ошибок.
В предыдущих прогонах воспроизведён intermittent deadlock assignment против
explicit transfer/ship; тот же сценарий воспроизведён и на исходной feature/kiz,
поэтому он не исправлялся скрыто в merge. В итоговом полном прогоне соответствующие
тесты прошли. Риск отдельно зафиксирован в `docs/current/open_questions.md`.

Production post-deployment smoke и проверка реально применённых миграций остаются
отдельным этапом. Push и deployment не выполнялись.
