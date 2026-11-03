# Inventory Stage 2A — единый релизный кандидат, 03.11.2026

## 1. Вердикт и идентификация

**Единый воспроизводимый кандидат подготовлен. Финальный Git-релиз ещё не зафиксирован; production pilot — NO-GO до закрытия условий ниже.** Коммит, tag, push, deployment и изменения production/RabbitMQ не выполнялись.

Исходное рабочее дерево оставлено на `feature/inventory-stage1`, HEAD `8aa94e40e868f41084537fd68016dc988cd59a26`. Все 371 исходных tracked/untracked файлов из контрольного списка сохранили SHA-256. Этот отчёт — единственный новый файл в исходном дереве в рамках подготовки кандидата. Исходные незакоммиченные изменения не переносились через stash и не удалялись.

Отдельный worktree: `/home/skurbick/PROJECTS/WMSService-releases/inventory-stage2a-20261103/worktree`.

Ветка: `release/inventory-stage2a-candidate-20261103`. Её HEAD пока `fc0662a6e2bd2213123d291c434a5db6fdddefe3`, а не коммит готового Inventory Stage 2A. Интегрированный состав находится в индексе, без создания коммита:

```text
source_tree = dc4aacec5347d7ba68a25015d7e7bbb595284549
381 файлов; относительно fc0662a: 52 файла, +10387 / -11 строк
release_commit = null
runtime_image = sha256:4004d2383c67fd739be0a7e5b191b97b22edd063f67928ee42c0bc264cb1159c
```

Артефакты: `/home/skurbick/PROJECTS/WMSService-releases/inventory-stage2a-20261103/artifacts`.

Неизменяемый по содержимому manifest:
`release-manifest.2aff56c09ec5cae7fa92809608e186bf4e418d1918faa813e70281da556de107.json`.
Имя содержит SHA-256 его содержимого. Manifest имеет статус `candidate_not_committed`, а не вымышленный release commit.

Рядом сохранены `source.tar.gz` с нормализованными timestamp/UID/GID, полный binary `candidate.patch` относительно fc0662a, `source-files.json` с Git blob/mode/SHA-256 каждого файла, `runtime-image.tar`, SQL allowlist, JUnit и проверочные журналы, `SHA256SUMS`. Это локальные артефакты; registry digest ещё не существует. Исходники приложения внутри итогового образа сверены с кандидатом по SHA-256.

## 2. Состав интеграции и конфликты

Общий предок веток — `404c55edec0d2da1dbdf3bb30e9745fe5ec8ec83`. От него inventory имеет 8aa94e4 и рабочие Stage 2A/B.1/B.2, базовая КИЗ-ветка — следующие три коммита:

| Коммит | Включённое поведение | Совместимость с Inventory |
|---|---|---|
| 5380629 | Автоматическая регистрация КИЗ независимо от поступления | Registered без holder не расходует физическую ёмкость и не вычитается из произвольного адреса. Изменение relevant КИЗ-контекста делает старую preparation неактуальной. |
| 07daa45 | Частичный импорт КИЗ, аудит коррекции product | Валидные группы принимаются, ошибочные отражаются отдельно; сохраняется идемпотентность импорта. КИЗ-history требует schema migration product corrections. |
| fc0662a | Оборот склада и summary истории | Turnover намеренно учитывает receive/ship, а не adjust. Inventory adjust отражается в balances и самостоятельной истории/summary операций; смысл оборота не изменён. |

На отдельной базе fc0662a применён 3-way overlay полного inventory-состава, включая выбранные untracked файлы. Runtime-конфликтов не возникло. Конфликты в `docs/current/business_rules.md`, `docs/decisions/decisions.md`, `scripts/migrations/README.md` разрешены сохранением независимых разделов обеих веток. Это объединение содержимого, а не merge/rebase исходной грязной ветки.

Из 18 upstream runtime-файлов 17 сохранены побайтово относительно fc0662a. В `app/core/schemas/kiz_import.py` с отдельного согласия пользователя добавлены **только descriptions** для `KizImportGroupRejection.error_code/error_message`: без изменения типов, обязательности, defaults и валидации. На чистом fc0662a отсутствие descriptions независимо воспроизведено Swagger-тестом. Остальные upstream изменения бизнес-логики не исправлялись.

Проверенные совместные сценарии добавлены в `tests/test_inventory_release_compatibility_postgres.py`: реальный consumer handler авто/частичного импорта с реальными repository/PG при freeze, повтор сообщения и устаревший apply; затем корректность balances/history/turnover после inventory shortage. Транспорт сообщения тестовый, это не подтверждение подключения реального брокера.

### Что включено и исключено

- Включены Stage 1, preparations, freeze, physical apply, обе B.1/B.2 правки, scoped preflight, inventory tests и четыре inventory audit-отчёта как проверяемое происхождение решений. Flow/runbook сохранены.
- Унаследованные tracked engineering audits базовой ветки оставлены как source provenance. Они не попадают в runtime или SQL bundle.
- Не копировались новые посторонние `docs/audit/wild188901_corrections_retry_20261025.md` и `fbs2_missing_kiz_2026-10-21_2026-10-22.txt`.
- Исторический `scripts/maintenance/20261023_correct_confirmed_kiz_products.sql` остаётся унаследованным source-файлом, **не включён в DB bundle и не выполнялся**. Автоматический запуск maintenance/data scripts запрещён.

