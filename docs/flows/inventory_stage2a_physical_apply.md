# Inventory Stage 2A — адресный freeze и physical pilot

Дата: 30.10.2026. Ветка `feature/inventory-stage1`, исходный HEAD `8aa94e4`.
Текущие B.1/B.2 и preparation-изменения сохранены, автоматических commits/deployment нет.
Production не подключалась. Этот документ заменяет ограничение «writer отсутствует»
из исторического [preparation-отчёта](inventory_stage2a_preparation.md).

## Поддержанный pilot и предел гарантий

Рабочий путь: небольшой **partial** документ, по умолчанию до **20 location_id**, только
available-россыпь с одной однозначной NULL-batch/NULL-container physical row на пару
SKU/location либо с отсутствующей строкой для приходного излишка. Decimal, включая
дробные значения; фактическое количество должно помещаться в numeric(10,2).

Полная инвентаризация и большие partial-документы Stage 1 продолжают работать без
freeze/apply. Ни start, ни location claims не блокируют склад. Freeze начинается только
отдельной командой; его область равна всей зафиксированной области данного документа,
независимо от SKU-фильтра. Нет глобальной блокировки склада или RabbitMQ.

Для physical apply отклоняются: full/слишком большой scope, неактивные адреса, ФБС-зона
и её LTREE descendants, контейнерные/empty-container остатки, batch/NULL-empty ambiguity,
active КИЗ в локации, неподтверждённый relevant КИЗ-контекст, незавершённые связанные
warehouse tasks (включая active tasks без определимого адреса), непересчитанные новые
положительные пары, старый count epoch,
invalidated freeze и изменившиеся relevant evidence. Потери не распределяются по партиям.
Метка can_apply в исторической подготовке не заменяет повторную проверку при approval.

**Операционная гарантия отличается от технической.** Сотрудник подтверждает текстом,
именем и временем, что физические действия завершены/отражены и не осталось неизвестных
операций. Сервер сохраняет подтверждение, но не объявляет его доказательством отсутствия
неизвестных физических действий. DB guards запрещают записи, а не физический доступ к
полке. Пилот допустим только при реальном соблюдении адресной остановки работ и проверке
границы оператором. Нельзя выводить физическое время из movement.created_at/ID.

ФБС-зона и active-КИЗ источники исключены из auto-apply; сами ФБС-списания продолжаются.
Для источников с недоказуемыми delayed physical effects расширение pilot не разрешено.
Физическое действие, которое вообще не сообщено WMS, технически обнаружить невозможно.

## Пользовательский workflow

1. Создать/start partial документ обычными Stage 1 командами; уточнить scope до start.
2. POST `/{id}/freeze`: состояние freezing. Новые адресные изменения запрещены, долговечные
   DB fences не держат транзакцию между HTTP-запросами. Запущенный SQL writer либо уже
   закончил транзакцию, либо команда freeze получает 409 и может быть повторена.
3. GET `/{id}/freeze`: проверить конфликты и урегулировать физические операции. Связанные
   задачи перечисляются с учётом parent tasks. Pending/assigned task можно отменить;
   in-progress работу, которую нельзя безопасно завершить под freeze, урегулировать через
   release → завершение/отражение → новый freeze. Автоматического drain/исправления задач нет.
4. POST `/{id}/freeze/confirm` с `unrecorded_operations_resolved=true`, `confirmation`
   (30–2000 символов) и обычными UUID/version/user_name. Создаётся epoch и baseline,
   все строки требуют финального пересчёта. Старые незавершённые attempts становятся
   cancelled; их история сохраняется. Завершённые попытки не переписываются.
5. Начать/завершить новые attempts существующими endpoints. В evidence сохраняется
   freeze_id/epoch. Ранее начатый или сделанный до freeze пересчёт для apply не подходит.
6. Submit → application-preview. Подготовка использует **frozen expected**, initial snapshot
   остаётся неизменным. Любой blocker делает весь документ неприменимым.
7. POST `/{id}/freeze/ready` с submitted_revision. Повторно проверяются revision, counts,
   scope и evidence. Состояние ready_to_apply. Использовать обновлённую версию документа.
