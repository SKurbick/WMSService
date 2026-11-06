# Inventory Stage 2B — реализация и проверки, 05.11.2026

## Итог

Реализованы opt-in инвентаризация КИЗ/QR, история сканов/повторных counts и атомарное
применение технически однозначных holder/contents исправлений через существующие
controlled services. **Финальная регрессия: 1050 passed, 0 failed/errors/skipped** на
disposable PostgreSQL 17.4, 291.84 с. Это результат backend-тестирования, не production
deployment и не отмена оставшихся эксплуатационных условий Stage 2A.

Разработка выполнена в основном каталоге на `feature/inventory-stage2b`, созданной от
интегрированного `9f3202c9a1922a24fda717dddedc13977c444ea9`. Исходное дерево было чистым.
Релизный кандидат/его commits не переписаны; автоматического commit, push, production
миграций, RabbitMQ изменений или включения реальных flags нет.

Точное описание: [inventory_stage2b.md](../flows/inventory_stage2b.md).

## Реализовано

| Область | Результат |
|---|---|
| КИЗ | Immutable исходный snapshot, общий prefix normalization, конкретные сканы с location/holder, missing/unknown/duplicate/lifecycle/placement conflicts |
| Пересчёты | Start/end evidence, author/time/revision/freeze token, progress через append-only scans, completed/superseded история, обязательная новая попытка после return |
| Контейнеры | QR snapshot, собственное содержимое по SKU, explicit contained КИЗ, сравнение contents/projection/status/location, missing/unknown/duplicate conflicts |
| Коррекции | Active loose КИЗ transfer внутри frozen scope; flat container move; same-address fill/extract с доказанной loose capacity и scanned identity set |
| Совместный учёт | Unidentified loose отдельно от identified units и собственного contained quantity; изменения содержимого учтены в planned loose expected |
| Apply | Одна application transaction, existing services на том же connection/savepoints, action identity, controlled operation IDs, movement_registry FK links |
| Защиты | Fence/revision/count freshness, NOWAIT prelocks, post-effect holders/contents/unidentified checks, deferred completeness и полный rollback |
| Stage 1/2A | Существующие quantity workflow, small-partial limits, flags, freeze/FBS exception и строгий source-specific adjust сохранены |

Новый application response имеет additive `identity_actions`; `movements` включает как
старые inventory-source adjustments, так и связанные controlled transfer/container legs.
Источники этих последних движений сохраняют существующую семантику `kiz_operation` и
`container_operation`. Связь с документом — application → action → registry, плюс plan,
scanned codes и штатные operation items/KIZ links.

## Поддерживаемые решения и ручные случаи

- Исправление active loose КИЗ в неверной ячейке: explicit transfer ровно выбранных codes,
  обоих frozen адресов и существующего product. Реальный inventory эффект проверен.
- Flat QR в другой ячейке: move всего контейнера с подтверждёнными собственными contents
  и КИЗ. Проверяются warehouse, status, contained projection и финальный holder.
- Больше/меньше единиц в контейнере при соответствующем loose count: controlled fill/extract
  того же адреса; для identified units нужны конкретные scanned codes. Количество товара
  сохраняется, двойного прихода/списания нет. Излишек неизвестного происхождения не
  считается допустимым fill при отсутствии source capacity.
- Обычная shortage/surplus другого однозначного unidentified scope применяется прежним
  strict inventory-source adjust в той же общей транзакции.
- Missing/unknown/terminal/holderless registered КИЗ, missing/unknown QR, неподтверждённый
  holder, batch/empty key, content projection drift, смешанная quantity-коррекция active
  SKU и неочевидный обмен КИЗ между контейнерами блокируют документ целиком.

Для blockers сохранён конкретный workflow: return → explicit release → установление
физической причины и существующие controlled операции по разрешённому сценарию → новый
freeze confirmation/epoch и все финальные counts. Если безопасная корректировка не
определена — close without adjustment. Нет endpoint, который просто помечает расхождение
решённым и выдаёт несуществующий физический эффект за applied.

**Вложенные контейнеры не объявляются реализованной physical-функцией:** действующая
схема имеет валидный CHECK `chk_container_flat`, запрещающий parent_container_id. Тест
доказал отказ создания вложенного контейнера. Read-model разворачивает legacy ancestry
и собственные contents без повторного суммирования; unsupported hierarchy блокирует
применение. CHECK и общая модель контейнеров не менялись. Для реальной вложенности нужны
новый controlled container contract и отдельная проверка ancestor writer admission.