## 3. Упаковка и конфигурация

Изменения только в отдельном кандидате:

| Файл | Назначение |
|---|---|
| `.dockerignore` | Исключение .env, .git, docs/audits, tests, scripts, локальных данных и compose из runtime image |
| `constraints.release.txt` | 61 точная версия установленного окружения; используется вместе с исходным requirements |
| `Dockerfile` | Фиксированный digest Python base, установка с constraints, без неограниченного обновления pip |
| `docker-compose.yml` | `WMS_IMAGE` и `WMS_ENV_FILE` для проверенного образа и внешней конфигурации; существующая топология сохранена |
| `.env.example` | Явные freeze=false и pilot limit=20, остальные flags остаются false |
| `docs/releases/inventory_stage2a_candidate_2026-11-03.md` | План интеграции, упаковки, ручной фиксации, staging и отключения |
| `docs/decisions/decisions.md` | Решение о составе единого кандидата |
| `app/core/schemas/kiz_import.py` | Два согласованных описания полей Swagger |
| `tests/test_inventory_release_compatibility_postgres.py` | Два межмодульных PostgreSQL regression tests |

Новых endpoints, бизнес-правил и новых SQL-миграций сверх уже реализованного Stage 2A не добавлено. Полный состав 52 изменённых файлов — в patch/status/source-files артефактах.

Сохраняются `INVENTORY_MODULE_ENABLED=false`, `INVENTORY_FREEZE_ENABLED=false`, `INVENTORY_APPLY_ENABLED=false`, `INVENTORY_PILOT_MAX_LOCATIONS=20`. Реальные flags не включались. Настройки ролей, FBS modes и очередей не выдумывались: остаются операторскими параметрами для staging и preflight.

Образ: Linux amd64, Python 3.12.13, base `python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`. Версии всех Python distributions сохранены в `evidence/image-metadata.json`; host Python — 3.12.3. SQL проверен PostgreSQL **17.4**, digest `postgres@sha256:304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad`.

Полностью идентичный будущий rebuild через apt/pip не обещается: исходный apt-шаг установки fonts использует текущий пакетный репозиторий. Для deployment следует использовать сохранённый проверенный image, затем его registry digest, а не заново собирать mutable tag. `.env`/секреты в образ не встроены. `docker compose config --quiet` проверен с disposable env; `compose up` не запускался.

## 4. Миграции и rehearsal

Разрешённый SQL bundle:

1. `20261023_add_kiz_product_corrections.sql` — предварительно проверить эквивалентную установленную КИЗ-схему; при наличии не повторять.
2. `20261022_add_inventory_documents.sql`.
3. `20261029_add_inventory_application_preparations.sql`.
4. `20261030_add_inventory_location_freeze.sql`.
5. `20261030_add_inventory_physical_apply.sql`.

Отдельно включены `20261102_inventory_projection_preflight.sql` и `20261102_inventory_runtime_privileges_preflight.sql`. Порядок и scoped grants — по Stage 2A flow/runbook; projection preflight предшествует physical migration, roster/privilege contract — установке guards. SQL не следует запускать по wildcard или только по сортировке даты. Индивидуальные SHA-256 находятся в manifest и SHA256SUMS.

**Реально проверено:** в disposable PG17.4 восстановлен сохранённый schema-only snapshot production от **02.11**, не актуальная БД 03.11. SHA-256 snapshot: `eb58888f7c2d8a37f48b5275ebc94e2d57d1a561c99e25ed888c9992e6574ecd`. Нового production-подключения не было. Snapshot уже содержит product corrections; повторно эта migration не запускалась. Четыре inventory migrations установлены последовательно: 0.040 / 0.005 / 0.024 / 0.016 с в пустой копии, без production нагрузки. Это не оценка времени блокировок на рабочей БД.

В копии созданы семь non-superuser shadow roles по сохранённым профилям; scoped inventory grants применены только локально. Privilege preflight прошёл для всех семи ролей. До inventory migrations шесть ролей выполняли baseline write; у `manager51` уже тогда воспроизведён `permission denied for sequence movements_movement_id_seq`. Это существующий prerequisite, а не новая регрессия. Его нельзя скрывать широкими grants ради зелёного результата.

Под inventory-профилем на восстановленной схеме прошли: shortage, surplus, no-delta, release, отклонение frozen transfer/receive, deferred-commit rollback, standard FBS HTTP и оба новых межмодульных сценария — **10/10**. Точные результаты в `evidence/schema-rehearsal-summary.json` и `schema-compatibility.log`. Проверки не равны полному прогону каждого writer под каждой production-ролью. Migration ledger не подделывался; в сохранённой схеме после rehearsal по-прежнему 0 ledger rows. Для уже установленной старой версии Stage 2A нужен отдельный проверенный forward upgrade, повторный запуск изменённого historical SQL не разрешён.

## 5. Тестирование