8. POST `/{id}/apply` с UUID, expected_version, submitted_revision, user_name. При успехе:
   applied, movements/registry links, release freeze и claims одной транзакцией.
9. GET `/{id}/application` и обычная история доступны для проверки результата.

После application-preview перечитать документ через GET: observed_document_version
относится к наблюдению до version bump. На submitted revision хранится одна неизменяемая
подготовка. Для другого результата/нового наблюдения нужны return-to-count и новый submit.
Возврат из ready сохраняет физический freeze, переводит его в frozen и требует выбранные
новые counts. После FBS invalidation нужны return-to-count → confirm с новым epoch →
финальные counts всех строк → submit/preview/ready. Автоматической правки actual нет.

Отмена: POST `/{id}/freeze/release` с reason. Снимает блокировку, сохраняет событие и
требует пересчёт, **не** создаёт movements и **не** освобождает claims. Затем документ
можно закрыть существующим close-without-adjustment. Закрытие с активным freeze запрещено.
Таймера автоматического release нет; crash/restart не снимает блокировку.

Пример: initial=10, старый count=8, до freeze поступило 3. Финальный count под freeze=11,
frozen expected=13 → adjust -2, WMS=11. Initial discrepancy +1 из Stage 1 не является
величиной разрешённого движения. Движение после начала freeze либо запрещается, либо
является подтверждённым ФБС и инвалидирует окно.

## PostgreSQL protocol и writers

`inventory_location_fences` имеет постоянную строку на каждый существующий/новый адрес.
Свободные ordinary writers берут FOR SHARE NOWAIT; друг другу они не мешают. Freeze
берёт эти строки FOR UPDATE NOWAIT в порядке location_id и меняет generation/pointer.
Старая REPEATABLE READ транзакция после freeze не может увидеть свободный старый fence
и успешно записать: locking read получает serialization failure. Предварительного SELECT
в Python в качестве защиты нет. Advisory locks старых writers не считаются общим барьером.

| Writer | DB boundary / поведение |
|---|---|
| receive, transfer (оба конца), manual adjust, ship, kit/re-sort, putaway/pick movements | BEFORE row guard на movements, включая partition clones; frozen address отклоняется до projection |
| legacy/direct projection, КИЗ touch, maintenance UPSERT/DELETE | BEFORE guard на inventory; отсутствие row также защищено постоянным location fence |
| КИЗ activation/transfer/deactivation/ship, holder change | BEFORE guard на kiz по old/new location и container holder |
| controlled fill/extract/move/unpack, container state | guards на containers, container_contents и возникающих inventory/movements/КИЗ |
| location edit/delete | guard по location_id; structural edits предков используют fences всех затронутых descendants по parent/path; новые descendants не расширяют fixed scope |
| warehouse tasks/items | guards по old/new source/destination, item source и ancestor task locations; безопасная отмена pending/assigned разрешена |
| maintenance/recalculate | существующая транзакция SHARE movements → EXCLUSIVE inventory сохранена; запись frozen inventory откатывает весь maintenance |
| TRUNCATE основных физических parent tables | statement guard отклоняет при активных fences; административное отключение guards/DDL не входит в модель безопасности |

У ordinary writers NOWAIT необходим: некоторые старые КИЗ writers уже держат physical
rows/locations до вызова SQL guard. Ожидание нового fence создало бы обратный lock order.
Новый конфликт возвращается 409, а исходная транзакция откатывается. Свободные локации
не получают запрета. Legacy KИЗ deadlocks вне pilot этим изменением не исправляются.

### ФБС

Standard/external/HTTP/manual retry/retry worker сходятся в product-group processing.
Реальный FbsShipmentRepository.lock_items_for_processing после блокировки item rows
устанавливает transaction-local shipment/item context через встроенный set_config.
Payloads, source_type старых movements (в том числе NULL), responses и task-level
idempotency не меняются. Не любой ship считается ФБС.

КИЗ-aware multi-source сначала берёт все source fences по location_id, затем все
затронутые freeze headers по freeze_id. Второй порядок предотвращает новый X/Y ↔ Y/X
deadlock для непересекающихся источников, относящихся к двум общим документам.

