# Inventory Stage 2A — подготовительный fallback, без physical apply

**Исторический результат preparation-этапа.** После согласования адресной блокировки
реализован [контролируемый physical pilot](inventory_stage2a_physical_apply.md).
Ограничения ниже описывают состояние до этого решения. Существующие preparations/components
переиспользуются; документы без подходящего freeze по-прежнему неприменимы.

Проверка начата 29.10.2026, завершена 30.10.2026. Ветка `feature/inventory-stage1`, исходный HEAD `8aa94e4`.
Ранее незакоммиченные исправления B.1/B.2 сохранены. Production не подключалась и не менялась.

## Статус и причина ограничения

Это разрешённая ТЗ подготовительная часть, **не реализация безопасного physical writer**.
Ни положительная, ни отрицательная, ни нулевая корректировка сейчас не подтверждается.
Нет статуса applied, успешного approval и новых movement types. Flags по умолчанию false.

Текущий count хранит наблюдения БД на start/finish, но не свидетельство физического
отсечения/публикации операций. Для обычного available SKU без КИЗ и партий возможны два
неразличимых случая: учёт=10, count=8, текущий учёт=10, новых movements нет.

| Физическая ситуация | Правильное действие | Наивный adjust -2, затем поздний transfer -2 |
|---|---|---|
| Реальная недостача 2, отложенных операций нет | Недостача -2 | Остаток 8 |
| Уже переместили 2 до count, HTTP transfer ещё не записан | Дождаться записи transfer, не списывать повторно | Ошибочный остаток 6 |

Контрпример воспроизведён в disposable PG17 через текущий `/api/movements` и реальные
triggers. Это моделирование задержки публикации, **не доказательство наличия именно
такого физического процесса в production**. Код не исключает этот процесс. Persistent
regression test проверяет безопасный отказ и последующий обычный transfer с остатком 8.
Тестовый наивный counterexample-скрипт был вне репозитория; в runtime модуле его нет.

SQL-блокировка на время approval не препятствует отложенной записи после commit.
Окно count и поздние ФБС требуют доказуемых физических границ, не timestamp эвристики.
Непроверенное предположение о дисциплине сотрудников не превращено в auto-apply eligibility.

## Что добавлено

Миграция `scripts/migrations/20261029_add_inventory_application_preparations.sql` additive:

| Таблица | Назначение |
|---|---|
| inventory_application_preparations | Одно историческое наблюдение на document/submitted_revision; version, author, current evidence, строки и blockers; can_apply=false enforced CHECK |
| inventory_application_components | Стабильный component_id для physical components текущего наблюдения; составные FK в документ/строку/подготовку; batch и остальной контекст в stock_evidence |

Для отсутствующей physical row сохраняется компонент с inventory_id=null. Aggregate
actual не распределяется между партиями. Исходный snapshot остаётся в таблицах Stage 1.
Нет FK к live inventory rows, которые могут удаляться обычными movements. Исторические
наблюдения не обновляются и не удаляются (DB triggers). Будущие writer/registry links
потребуют отдельной миграции: FK к существующему movement_registry, а не новый registry.
Сохранённые preparation/component IDs можно использовать как технические идентичности;
они не означают созданное движение. source_type=inventory_document пока только метаданные.

Переиспользованы command protocol (UUID/hash, document FOR UPDATE, RR, bounded retry),
read-only capture и алгоритм reconciliation. Для reuse выделено observe_reconciliation,
его вызов сам по себе не создаёт новую ревизию. Stage 1 reconcile/submit работают как прежде.

## API

Все URL под `/api/inventory-documents`:

| Метод и путь | Результат |
|---|---|
| POST /{document_id}/application-preview | ApplicationCommand: UUID, expected_version, user_name, submitted_revision. Только pending_approval и current_revision=submitted_revision. Сохраняет заблокированное наблюдение и компоненты, audit, command одной транзакцией. |
| POST /{document_id}/apply | Apply false: 503 INVENTORY_APPLY_DISABLED до pool. Apply true: optimistic version/state/submitted revision checks, затем 409 INVENTORY_APPLY_PROTOCOL_UNAVAILABLE. Ничего не коммитит. |
| GET /{document_id}/application | Текущее состояние документа, последнее историческое наблюдение либо null, applied=false, movements=[] |

