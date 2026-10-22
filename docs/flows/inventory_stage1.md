# Инвентаризация — этап 1: документы и пересчёты

Реализация от `feature/kiz-fbs-integration` (`404c55e`), отдельная ветка
`feature/inventory-stage1`. Production deployment исходной ветки не предполагается.

## Граница этапа

Модуль пишет только собственные `inventory_document_*` и `inventory_location_claims`.
Inventory, movements, movement registry, КИЗ, контейнеры, tasks и ФБС только читаются.
Нового типа движения, применения, approved/applied-статуса и apply endpoint нет.
Каждый reconciliation response содержит `preliminary_only=true`, `safe_for_apply=false`.

`INVENTORY_MODULE_ENABLED=false` по умолчанию. При выключенном модуле все его
endpoints возвращают 503 до создания pool/обращения к новым таблицам. Это позволяет
развернуть код до миграции. `INVENTORY_APPLY_ENABLED=false` обязателен для этапа 1:
ошибочное включение делает новый модуль недоступным (503), а не разрешает движения.
Существующие endpoints и consumers от этих flags не зависят.

## Схема и жизненный цикл

Миграция: `scripts/migrations/20261022_add_inventory_documents.sql`.

| Таблица | Назначение |
|---|---|
| inventory_documents | Заголовок, состояние, optimistic version, current/submitted revision |
| inventory_document_filters | Исходные selections и SKU-фильтр, редактируемые только в draft |
| inventory_document_locations | Неизменяемые location IDs/codes/path/is_active на старте |
| inventory_location_claims | Одна активная инвентаризация на location_id (PK) |
| inventory_document_items | Одна строка SKU/location, неизменяемый initial expected |
| inventory_document_stock_components | Исходные inventory rows, партии, physical/active/unidentified |
| inventory_document_kiz_snapshot | Стабильные kiz_id/lifecycle/holder; без копирования/нормализации кода |
| inventory_count_attempts | Номер, автор, времена, прогресс/результат, revision, start/finish evidence |
| inventory_document_revisions | Неизменяемые start/reconciliation snapshots и свидетельства |
| inventory_document_history | Append-only команды, прогресс, решения и их авторы |
| inventory_document_commands | UUID key, canonical request hash и сохранённый ответ |

Начальный inventory_id хранится как свидетельство без FK к физической строке:
обычный movement имеет право удалить обнулённый inventory. Новые snapshot guards
защищают только таблицы модуля. Существующие функции и триггеры не заменяются.
Snapshot kiz_id тоже хранится как историческое свидетельство без FK к live КИЗ,
чтобы не держать KEY SHARE на identities во время построения документа.

Переходы: `draft → counting → pending_approval → counting` либо
`closed_without_adjustment`. Закрыть без применения можно из draft/counting/pending;
закрытие окончательное. Claims сохраняются в counting и pending_approval,
освобождаются только при закрытии. Черновики не имеют claims.

Все write-команды, кроме create, требуют `expected_version`. Версия увеличивается
при записи; submit одновременно создаёт reconciliation revision и меняет статус,
поэтому версия может увеличиться на два. Клиент всегда использует версию из ответа.
Чтение и preview версию не увеличивают.

Каждая write-команда требует новый UUID `idempotency_key` и `user_name`. Replay
**того же полного запроса** возвращает первоначальный сохранённый ответ, даже если
документ уже изменился. Ключ с другим телом/URL/командой даёт 409. UUID уникален во
всём модуле, не только в документе. После сетевой ошибки повторять исходный запрос
с тем же ключом; после 409 VERSION_CONFLICT перечитать документ и создать новую команду.

Транзакции write-команд используют REPEATABLE READ, lock документа и отдельный
advisory key команды, bounded retry 40001/40P01. Начальный snapshot, scope и claims
коммитятся вместе; unique location claim защищает конкурентный старт даже при разных
SKU. Порядок claims — sorted location_id. Явные row locks physical inventory/КИЗ
не берутся; обычные FK-проверки ключей товаров/локаций сохраняются.
Локальные timeouts: lock 5s, statement 15s. Общего протокола сериализации writers нет.

## Область

Warehouse — существующая активная root location (`parent_location_id IS NULL`),
не произвольная зона. Полная инвентаризация включает root и всех descendants,
включая пустые и неактивные locations. Неактивные адреса не исключаются, preview
показывает предупреждение, stock lines получают conflict.

Partial требует locations и/или product_ids. Selection —
`{"location_id":123,"include_descendants":true}`; false выбирает только exact ID.
Выбранный родитель включается. SKU-only материализует и claims весь склад,
включая пустые адреса. SKU+locations применяется как пересечение. Parent/path должны
задавать одинаковый набор warehouse descendants; иначе start/preview дают 409.
`level` не используется как authoritative hierarchy.