На свободном адресе ФБС использует SHARE и сохраняет параллельность разных SKU. При
гонке free→frozen savepoint снимает спекулятивный SHARE до перехода к UPDATE: нет
upgrade deadlock двух ФБС writers. На активном адресе guard удерживает fence до конца транзакции, обновляет generation и переводит freeze
в invalidated; событие хранит shipment/location/product. Deferred DB constraint при
commit требует реальное ship-движение **этой транзакции** и success item либо written_off
task result с точной movement coordinate. Событие guard на partition хранит ID/created_at
движения и deferred FK; связанные inventory/КИЗ события подтверждаются этим witness в
той же outer transaction. xmin не используется: у savepoint он отличается от top-level
xid. Trigger family передаётся явно через TG_ARGV, поскольку TG_TABLE_NAME у clone —
имя partition. Одна GUC без эффекта shipment не даёт обхода:
откатываются движение, projection и invalidation. FBS failure без committed stock effect
не превращается в успешное списание; pending/retry сохраняют старое поведение.

Если FBS уже держит fence, approval получает 409 и после commit видит invalidation.
Если approval первым сериализовался на fences, FBS ждёт только короткую SQL-транзакцию,
затем пишет после освобождения. Его эффект не теряется и не включается задним числом
в старый count. Это порядок DB commit, не доказательство времени физического изъятия.
В очереди нет глобального stop/pause и нет правила «FBS запрещён из-за freeze».

## Атомарное применение

READ COMMITTED command: UUID lock → document FOR UPDATE/version → ROW EXCLUSIVE NOWAIT
на movements (исключает текущий maintenance SHARE) → location fences FOR UPDATE NOWAIT
→ freeze header → ordered products FOR UPDATE NOWAIT → locations FOR KEY SHARE NOWAIT
→ inventory rows FOR UPDATE NOWAIT → свежая проверка counts/preparation/evidence.
NOWAIT для physical/FK rows исключает ожидание legacy writer с противоположным порядком.
Нет долгой транзакции на время физического пересчёта и нет глобального writer versioning.

Warehouse tasks/items дополнительно используют короткий shared admission latch через DB
trigger. Confirm/ready/apply берут его exclusive NOWAIT для проверки unscoped task intent:
незакоммиченная или новая неизвестная адресная операция не проходит между проверкой и
commit. Это сериализует короткий участок task metadata; latch не держится во время
физического count и не используется inventory/FBS/public assembly_task writers. Другие
адреса не получают постоянного запрета. Confirm/ready используют READ COMMITTED под
адресными барьерами, чтобы увидеть завершившийся task intent. Наличие активной задачи
без определимого адреса не останавливает WMS, но делает этот pilot неприменимым до
урегулирования неизвестной области.

Затем создаётся unique application, transaction-local application context и все ненулевые
adjust movements. `source_type=inventory_document`, source_id=document_id,
source_item_id=component_id. Component/preparation/approved_revision связаны с application,
movement_ref — FK на существующий registry. Нулевое расхождение не создаёт movement,
но проходит все остальные проверки.

Установленная `update_inventory_from_movement()` адаптируется фиксированным внешним
PL/pgSQL wrapper: inventory source вызывает строгий helper и RETURN, остальные source
выполняют исходный legacy body как вложенный блок. Миграция проверяет поддержанный body
и attributes до замены; неизвестный runtime contract отклоняется целиком. Поиск текстового
BEGIN и проверка имени helper как доказательства исполнения не используются. Legacy
body сохраняется побайтно внутри wrapper, owner/ACL/attributes — через pg_get_functiondef.
Архивная функция не восстанавливается поверх неизвестной runtime-версии. Новая ветка требует provenance текущей application/transaction, ready freeze,
актуальный финальный attempt, exact available/NULL/NULL component, отсутствие КИЗ и
batch/container ambiguity, достаточное количество и существование расходной строки.
Проверяются RETURNING и итоговая physical quantity/число rows, включая AFTER-trigger effect.
Расход 0 удаляет только свой exact row. App layer не выполняет UPDATE inventory.