При первом preview версия документа увеличивается на 1, ревизия и submitted_revision
не меняются. `observed_document_version` — версия **до** подготовки. Для следующей команды
нужно перечитать документ через существующий GET. Replay полного UUID запроса возвращает
исходный ответ. Другой payload с тем же UUID даёт 409. Новый UUID для той же ревизии
возвращает ту же историческую подготовку без изменения версии (audit команды сохраняется).
Для нового наблюдения требуется return-to-count и повторный submit. Повтор preview не
означает актуализацию. Result после закрытия также остаётся историческим наблюдением.

Все линии safe_for_apply=false. `provisional_difference=actual-current_expected` — только
арифметический индикатор, **не величина разрешённой корректировки**. Исходное expected
никогда не переписывается. Multi-batch, КИЗ, движения и остальные конфликты сохраняются,
плюс глобальные physical_count_boundary_unproven и physical_writer_not_implemented.

## Транзакции и совместимость

Уникальность document/submitted_revision и component coordinates — constraints БД.
Начало подготовки, компоненты, version bump, audit и сохранённый UUID-ответ атомарны.
Commit происходит до HTTP success; deferred error откатывает всё. Rejected apply не
создаёт application/history/commands, не освобождает claims, не меняет lifecycle.
История успешной подготовки сохраняется; отклонённый запрос не выдается за принятое решение.

| Компонент | Изменение / ограничение |
|---|---|
| Stage 1 API | Существующие payload/response/state неизменны; модуль работает и при apply=true |
| Public POST /api/movements, обычный adjust | Не изменены; legacy COALESCE/missing-row semantics не исправлялись |
| update_inventory_from_movement и КИЗ triggers | Не заменяются; fixture проверяет неизменность существующих function definitions при миграции |
| FBS/HTTP/RabbitMQ/retry, kits, containers, re-sorting, tasks | Ни код, ни SQL contracts не менялись |
| Registry, operations/history/integrity | Не создаются новые movements, новые типы/reader CASE не требуются |
| Maintenance | Код не менялся; действующий recalculate блокирует movements SHARE, затем inventory EXCLUSIVE |

На disposable PG17 подтверждён конфликт ROW EXCLUSIVE movements / SHARE maintenance
через NOWAIT (55P03). Это возможный элемент будущего протокола, а не причина отказа от
writer. Полный writer lock order, absent-row/FK races, КИЗ touch и legacy deadlock пока
не объявляются решёнными. RR/advisory command locks защищают только новый документ.

## Внедрение и дальнейшая работа

1. Проверить migration на staging, вручную применить после Stage 1 migration. Не повторять
   CREATE TABLE как идемпотентную миграцию; повторное выполнение даст ошибку без частичного DDL.
2. Старая версия сервиса совместима с новыми отдельными таблицами. DB-first, затем backend.
   Module=false позволяет развернуть код до миграции; при module=true migration обязательна.
3. Оставить INVENTORY_APPLY_ENABLED=false. Включение true в этой версии не включает writer.
   Для rollback приложения удалять preparations, inventory или movements не нужно.
4. Определить проверяемую границу count и delayed publications. Разрешать только явно
   поддержанные локации, не автоматически весь склад или штатную ФБС-зону.
5. Отдельно доказать SQL lock/recheck order для всех разрешённых writers (включая gap для
   отсутствующей строки и lifecycle КИЗ). Не исправлять посторонний KИЗ deadlock здесь.
6. Только затем вводить source-specific строгий adjust с exact NULL-safe ключом, existence,
   sufficient eligible quantity и проверкой фактического эффекта. Добавить DB idempotency
   approval, FK movement_ref, approved revision/компонент/source provenance, applied state
   и атомарное освобождение claims. Legacy adjust обязан сохранить семантику.

Текущий fallback не доказывает успешные surplus/shortage/zero apply, concurrent physical
adjust, exact projection effects нового source и backward compatibility изменённой общей
функции: writer и такая функция **не реализованы**. Эти сценарии остаются обязательными
до включения physical apply. Batch allocation и FBS auto-reconciliation отложены до 2B.

## Проверки

Используются только локальные disposable базы PostgreSQL **17.4**, актуальные
migration/trigger fixtures проекта. Production DSN не использовался. Контрпример и
проверка конфликта maintenance выполнены в отдельных клонах той же тестовой базы.