Все записи сделаны только в disposable PostgreSQL на localhost:55454; production/RabbitMQ не затрагивались.

| Проверка | Результат | Доказательство |
|---|---|---|
| Финальный image, полный regression suite | **1010 passed**, 0 failures/errors/skips, 273.47 с | `evidence/final-image-suite.xml/.log` |
| Из них inventory-prefixed tests, включая history и deployment | 214 passed | JUnit classname; это более широкая группа, чем прежние 181 inventory tests |
| Host full suite той же версии | 1008 passed, 2 failed, 258.95 с | `evidence/final-suite.xml/.log` |
| Независимая базовая проверка ship deadlock на чистом fc0662a | Воспроизведён с первого запуска | `evidence/base-kiz-deadlock.log` |
| Независимая базовая проверка transfer deadlock на чистом fc0662a | Воспроизведён на шестом запуске | `evidence/base-transfer-deadlock.log` |
| Swagger после двух descriptions | 18 passed | `evidence/swagger-fixed.log` |
| Два новых integration tests | Прошли в полном suite и отдельно на копии production schema | JUnit, schema-compatibility.log |
| Black / Ruff нового test-файла, staged whitespace | Passed | Повторная адресная проверка; полный форматирующий рефакторинг upstream DTO не выполнялся |
| Проверка Docker app hashes, отсутствие .env/docs/tests/scripts в образе | Passed | `evidence/image-metadata.json` |

Два host failures: `test_concurrent_ship_and_assignment_preserve_touch_protocol` и `test_concurrent_transfer_and_assignment_preserve_touch_protocol`. Оба независимо воспроизводятся без inventory overlay на fc0662a. Финальные 1010 passed в образе **не доказывают устранение недетерминированных deadlocks**. Их не исправляли; журналы не скрыты и остаются частью manifest.

Отдельное окружение брокера и все реальные процессы workers не запускались. Consumer tests используют тестовый транспорт и реальные SQL paths. Новое нагрузочное испытание 5338 адресов не повторялось — это задача интеграции релиза, а не повтор Stage 1 acceptance. Текущие production privileges/partitions/физическая готовность адресов не подтверждены этим прогоном.

## 6. Ручная фиксация и staging deployment

1. Проверить `SHA256SUMS`, manifest и staged diff в отдельном worktree. Сверить `git write-tree` с указанным source_tree. При любых изменениях исходников повторно тестировать затронутые сценарии и выпускать новый manifest.
2. Вручную создать commit на release-ветке после review. Рекомендуемый текст: `feat(inventory): prepare unified Stage 2A release with KIZ and FBS integration`. Убедиться, что `HEAD^{tree}` равен `dc4aacec5347d7ba68a25015d7e7bbb595284549`.
3. Создать **новый** immutable manifest с настоящим release_commit и тем же деревом; текущий manifest не переписывать. Сохранить/tag/push только после отдельного решения владельца репозитория. Последующие изменения отчётов также меняют tree.
4. Загрузить сохранённый image archive, сверить image ID; опубликовать этот же image в согласованный registry и записать digest в новый manifest. Docker image ID и registry manifest digest — разные идентификаторы.
5. В staging выполнить актуальные schema/projection/roles/ledger/partitions preflight. До установки guards закрыть точные privilege prerequisites. Не менять ledger вручную. SQL применять лишь к неприменённой схеме по runbook, с полным rollback при ошибке.
6. Развернуть **единый artifact** для API, standard/external FBS consumers, task-level/multi-source FBS, retry workers и KIZ import consumer. Проверить точный roster процессов и настройки modes/queues; старые FBS binaries не совместимы с активным freeze. Staging queues должны быть изолированы от production.
7. Начать с трёх flags=false и smoke старых writers. Затем MODULE, контролируемый FREEZE при APPLY=false, FBS invalidation/retry, явное release и восстановление после перезапуска. Только после отдельного допуска включать APPLY для малого обычного partial pilot; не для ФБС-зоны, mixed КИЗ, containers и неоднозначных batches.

## 7. Оставшиеся условия и безопасное отключение

Блокируют production pilot: отсутствие финального release commit/registry digest; непроверенные текущие production projection/roles/default ACL/sequence prerequisites; готовность 2027 partitions; подтверждённая единая версия всех FBS процессов; staging с реальным изолированным брокером и утверждённым freeze SOP. По readiness 02.11 отмечены manager51 и широкие default ACL — их актуальность и допустимость должен подтвердить владелец БД. Эта задача не устраняла их автоматически.

Безопасное отключение: запретить новый apply через APPLY=false, дождаться текущих transactions, выключить новые freeze-команды, **совместимой версией явно освободить активные блокировки с аудитом**, затем MODULE=false и только после проверки отсутствия freezes рассматривать rollback binaries. Выключение флага само по себе не снимает сохранённые DB-блокировки. Не удалять уже applied movements, registry/component links или историю. DB rollback после первых физических применений не равен откату приложения и требует отдельной процедуры.

**Дальнейшее согласование:** изменения физической бизнес-логики для объединения веток не потребовались. Требуется ручная фиксация точного кандидата и отдельное разрешение на deployment; production не изменён.