Application → applied, header → applied, освобождение fences/claims, registry links,
command response и audit коммитятся вместе. Deferred constraints запрещают незавершённую
application и inventory movement без registry/application link. Ошибка последнего movement
или commit откатывает весь физический результат. Отдельный rejection audit после rollback
не является частичным успехом. Отклонённые SQL writers дополнительно пишут RAISE LOG;
их aborted DB inserts не выдаются за сохранённые события.

UUID retry возвращает первоначальный результат после успешного commit; другой payload
с тем же UUID — 409. Уникальность application(document_id), preparation и component/ref
links не позволяет списать повторно. Конкурентные разные UUID со старой version могут
получить 409; после перечитывания applied документа тот же approved revision возвращает
сохранённую application. При потерянном HTTP-ответе повторять исходный UUID/body.

## Миграции и безопасное включение

После предыдущих КИЗ/registry/container миграций:

1. 20261022_add_inventory_documents.sql (Stage 1).
2. 20261029_add_inventory_application_preparations.sql (предыдущая preparation).
3. **20261030_add_inventory_location_freeze.sql** — 3 новые таблицы, fences/guards/tasks,
   lifecycle/history, отменённые attempts. Не создаёт physical writer.
4. **20261030_add_inventory_physical_apply.sql** — расширяет существующие preparations/
   components, добавляет 2 application/link таблицы, applied lifecycle, строгий source branch
   и deferred constraints. movement_type не меняется, нового registry нет.

Новые таблицы: inventory_freezes, inventory_location_fences, inventory_freeze_events,
inventory_applications, inventory_application_movements. Существующие snapshot/count/
preparation/component таблицы используются повторно. Нет backfill physical data и
переписывания исторических movements. Миграции транзакционные, timeouts ограничены;
повторный запуск CREATE не поддерживается. Production агентом не применялись.

Первое развёртывание — с MODULE/FREEZE/APPLY flags=false. DB-first совместим со старым
backend **при отсутствии active freezes**; app-first совместим со старой DB при flags=false
(FBS context использует built-in GUC, multi-source helper проверяет capability).
До первого freeze обновить **все** backend/consumers/retry workers на поддерживающую
context версию и проверить triggers/partitions/permissions на staging. Старый FBS binary
не умеет этот контекст: оставлять его с активным freeze недопустимо.

Включать поэтапно: MODULE=true → FREEZE=true, APPLY=false (dry run + release) → после
приёмки малого pilot отдельно APPLY=true. Значения по умолчанию остаются false.
INVENTORY_PILOT_MAX_LOCATIONS=20; повышение лимита вне проверенного pilot не рекомендуется.
Операторские flags/production RabbitMQ автоматически не меняются.

Безопасное отключение: APPLY=false → запретить новые freeze (FREEZE=false) → оставить
новую версию работающей для FBS/status/release → явно освободить все оставшиеся freezes
с reason → проверить отсутствие активных fences → при необходимости MODULE=false и
rollback приложения. Уже начатая SQL-транзакция может завершиться; flag не отменяет commit.
При flags=false persistent fences не исчезают. Не откатывать старый FBS binary до drain.
Применённые движения, application/registry links и audit сохраняются; rollback приложения
не означает удаления физических движений. Обратные корректировки — отдельный процесс.

Границы доверия: прямой DBA DDL, отключённые triggers, неучтённые внешние writers и
физический обход операторского протокола не покрываются. Runtime schema production ещё
нужно сверить перед ручным deployment; старые аудиты не доказывают её текущую версию.

## Исправления review B.1/B.2 — 02.11.2026

Это исправление deployment contract, без новых endpoint или бизнес-состояний. Исходный
[review](../audit/inventory_stage2a_physical_apply_review_2026-10-30.md) остаётся историческим.
Production не подключалась; flags не менялись.

### Поддержанный projection contract

`20261102_inventory_projection_preflight.sql` — read-only metadata preflight **до**
physical migration, не повторять его как проверку уже установленного wrapper.
Поддержаны только два явно проверенных `prosrc` после удаления внешних пробелов/CR/LF/tab:

| Вариант | MD5 trimmed prosrc |
|---|---|
| Body из runtime-аудита 19.10 / archive 08.08 с двумя исходными line comments | `59757ad997db9dd81274bd13dadc9e4f` |
| Тот же body с удалёнными двумя строками комментариев, остальные bytes сохранены | `e20d6d1d99ebfea866825b07f0644009` |

MD5 — идентификатор поддержанного deployment artifact, не средство авторизации.
Сигнатура: `wms.update_inventory_from_movement()` без arguments, RETURNS trigger,
LANGUAGE plpgsql, обычная function, VOLATILE, PARALLEL UNSAFE, SECURITY INVOKER,
не LEAKPROOF, proconfig IS NULL. Нестандартные attributes, leading comments с BEGIN,
декларации/строковые литералы с BEGIN, неизвестное тело, уже установленный wrapper
отклоняются с P7604. Это намеренный fail-closed контракт, а не универсальный SQL parser.
Расширять allowlist можно только после отдельной проверки фактического DDL и regressions.

После CREATE OR REPLACE миграция сравнивает весь сохранённый prosrc с фиксированным
исполняемым внешним блоком + проверенным nested legacy body. Regression tests дополнительно
исполняют inventory source и missing-row/insufficient/AFTER-projection faults. Наличие имени
helper в комментарии не принимается. Любая ошибка откатывает всю physical migration.

### Явный runtime role roster и минимальные grants

Владелец migrations задаёт **полный фактический** список API/standard/external/HTTP/retry
runtime roles через session GUC `wms.inventory_runtime_roles`: JSON array из объектов
`{"role":"actual_role","profile":"writer"}` или profile `inventory`. Автоматического
обнаружения ролей нет; отсутствие/пустой roster, неизвестная роль/профиль — P7605.
Роли не создаются и не ALTER-ятся. Профиль `inventory` нужен API, обслуживающему новые
команды; отдельным consumers достаточно `writer`. Одна общая backend role получает
`inventory`. Inherited privileges учитываются обычными PostgreSQL has_*_privilege.

`inventory_runtime_privileges('freeze'|'apply')` — SECURITY INVOKER, доступна владельцу,
EXECUTE у PUBLIC отозван. По умолчанию это **только проверка**, никаких GRANT. Проверка
встроена в freeze migration до установки guards старых таблиц и в physical migration
до замены projection. Нехватка права прерывает transaction с конкретными role/object/right.
Предыдущие objects/ACL/guards откатываются вместе.

Только явно заданный оператором `wms.inventory_grant_runtime_privileges=on` разрешает
в этой же migration transaction scoped GRANT для перечисленных ролей. Это ручное
согласие на ACL delta, не автоматическое изменение production roles. Нет GRANT ALL,
ALTER DEFAULT PRIVILEGES, schema-wide permissions или нового SECURITY DEFINER.

| Новые objects | writer | inventory API |
|---|---|---|
| inventory_documents | SELECT | SELECT, INSERT, UPDATE |
| inventory_document_filters | — | SELECT, INSERT, UPDATE |
| inventory_document_locations, inventory_document_revisions, inventory_document_stock_components, inventory_document_kiz_snapshot | — | SELECT, INSERT |
| inventory_location_claims | SELECT | SELECT, INSERT, DELETE |
| inventory_document_items, inventory_count_attempts | SELECT | SELECT, INSERT, UPDATE |
| inventory_document_history, inventory_document_commands | — | SELECT, INSERT |
| inventory_application_preparations, inventory_application_components | SELECT | SELECT, INSERT |
| inventory_freezes | SELECT, UPDATE | SELECT, INSERT, UPDATE |
| inventory_location_fences | SELECT, INSERT, UPDATE | SELECT, INSERT, UPDATE |
| inventory_freeze_events | SELECT, INSERT | SELECT, INSERT |
| inventory_applications | SELECT | SELECT, INSERT, UPDATE |
| inventory_application_movements | SELECT | SELECT, INSERT |

