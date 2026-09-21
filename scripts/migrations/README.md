# Миграции WMSService

Статус: `CURRENT` для файлов, находящихся в этом репозитории. Факт применения в конкретном окружении необходимо проверять в PostgreSQL.

Alembic и автоматический migration runner в проекте отсутствуют. SQL применяется внешним процессом или владельцем БД. Все изменения сначала проверяются на stage и выполняются до запуска версии приложения, которая от них зависит.

## Найденные файлы

| Порядок | Файл | Назначение |
|---:|---|---|
| 1 | `../migrations.sql` | Таблицы и view мягких резервов |
| 2 | `20260614_add_fbs_shipment_source.sql` | `fbs_shipments.source` и индексы |
| 3 | `20260707_add_kit_operations.sql` | Source-поля movements, allow-list и kit operations |
| 4 | `20260715_add_re_sorting_operations.sql` | Re-sorting tables, constraints, indexes и audit views |
| 5 | `20260906_add_kiz_v1.sql` | KIZ v1 registry, audit и guards |
| 6 | `20260910_movement_registry_preflight.sql` | Read-only preflight movement identity |
| 7 | `20260910_add_movement_registry.sql` | Stable movement registry, trigger, backfill и integrity |
| 8 | `20260910_kiz_phase2_preflight.sql` | Read-only проверка KIZ v1 и Phase 1 prerequisites |
| 9 | `20260910_add_kiz_movement_links.sql` | Immutable KIZ links, shipped-ready lifecycle и events |
| 10 | `20260910_kiz_phase3_preflight.sql` | Read-only prerequisites KIZ operation infrastructure |
| 11 | `20260910_add_kiz_operations.sql` | Transaction-owned KIZ transfer/ship idempotency |
| 12 | `20260910_kiz_phase4_preflight.sql` | Read-only проверка prerequisites/integrity Phase 4 |
| 13 | `20260910_add_kiz_transfer.sql` | Controlled KIZ location transfer protocol |
| 14 | `20260911_kiz_phase5_preflight.sql` | Read-only проверка prerequisites/integrity Phase 5 |
| 15 | `20260911_add_kiz_ship.sql` | Controlled KIZ shipment lifecycle protocol |
| 16 | `20260915_container_b1_preflight.sql` | Read-only проверка совместимости container B1 |
| 17 | `20260915_add_container_b1_contract.sql` | Stable identity, flat model и единый container contract |
| 18 | `20260915_container_b21_fill_preflight.sql` | Read-only проверка prerequisites/invariants fill |
| 19 | `20260915_add_container_b21_fill.sql` | Idempotent loose-to-container fill protocol |
| 20 | `20260916_container_b22_extract_preflight.sql` | Read-only проверка prerequisites/invariants extract |
| 21 | `20260916_add_container_b22_extract.sql` | Controlled partial/full container extract |
| 22 | `20260916_container_b3_move_unpack_all_preflight.sql` | Read-only проверка prerequisites/invariants B3 |
| 23 | `20260916_add_container_b3_move_unpack_all.sql` | Controlled move и unpack-all |
| 24 | `20260917_container_b4_final_preflight.sql` | Read-only проверка consistency/provenance перед B4 |
| 25 | `20260917_add_container_b4_final.sql` | Generic container guard и удаление legacy DB writers |

Порядок выше отражает хронологию и зависимости файлов репозитория, но не доказывает, что целевая БД начинает с состояния, совместимого с первым файлом. Перед применением нужно сравнить runtime schema с ожидаемыми объектами каждого SQL.

## Важные ограничения

- `docs/archive/snapshots/wms_schema.sql` — snapshot, который уже содержит часть объектов из ранних миграций, но не содержит re-sorting. Его нельзя затем безусловно дополнять всеми файлами как чистую новую БД.
- `scripts/migrations.sql` находится вне датированного каталога, хотя является миграцией stock reservations.
- Миграция FBS source от 2026-06-14 первоначально разрешает только `standard` и `external_detected`. Текущий код также пишет `http_api`; расширение constraint в репозитории отдельной датированной миграцией не найдено и описано как ручная операция владельца БД.
- Re-sorting migration содержит `CREATE TABLE` без `IF NOT EXISTS`; повторное применение не является гарантированно безопасным.
- Rollback-скрипты в репозитории не найдены.
- Для `wms.movements` должна существовать партиция, охватывающая дату выполнения операций.