Registered без holder не приписывается произвольной локации. Ни создание/удаление КИЗ,
ни terminal-to-active смена lifecycle не выполняются inventory SQL или quantity adjust.
Состояния shipped/deactivated обрабатываются как terminal conflicts; отдельный новый
scan-case тестировался для deactivated, ship writer проверен общим regression suite.

## SQL, транзакции и безопасность

Новая migration: `scripts/migrations/20261105_add_inventory_identity_counts.sql`.
Новые таблицы:

1. `wms.inventory_identity_config`.
2. `wms.inventory_identity_snapshots`.
3. `wms.inventory_identity_counts`.
4. `wms.inventory_identity_scans`.
5. `wms.inventory_identity_actions`.
6. `wms.inventory_identity_movement_links`.

PK/FK, fixed-location FK сканов, immutable guards, one-open-count unique index и deferred
application/action/link checks установлены и реально проверены миграционными tests.
Снимок фиксируется в той же REPEATABLE READ start transaction, что и Stage 1 stocks/claims.

Не изменены старые migration-файлы, inventory/movements таблицы, projection function,
КИЗ lifecycle функции, controlled container функции, movement types и публичный
POST /api/movements. Из shared SQL меняется только admission:
`inventory_check_location_write` получает дополнительный ограниченный identity context.
Поддержанное исходное body md5: `aca60cf9d0cc472df2c334adf4f983eb`, проверяются также
language/return type/security/config/volatility/parallel attributes. При drift migration
целиком откатывается, существующий установленный body остаётся исходным.

Identity bypass требует реальную applying application того же xid, active server-derived
action, готовый актуальный freeze и локацию документа. Обычные writers/FBS без этого
контекста используют прежнюю ветку. FBS продолжает атомарно инвалидировать frozen scope.
Нет открытой PostgreSQL transaction между запросами физического пересчёта.

Для identity apply локации берутся сразу FOR UPDATE NOWAIT, затем containers/contents,
inventory и КИЗ — NOWAIT. Это исключает позднее повышение location KEY SHARE за FBS
FOR SHARE. Изолированный regression подтвердил быстрый 409 и успешный retry того же UUID.
Legacy Stage 2A mode сохраняет прежний KEY SHARE протокол.

Controlled services используют один outer connection. Любая ошибка, включая deferred
commit failure и неверный AFTER projection effect, откатывает физический эффект,
operation headers/items, registry, links, application и успешную историю/освобождение.
Существующий отдельный аудит отказа freeze может сохраняться как факт неуспешной команды.

Deployment privileges проверяются `inventory_identity_runtime_privileges()`: default
check-only, scoped grants лишь при явном разрешении владельца в deployment-сессии.
Проверяются existing controlled table/sequence prerequisites; helper не расширяет их
молча. Точечный EXECUTE на четыре existing controlled functions и новые identity object
privileges выдаются только указанному inventory profile, admission EXECUTE — всем writers.
Non-owner tests выполнили transfer/move/fill/extract/apply; новые immutable tables не
получили лишний UPDATE. Новой SECURITY DEFINER функции нет.

Post-install read-only script:
`scripts/migrations/20261105_inventory_identity_privileges_preflight.sql`.
READ ONLY, statement_timeout=10s, lock_timeout=1s, grant mode off; проверен тестом.
Не запускать изменённую migration повторно поверх уже установленной старой версии.

## Новые HTTP операции

Префикс `/api/inventory-documents`, общий version/UUID/error protocol:

| Method / suffix | Назначение |
|---|---|
| POST `/{document_id}/identity-accounting` | Однократный opt-in в draft; containers flag |
| GET `/{document_id}/identities` | Исходный snapshot и пагинированные попытки/сканы/results |
| POST `/{document_id}/identity-counts` | Новая count attempt и её start evidence |
| POST `/{document_id}/identity-counts/{count_id}/scans` | Append скана/own contents в текущую попытку |
| POST `/{document_id}/identity-counts/{count_id}/complete` | End evidence, conflicts и controlled plan |

Application-preview/ready/apply/application/history и loose counts повторно используются.
Всего inventory operations в OpenAPI теперь 32; API map и русские summaries/descriptions
синхронизированы. Старые URL не удалены и не переименованы.

## Изменённые файлы

Новые: `app/core/schemas/inventory_identities.py`,
`app/core/services/inventory_identity_service.py`,
`app/infrastructure/database/queries/inventory_identities.py`, указанная migration и
preflight в `scripts/migrations/`, `tests/test_inventory_identities_postgres.py`,
`docs/flows/inventory_stage2b.md` и этот отчёт.