Locking reads на fences/freezes требуют UPDATE помимо SELECT. INSERT fences нужен даже
обычному location INSERT при выключенных flags. SELECT application/preparation/count
зависимостей нужен invoker guards; INSERT/UPDATE applications writers не выдаются.
На identity sequences выдаётся только USAGE и только для таблиц, где профиль имеет INSERT:
writer — event_id sequence; inventory — document/item/attempt/history/preparation/component/
freeze/event/application identity sequences. Имена берутся через pg_get_serial_sequence,
без wildcard GRANT на sequences. Не выдаются UPDATE/DELETE на immutable history/links.

EXECUTE выдаётся только на nested helpers `inventory_application_can_write`,
`inventory_fbs_context`, `inventory_lock_fbs_fence`, `inventory_check_location_write`,
`inventory_freeze_fbs_lock_locations`, `inventory_task_locations` и, после его создания,
`apply_strict_inventory_source_adjustment`. Trigger functions вызываются установленными
triggers; их EXECUTE проверяет владелец при CREATE TRIGGER, public grants не расширяются.

Для apply профиля `inventory` дополнительно проверяются metadata locking prerequisites:
SELECT и UPDATE хотя бы одной колонки на `public.products` и `wms.locations`. Если у роли
нет UPDATE ни на одну колонку, явный grant mode выдаёт только UPDATE(id) / UPDATE(location_id).
Полный UPDATE на products/locations не добавляется. Он нужен locking read, а не прямому
редактированию inventory из нового service.

Это **добавка к существующему WMS-контракту**, не замена старых grants. Сохраняются права
на ledger/projection, locations, КИЗ, containers, registry, shipment/task tables и старые
sequences по обязанностям роли. В частности, multi-source FBS уже требует EXECUTE
`wms.ship_kiz(bigint,bigint,varchar,bigint,numeric)`; PUBLIC этого права не имеет. Эти старые
права не выдаются модулем автоматически; на staging проверить их до inventory migrations.
USAGE schemas wms/public обязателен. Для реального apply существующие movement INSERT и
projection INSERT/UPDATE/DELETE должны быть разрешены runtime-role действующим контрактом.

### Порядок ручного deployment rehearsal

Примеры ниже — инструкция оператору, агент их на production не выполнял. Использовать
отдельное owner connection с ON_ERROR_STOP. Имена ролей заменить фактическими; список
сверить со всеми replicas/consumers/retry workers. MODULE/FREEZE/APPLY остаются false.
Не оборачивать файлы migrations с собственными BEGIN/COMMIT в ещё один внешний BEGIN.

```sql
-- psql, cwd — корень репозитория; 20261022/20261029 уже применены.
\set ON_ERROR_STOP on
\i scripts/migrations/20261102_inventory_projection_preflight.sql
SET wms.inventory_runtime_roles = '[{"role":"wms_backend","profile":"inventory"},{"role":"wms_consumer","profile":"writer"}]';
-- Явное ручное согласие владельца на ограниченную выдачу прав новым objects:
SET wms.inventory_grant_runtime_privileges = 'on';
\i scripts/migrations/20261030_add_inventory_location_freeze.sql
\i scripts/migrations/20261030_add_inventory_physical_apply.sql
SET wms.inventory_grant_runtime_privileges = 'off';
\i scripts/migrations/20261102_inventory_runtime_privileges_preflight.sql
RESET wms.inventory_runtime_roles;
RESET wms.inventory_grant_runtime_privileges;
```

Если права уже обеспечены существующим ACL/default privilege contract, оставить grant
mode off: встроенный preflight проверит их до включения guards. Отдельный privileges
preflight использует READ ONLY и принудительный local grant mode off даже при ошибочно
оставленном session on. При ошибке SQL — ROLLBACK, затем разбор причины; не продолжать
применение со следующего statement. При неизвестном body не заменять его архивным SQL.

Это изменения **ещё не применённых** Stage 2A migration files. Перед rehearsal проверить
migration state. Если старая версия Stage 2A уже установлена в окружении, не перезапускать
CREATE-файлы: требуется отдельная проверенная forward upgrade procedure; удалять schema,
возвращать старое тело projection или терять applications/movements недопустимо.