- Первый узкий прогон: 82 passed, 2 failed — два новых fault-injection теста ошибочно
  ожидали исключение из HTTP ASGI, хотя общий handler возвращает HTTP 500. Ожидания
  исправлены на HTTP 500 с проверкой полного DB rollback, production-код не менялся.
- Первый полный прогон: **871 passed, 2 failed**, 163.92 s. Failures: несинхронная карта
  API (обновлена) и существующий test_concurrent_transfer_and_assignment_preserve_touch_protocol.
- Проверка OpenAPI/API-map, unit contracts и документационных ссылок после обновления:
  **45 passed**, 4.17 s. Инвентаризация представлена 22 операциями в OpenAPI.
- Исходный commit **404c55e**, извлечённый через git archive только в /tmp, тот же
  PostgreSQL 17.4, 8 отдельных запусков проблемного KИЗ-теста: **4 deadlocks / 8**.
  git diff к baseline для KizService/KizTransferService, их repositories и теста пуст.
  Воспроизведение доказывает предшествование дефекта; оно не доказывает отсутствие других
  гонок. Deadlock не исправлялся и тест не отключался.
- Финальный полный прогон: **878 passed, 1 failed**, 192.49 s. API-map и все тесты
  нового модуля прошли. Failure: test_concurrent_ship_and_assignment_preserve_touch_protocol,
  DeadlockDetectedError в неизменённом KizShipRepository.lock_inventory.
  ShipService/ShipRepository и этот тест также идентичны исходному 404c55e.
- Независимая проверка ship на **404c55e**: первые 8 отдельных запусков прошли;
  дополнительные **32** повторения через временную autouse-параметризацию вне рабочего
  дерева дали **5 DeadlockDetectedError, 27 passed**, 21.08 s. Исходный тест не менялся.
  Таким образом, оба наблюдавшихся КИЗ lock-cycle подтверждены на baseline; единичный
  green запуск не доказывает их устранения. Полный suite не объявляется green.
- Отдельный финальный прогон всех тестов инвентаризации: **94 passed**, 50.55 s
  (41 unit/API-contract + 53 PostgreSQL). Из них 21 сценарий добавлен текущим этапом.
- black --check затронутых Python-файлов и git diff --check — успешно.

Новые PostgreSQL проверки: blocked surplus/shortage/fraction/zero/no-delta;
идемпотентный replay, новый UUID на ту же ревизию, конфликт payload, конкурентные
preparations; stale version/revision/state; многокомпонентный rollback; deferred failure
на commit с повтором того же UUID после rollback; immutability; multi-batch/NULL-empty;
active КИЗ; configured FBS; delayed ordinary transfer; Stage 1 с apply=true/false.
Физические таблицы/registry сравниваются до/после операций нового модуля. КИЗ-контекст
создаётся текущими guards/fixtures, не production data. Реальный транспорт RabbitMQ
и production physical workflow этой задачей не тестировались.

Успешные физические применения и source-specific расходы **не тестировались**, поскольку
не реализованы. Отсутствие нового source-specific guard нельзя трактовать как устранение
пограничного поведения старого adjust.

## Состав изменений текущего этапа

Новые файлы:

- app/infrastructure/database/queries/inventory_applications.py
- scripts/migrations/20261029_add_inventory_application_preparations.sql
- tests/test_inventory_applications_postgres.py
- docs/flows/inventory_stage2a_preparation.md

Изменены в рамках этой задачи (поверх сохранённых B.1/B.2):

- app/api/v1/dependencies.py
- app/api/v1/endpoints/inventory_documents.py
- app/core/schemas/inventory_documents.py
- app/core/services/inventory_document_service.py
- tests/test_inventory_documents_postgres.py (применение новой migration в disposable fixture)
- tests/test_inventory_documents_unit.py
- docs/current/api_map.md
- docs/current/open_questions.md
- docs/decisions/decisions.md
- docs/flows/inventory_stage1.md
- scripts/migrations/README.md

Существующий diff app/infrastructure/database/queries/inventory_documents.py относится к
предшествующему исправлению B.1; на этом этапе файл не изменялся. Предыдущие audit reports
и пользовательские untracked файлы не перезаписывались. Ветка не менялась; commit и
deployment не выполнялись. Миграция применялась только в disposable integration fixtures.