После старта selections, SKU-фильтр и materialized IDs не изменяются. Новые или
перенесённые locations не меняют документ. Для GET scope возвращаются исходные
codes/path, чтобы история не менялась при переименовании адреса.

## Учёт и пересчёт

Учитываются available rows с container_code IS NULL, с сохранением batch dimensions.
Active loose КИЗ вычитаются только из null-batch physical capacity их exact holder.
Registered holderless не вычитаются: они сохраняются как контекст с признаком
`registered_kiz_without_holder`. Terminal КИЗ не занимают capacity.
Expected pair — max(0, сумма physical − active loose). При нарушении capacity
исходный отрицательный component сохраняется и выставляется conflict; clamp не
является исправлением данных или разрешением применения.

Автоматически создаются только строки с положительным initial expected. Пустые
адреса остаются в scope. Найденный SKU можно добавить только в frozen scope/SKU
filter; initial expected=0. Его текущий expected после последующего прихода может
быть положительным и показывается отдельно.

Multi-batch, NULL/empty aliases, active КИЗ и registered-without-holder сохраняют
conflicts. Начальный snapshot не распределяет потери по партиям. Нет притворной
физической идентификации маркированных единиц.

`actual_quantity=null` означает отсутствие результата; 0 — пересчитанный ноль.
Количество — integer или decimal string до 2 знаков и 12 цифр; float/bool запрещены.
Прогресс не считается завершённым count. На строку разрешена одна открытая попытка;
следующая начинается после завершения предыдущей. Каждое сохранение прогресса
также записывается в history, completed attempt неизменяем.

Submit требует completed count всех строк и отсутствие `needs_recount`. Ambiguous
строки допускаются к рассмотрению: pending_approval не означает разрешение physical
apply. Возврат может потребовать пересчёт всех либо выбранных affected item_ids;
решение принимается для документа целиком, выборочного approval строк нет.

## Свидетельства и предварительная актуализация

Start сохраняет видимый набор координат movements/registry и ID КИЗ events.
Reconciliation сравнивает identity sets и physical/KИЗ state, а не timestamp watermark.
Это обнаруживает late commit со старым created_at. Evidence bounded: максимум 100 000
rows на stocks/KИЗ/movements/events в одном capture; превышение даёт 409 с полным
rollback, нужно сузить scope. Ничего не отбрасывается молча. Полная история scope
может быть объёмной; stage load test обязателен перед большим warehouse pilot.

Revisions и attempts хранят observed_at как время наблюдения, не commit sequence и
не физическое время. Container fill/extract обнаруживаются через loose legs;
КИЗ activation/deactivation — через events и state. Любое изменение между start/finish
попытки даёт `state_changed_during_count`. После finish — `state_changed_after_count`.
Изменение состояния без нового ledger evidence тоже сохраняется как conflict.

Ship после snapshot имеет `shipment_physical_time_unverified`: нельзя доказать, был
ли товар физически изъят до count либо после. Даже обычный receive не используется
для автоматического переноса actual на новый момент. Например, initial=10, actual=8,
receive+3 → current expected=13, initial discrepancy=-2; actual остаётся 8, apply=false.
Предлагаемого physical adjustment или доказанно безопасного target нет.

Настроенная `FBS_LOCATION_CODE` отмечается как потенциально неоднозначная даже без
нового ship: отсутствие DB события не доказывает отсутствие задержанной отгрузки.
Очередь и внешний физический процесс модуль не опрашивает и не останавливает.

Event timestamps, maintenance, missing ledger history и временно невидимые транзакции
не позволяют доказать точность reconciliation. Этап 1 только сохраняет свидетельства,
показывает ambiguity/recount и даёт ответственный workflow.

## API

Prefix `/api/inventory-documents`. List endpoints используют limit (1–1000)/offset,
response `{items,total,limit,offset}`. Errors — detail/error_code/diagnostics;
404 not found, 409 state/version/scope/idempotency, 422 DTO, 503 flags.

| Method | Suffix | Назначение |
|---|---|---|
| POST | empty | Создать draft |
| GET | empty | Список; status/warehouse_location_id |
| GET/PATCH | /{id} | Получить/изменить draft |
| POST | /{id}/preview-scope | Read-only preview draft |
| GET | /{id}/scope | Frozen locations |
| POST | /{id}/start | Snapshot и claims |
| GET/POST | /{id}/items | Строки / добавить найденный SKU |
| GET/POST | /{id}/items/{item}/attempts | История / начать попытку |
| PUT | /{id}/items/{item}/attempts/{attempt}/progress | Сохранить прогресс |
| POST | /{id}/items/{item}/attempts/{attempt}/complete | Завершить |
| POST | /{id}/reconcile | Новая предварительная revision |
| POST | /{id}/submit | Отправить целиком |
| POST | /{id}/return-to-count | Решение с reason, optional item_ids |
| POST | /{id}/close-without-adjustment | Решение с reason, освобождение claims |
| GET | /{id}/history | Полная audit history |
| GET | /{id}/revisions | Неизменяемые evidence revisions |