Изменены: `app/api/v1/endpoints/inventory_documents.py`,
`app/core/schemas/inventory_documents.py`, `app/core/services/inventory_document_service.py`
и `app/core/services/inventory_application_service.py`;
tests/conftest.py, test_inventory_deployment_postgres.py, test_inventory_documents_unit.py;
docs/current/api_map.md, business_rules.md, current_state.md, open_questions.md;
docs/decisions/decisions.md, scripts/migrations/README.md.

Новый тестовый flag KIZ_TEST_IDENTITY_SCHEMA=1 добавляет migration в disposable template,
чтобы весь regression suite проверял legacy writers с новым admission function, а не
только отдельно установленные Stage 2B tests. Production flags/config не менялись.

## Проверки и артефакты

- Полный итоговый suite: **1050 passed**, 66 существующих Pydantic deprecation warnings,
  291.84 с; errors/failures/skips=0. Из них 35 новых Stage 2B PostgreSQL cases и 249
  inventory-prefixed tests с history/deployment и прежними Stage 1/2A.
- Проверены HTTP snapshot/count/progress/history, missing/found/duplicate/lifecycle,
  normalization, holder/container transfer, fill/extract, mixed loose+КИЗ+container,
  scope/bigint validation, stale count/return/freeze epoch, FBS invalidation, idempotency,
  concurrent saves/apply, last-row/deferred rollback, unexpected projection effect,
  non-owner operations, read-only preflight и unsupported-function migration rollback.
- Black --check для всех 11 затронутых Python файлов: passed.
- Ruff для изменённых Python файлов: passed. Полный Ruff app имеет семь замечаний в
  неизменённых locations.py, operations_history_service.py, system_service.py,
  re_sorting_operation_repository.py и retry_worker.py. Они не исправлялись.
- git diff --check: passed.

Во время предыдущего полного запуска воспроизведён старый
`test_concurrent_transfer_and_assignment_preserve_touch_protocol` deadlock: 1048 passed,
1 failed. Независимо получен с **первой попытки на чистом 9f3202c**, восстановленном из
git archive в /tmp, без Stage 2B. Финальный зелёный прогон не означает устранение этой
недетерминированной legacy проблемы. Новый lock-upgrade regression относится к другому
сценарию и не меняет legacy transfer/assignment код.

Локальные evidence:

- `/tmp/wms-stage2b-regression.xml`, SHA-256 `c81eb1000e3c440c8776e7941cd5c9ac04faf19c809a4f1166a5d673f4b3cb14`.
- `/tmp/wms-stage2b-regression.log`, SHA-256 `1150be366a20f735ddab0dfa6a5cf94d3c95fbdcfa3df76e3b82cb93f4f7d705`.
- `/tmp/wms-stage2b-baseline-deadlock.log`, SHA-256 `0dea9194d30c877ad787f8c2137698333942156bde62a872c67c3415bd35723a`.
- `/tmp/wms-stage2b-lock-upgrade.log` — отдельный successful lock/retry regression.
- `/tmp/wms-stage2b-source-files.json` — 389 исходных tracked/untracked файлов до этого
  отчёта, SHA-256 manifest `31f8fbdb5b7b42251dd3536f51003d8b8ae3e123ca83444cd94d73a477b977ae`.

Migration SHA-256: `d4a7cb3744c5db1f405d07eb481374ca6580308d46b9126269a436ee94ebf78d`.
Preflight SHA-256: `ab990e7603b93635ee3320339fbd51b6a5e4fd16239f07e902af746007ac9784`.

Тесты выполнялись на отдельном wms-inventory-stage2b-pg, localhost:55456, PostgreSQL
17.4, host Python 3.12.3. Fixture databases disposable; production и RabbitMQ не
подключались для записей. Реальный staging broker/process deployment не проверен.

## Оставшиеся условия

Перед staging: review новых source/SQL, фиксация версии по отдельному разрешению,
сборка нового образа/manifest, актуальный projection/admission/roles/partitions preflight,
проверка всего FBS roster и операционного SOP. Ранее сохранённый Stage 2A image не
содержит этот незакоммиченный Stage 2B и не является его deployment artifact.

Перед завершением поддержки всех расширенных сценариев остаются controlled nesting,
mixed active-SKU quantity correction/identity exchange и явные domain-specific решения
по отсутствующим/terminal идентификаторам. Сейчас они имеют безопасный manual workflow,
но не выдаются за реализованный автоматический physical apply. Batch allocation,
автоматическая ФБС-инвентаризация и масштабирование сознательно не развивались.

Отключение сохраняет протокол Stage 2A: APPLY=false, завершить начатые transactions,
прекратить новые freezes, совместимой версией explicit release активных fences с аудитом,
затем MODULE=false/rollback приложения. Флаги не снимают fences автоматически; уже
applied movements и историю нельзя удалять для имитации отката.