После migrations: check-only preflight, smoke под настоящими non-owner roles без freeze,
затем обновление всех FBS processes и dry-run freeze/release. Только после staging
приёмки разрешать controlled physical pilot. Production source timing и отсутствие
неизвестных physical operations эти исправления не доказывают. Порядок безопасного
отключения freeze/apply и запрет rollback старых workers при active fences сохраняются.

### Проверки исправлений — 02.11.2026

Все SQL writes выполнялись только в disposable PostgreSQL 17.4, контейнер
`wms-stage2a-review-fixes`, localhost:55452. Production, RabbitMQ и flags не изменялись.

- Исходные freeze/physical tests после замены wrapper: **64 passed**, 55.08 s.
- Окончательная новая deployment matrix: **23 passed**, 21.32 s. Поддержанный body с
  исходными comments и без них; точный leading `/* BEGIN ... */` из review, BEGIN в
  declaration string, unknown body и SECURITY DEFINER rejected; rollback всей migration.
- Реальные LOGIN non-owner роли: legacy receive/transfer/adjust/ship и location INSERT с
  flags=false; blocked writers получают P7601; writer-profile standard FBS invalidates
  freeze; inventory-profile multi-source KIZ FBS retry/duplicate; freeze/release и
  shortage/surplus/zero/no-delta/missing-row incoming apply, фактический stock и registry.
- Negative ACL tests: check-only отказ до установки guards, rollback ACL при ошибке
  следующей роли roster, новые apply dependencies, отсутствие UPDATE при locking read,
  sequence USAGE и helper EXECUTE. Metadata test доказывает UPDATE только ключевой
  колонки без выдачи UPDATE name/полного UPDATE table. Runtime roles не superuser.
- Первый общий прогон: **963 passed, 2 failed**, 258.96 s, только известные ship/assignment
  и transfer/assignment deadlocks. После окончательного privilege уточнения полный
  suite: **966 passed, 0 failed/errors/skipped**, 258.91 s. В том числе **181** тест нового
  inventory-модуля (23 deployment, 64 freeze/physical, 94 Stage 1/preparation).
  JUnit `/tmp/wms-stage2a-review-fixes-final.xml`.
- Baseline `404c55e` из git archive в отдельной /tmp-копии без inventory migrations:
  ship/assignment deadlock воспроизведён на третьем запуске, transfer/assignment — на
  восьмом. Logs `/tmp/wms-stage2a-fixes-baseline-ship.log` и
  `/tmp/wms-stage2a-fixes-baseline-transfer.log`. Это не новые дефекты и не исправления
  данной задачи; один зелёный полный suite не доказывает устранение deadlocks.
- Black и Ruff трёх изменённых Python test/fixture файлов, git diff --check — passed.
  Общий `ruff check app` не чист: 7 прежних замечаний в неизменённых locations,
  operations_history_service, system_service, re_sorting_operation_repository,
  retry_worker. В рамках B.1/B.2 они не исправлялись.

Новых endpoints, movement types или physical business rules нет. B.1/B.2 закрыты для
поддержанного **fresh/unapplied** migration path; следующий шаг — staging deployment
rehearsal с фактическим schema/role roster. Уже установленная старая Stage 2A требует
отдельной forward upgrade procedure. Операционная граница delayed FBS, обновление всех
workers и условия ограниченного pilot остаются обязательными; это не production approval.

## Проверки и результаты исходной реализации — 30.10.2026

Только локальный disposable PostgreSQL **17.4**; полный suite запускается с
KIZ_TEST_INVENTORY_SCHEMA=1, поэтому все kiz_pool tests получают установленные новые guards,
а не только тесты нового модуля. Activation/import suites используют другие disposable DBs.
Секреты production не читались/не публиковались. Интеграции с настоящим RabbitMQ transport
не запускались; подтверждены общий processing path, HTTP ingress, worker retry и code paths.

- Freeze до physical writer: **21 passed**; затем расширено покрытие tasks/КИЗ/multi-source.
- Первый physical набор: **17 passed**; затем добавлены fault injection и concurrency.
- Актуальная freeze/physical схема с task admission: **55 passed**, 37.44 s.
- Окончательный freeze/physical набор с partition dispatch, missing-row freeze,
  FBS savepoints, free/frozen гонками, fixed descendants и ancestor edits:
  **64 passed**, 50.42 s.