Create example:

```json
{
  "idempotency_key": "34473291-ddbb-48b2-a9cc-e900a6b27c81",
  "user_name": "operator",
  "title": "Пересчёт зоны",
  "mode": "partial",
  "warehouse_location_id": 1,
  "locations": [{"location_id": 20, "include_descendants": true}],
  "product_ids": ["wild100"]
}
```

Start/reconcile/submit/begin body:

```json
{"idempotency_key":"f806790e-dcb1-4f46-8ed3-a2bc20709db6","expected_version":1,"user_name":"operator"}
```

Progress/complete добавляет actual_quantity (например "0"); return/close — reason;
add-found — product_id/location_id. UUID из примеров не переиспользовать для разных команд.

## Безопасное развёртывание и rollback

1. Сверить runtime schema на stage с КИЗ/registry prerequisites и сделать backup.
2. Проверить additive SQL на изолированной PostgreSQL 17 с текущими migrations/triggers.
3. Применение на production выполняет владелец БД вручную, не приложение/consumer.
   SQL создаёт только новые таблицы/functions/triggers. Старые API не обращаются к ним.
4. Дать runtime-роли SELECT на используемые physical tables, CRUD только необходимых
   module tables и sequence privileges. Physical write privileges нового модуля не нужны.
5. Развернуть код с обоими flags=false. Затем включить только module на stage/pilot.
6. Smoke: create/start/count/submit/return/close, overlap race и сравнение physical state.
7. При rollback выключить module. Additive schema и audit оставить; не удалять историю.

Миграция не предназначена для повторного запуска; записать её применение во внешнем
journal. Production миграции и тесты этим изменением автоматически не запускаются.

## Проверки реализации (22.10.2026)

- 43 новых сценария: 14 unit/контрактных и 29 PostgreSQL/API acceptance cases,
  включая параметры фильтров. Все прошли в финальном полном прогоне.
- Финальная регрессия на PostgreSQL 17: **827 passed, 1 failed**, 66 существующих
  Pydantic deprecation warnings. Неуспешен существующий
  `test_concurrent_transfer_and_assignment_preserve_touch_protocol`: intermittent
  deadlock assignment/explicit transfer. Этот тест использует kiz_pool без новой
  inventory migration; его service/repository/test files не изменены. Предыдущий
  полный прогон прошёл 827/827 до добавления финального 43-го нового сценария.
- Known assignment/transfer/ship lock-order risk уже зафиксирован в
  `docs/current/open_questions.md`; он не исправлялся в рамках inventory.
- Новая проверка удерживает незавершённую snapshot transaction и выполняет штатную
  КИЗ deactivation параллельно; writer завершается до commit snapshot. Проверены
  immutable snapshot/attempts и неизменность всех existing wms function definitions.
- Black и Ruff прошли для 8 новых Python-файлов; diff whitespace checks прошли.
- Production не использовался. Тесты создавали/удаляли только локальные disposable
  databases в отдельном PostgreSQL 17 container на 127.0.0.1:55441.

Для воспроизведения сначала явно задать **все** test DSN на изолированный localhost:
`KIZ_TEST_DSN` и `KIZ_LOCK_TEST_DSN` → `kiz_test`,
`KIZ_B2_TEST_DATABASE_URL` → `kiz_b2_test`,
`KIZ_ACTIVATION_TEST_DATABASE_URL` → `kiz_activation_test`.
Последние два existing fixture пересоздают schema: никогда не использовать рабочую БД.
Эти databases создаются на тестовом сервере заранее; `kiz_pool` создаёт case clones.

```bash
PYTHONPATH=. FBS_TASK_PROCESSING_MODE=legacy .venv/bin/pytest -q -p no:cacheprovider
```

Legacy здесь — детерминированный baseline для старых FBS mock tests; task-level cases
сами явно переключают test settings. Изменять production FBS mode для тестов не нужно.

## Следующий этап

Отдельно проектируются approval revision locking, physical count boundary/FBS,
batch allocation, строгий inventory-source adjust, immutable application links на
существующий movement_registry, document-wide transaction и idempotent apply.
Существующие source links/движения не переписываются. Global writer version protocol
и изменение общей updater function не входят в этот этап.