## Перед применением

1. Сделать backup и проверить восстановление.
2. Снять schema-only dump целевого окружения.
3. Проверить уже существующие tables, columns, constraints, indexes, views и functions.
4. Проверить активную партицию `wms.movements`.
5. Выполнить SQL транзакционно там, где это предусмотрено самим файлом.
6. Проверить constraints и smoke-сценарии приложения до переключения трафика.
7. Зафиксировать примененный файл, время, окружение и ответственного во внешнем журнале миграций.

## Не подтверждено репозиторием

- точный bootstrap новой WMS database;
- создание extension/type `ltree`;
- актуальная версия production/stage schema;
- применено ли ручное расширение `chk_fbs_shipments_source` для `http_api`;
- механизм блокировки от одновременного применения одной миграции;
- штатный rollback.

## 5. KIZ v1 — 20260906_add_kiz_v1.sql

Применяется вручную после проверки владельцем БД, до deployment кода KIZ.
Создаёт kiz/kiz_events, constraints/indexes и три guard functions/triggers.
Inventory/movement существующие функции не заменяет. Backfill отсутствует.
Скрипт BEGIN/COMMIT, lock_timeout=10s; повторное применение не поддерживается.

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/migrations/20260906_add_kiz_v1.sql
```

Сначала проверить stage, актуальную partition movements и права runtime-role
(включая SELECT новых таблиц для guard, identity sequence privileges и KIZ write privileges).
В рабочей БД скрипт не применялся агентом. [Проверка runtime](../../docs/database/kiz_v1_runtime_check.md).

Down runner в проекте отсутствует. Автоматического DROP KIZ/audit не предлагается.
При откате приложения сохранять данные и guard: старый destructive recalculate с active
KIZ несовместим. Для rollback deployment требуется отключить KIZ write endpoints и
maintenance вызовы старого пересчёта, затем отдельно согласовать дальнейшую стратегию.

## 6. KIZ Stage 2A Phase 1 — movement registry

`20260910_movement_registry_preflight.sql` — read-only duplicate/volume/partition check.
`20260910_add_movement_registry.sql` добавляет composite movement UNIQUE, stable registry,
FK, automatic registration, immutable mapping, batch backfill и integrity function.

Migration не выполняет исторический backfill внутри long transaction. После её применения
вызывать `SELECT wms.backfill_movement_registry(10000)` отдельными транзакциями до `0`,
затем требовать `is_complete=true` от `wms.check_movement_registry_integrity()`.
Точный runbook: [KIZ Stage 2A Phase 1](../../docs/flows/kiz_stage2a_phase1.md).

## 7. KIZ Stage 2A Phase 2 — association

`20260910_kiz_phase2_preflight.sql` выполняется read-only поверх применённых KIZ v1 и
Phase 1. Затем владелец БД вручную применяет `20260910_add_kiz_movement_links.sql`.
Migration транзакционна и fail-fast по lock timeout; historical backfill не нужен.
Она создаёт immutable links, расширяет lifecycle/events и делает current location
nullable только в согласованном shipped состоянии. Публичных write endpoints нет.
[Phase 2 runbook](../../docs/flows/kiz_stage2a_phase2.md).

## 8. KIZ Stage 2A Phase 3 — idempotent operation infrastructure

Сначала выполнить read-only `20260910_kiz_phase3_preflight.sql`, затем вручную
`20260910_add_kiz_operations.sql`. Migration только создаёт две operation tables,
constraints и guards. Она не меняет movements/inventory/KIZ/links и не требует backfill.
[Phase 3 runbook](../../docs/flows/kiz_stage2a_phase3.md).

## 9. KIZ Stage 2A Phase 4 — explicit transfer

Сначала выполнить read-only `20260910_kiz_phase4_preflight.sql`, затем вручную
`20260910_add_kiz_transfer.sql`. Migration добавляет только transaction-local
authorization и controlled DB function/guard; backfill не нужен. Если runtime role не
является владельцем функции, выдать ей только EXECUTE на `wms.transfer_kiz_location`.
[Phase 4 runbook](../../docs/flows/kiz_stage2a_phase4.md).

## 10. KIZ Stage 2A Phase 5 — explicit shipment

Сначала выполнить read-only `20260911_kiz_phase5_preflight.sql`, затем вручную
`20260911_add_kiz_ship.sql`. Migration добавляет transaction-local shipment
authorization, controlled function, guard branch и deferred completeness trigger.
Backfill не нужен. Runtime role выдаётся только EXECUTE на `wms.ship_kiz`.
[Phase 5 runbook](../../docs/flows/kiz_stage2a_phase5.md).

## 11. Container Stage 3B Phase B1

Сначала вручную выполняется read-only `20260915_container_b1_preflight.sql`, затем —
`20260915_add_container_b1_contract.sql`. Migration нормализует legacy `opened` в
`open`, согласует status/type allow-list, запрещает nesting/NULL location/QR rename и
hard delete, а content scope делает `UNIQUE NULLS NOT DISTINCT`.

Файл транзакционный, `lock_timeout=10s`. Он не меняет physical quantity, не создаёт
movements и не применялся агентом к production. Порядок и smoke-checks описаны в
[B1 runbook](../../docs/flows/container_stage3b_phase_b1.md).

## 12. Container Stage 3B Phase B2.1

После применённой B1 сначала выполнить read-only
`20260915_container_b21_fill_preflight.sql`, затем вручную —
`20260915_add_container_b21_fill.sql`. Migration создаёт container-specific operation
graph, controlled fill contents support и добавляет lock в legacy unpack compatibility
function. Она не выполняет business fill и не меняет существующий stock.

Production apply агентом не выполнялся. Полный порядок и smoke checks:
[B2.1 runbook](../../docs/flows/container_stage3b_phase_b21_fill.md).

## 13. Container Stage 3B Phase B2.2

После применённой B2.1 сначала вручную выполнить read-only
`20260916_container_b22_extract_preflight.sql`, затем —
`20260916_add_container_b22_extract.sql`. Migration расширяет operation type значением
`extract`, добавляет controlled partial/full current-content mutation и обновляет
commit-time graph validation для зеркальной пары movements. Она не выполняет business
extract и не меняет существующие physical quantities.

Production apply агентом не выполнялся. Полный порядок, rollback и smoke checks:
[B2.2 runbook](../../docs/flows/container_stage3b_phase_b22_extract.md).

## 14. Container Stage 3B Phase B3

После применённой B2.2 сначала вручную выполнить read-only
`20260916_container_b3_move_unpack_all_preflight.sql`, затем —
`20260916_add_container_b3_move_unpack_all.sql`. Migration расширяет operation graph,
controlled move authorization/legacy trigger branch и unpack-all reuse B2.2 mutation.
Она не выполняет business move/unpack и не меняет physical quantities.

Production apply агентом не выполнялся. [B3 runbook](../../docs/flows/container_stage3b_phase_b3_move_unpack_all.md).

## 15. Container Stage 3B B4 Final

После применённой B3 вручную выполнить `20260917_container_b4_final_preflight.sql`,
затем `20260917_add_container_b4_final.sql`. Migration не меняет physical quantities:
она добавляет container movement provenance guard, удаляет legacy move/unpack DB writers
и делает register empty-only. Production apply агентом не выполнялся.
[Runbook](../../docs/flows/container_stage3b_phase_b4_final.md).

## 16. Container Stage 3C C1 — KIZ inside containers

После B4 вручную выполнить read-only `20260918_container_c1_kiz_preflight.sql`, затем
`20260918_add_container_c1_kiz.sql`. Migration добавляет container holder, controlled
holder authorization, deferred final integrity и container-aware KIZ inventory guard.
Она не создаёт movements и не изменяет business stock. Production apply выполняет владелец
БД. [C1 runbook](../../docs/flows/container_stage3c_phase_c1.md).