- OpenAPI/API-map, ссылки документации и unit contracts: **63 passed**, 4.32 s.
- Исходный backend **404c55e** в отдельной /tmp копии: receive, transfer, adjust, legacy FBS
  успешно выполнены на новой DB **без active freezes**, registry и реальные quantities проверены.
- Первый полный suite: **927 passed, 3 failed**, 208.48 s: два известных КИЗ deadlocks и
  отсутствующие descriptions новых freeze endpoints. Descriptions исправлены.
- Последний полный suite на окончательной схеме: **943 passed, 0 failed/errors/skipped**,
  **217.22 s**. Из них **158** tests инвентаризации, включая **64** новых freeze/physical
  сценария. Результат сверен с JUnit XML `/tmp/wms-inventory-stage2a-final-suite.xml`.
- Исходный 404c55e повторно проверен с окончательными guards: receive/transfer/adjust/
  legacy FBS работают без active freezes, реальные количества и registry проверены.
- black --check затронутых Python-файлов и git diff --check прошли. Test PostgreSQL
  после завершения остановлена и удалена; production flags/DB/RabbitMQ не затрагивались.

Покрываются: обе стороны transfer, receive/adjust/kit/re-sort/ship; task admission и
conflicts; КИЗ activation/transfer/deactivation/ship; controlled fill и maintenance;
freeze/writer и old RR гонки; explicit release; real HTTP legacy FBS и multi-source
active-КИЗ retry/duplicate; перекрёстные FBS source/freeze orders; surplus/shortage/zero/
fraction/missing row; NULL-empty/multi-batch/mixed stock отказ; повтор/concurrent apply;
последний movement/deferred commit/изменение projection AFTER trigger; обе очередности
FBS/approval; product/location/inventory locks; history/integrity/recalculate и immutable
initial snapshot. Изменение КИЗ другого SKU без holder не инвалидирует чужую пару.

Legacy deadlock assignment versus transfer/ship не исправлялся. На исходном 404c55e он
ранее подтверждён: transfer 4/8, ship 5/32; новые guards не объявляются его устранением.
Production-ready verdict требует отдельной приёмки операционного протокола/ролей/нагрузки
на staging. Рабочий physical pilot не является автоматическим разрешением включить prod.

## Файлы и следующий этап

Новые файлы текущего этапа:

- app/core/services/inventory_freeze_service.py
- app/core/services/inventory_application_service.py
- app/infrastructure/database/queries/inventory_freeze.py
- scripts/migrations/20261030_add_inventory_location_freeze.sql
- scripts/migrations/20261030_add_inventory_physical_apply.sql
- tests/test_inventory_freeze_postgres.py
- tests/test_inventory_physical_apply_postgres.py
- docs/flows/inventory_stage2a_physical_apply.md

Изменены поверх сохранённых Stage 1/preparation изменений:

- app/core/services/inventory_document_service.py
- app/core/schemas/inventory_documents.py
- app/api/v1/endpoints/inventory_documents.py
- app/infrastructure/database/queries/inventory_applications.py
- app/shared/config.py
- app/infrastructure/database/repositories/fbs_shipment_repository.py
- app/infrastructure/database/repositories/fbs_kiz_repository.py
- app/middleware/error_handler.py
- tests/conftest.py
- tests/test_inventory_documents_postgres.py
- tests/test_inventory_documents_unit.py
- docs/current/api_map.md, business_rules.md, invariants.md, open_questions.md
- docs/decisions/decisions.md
- docs/flows/inventory_stage1.md, inventory_stage2a_preparation.md
- scripts/migrations/README.md

Существующий B.1 SQL diff app/infrastructure/database/queries/inventory_documents.py и
предыдущие audit/user untracked files не перезаписывались. Дефолты флагов не включались;
commit, смена ветки и deployment не выполнялись.

Stage 2B: batch allocation, mixed КИЗ и контейнерные counts, доказуемые delayed FBS
physical boundaries, безопасное расширение адресов/лимитов и ролей. Глобальное изменение
старых writers/versioning, исправление legacy КИЗ deadlock и 1С в этот этап не входят.
