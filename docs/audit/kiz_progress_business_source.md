# KIZ-related WMS progress audit

Дата аудита: 2026-10-05.

Область аудита: изменения, начавшиеся с ветки `feature/kiz`, и последующие связанные работы по KIZ, container, movement identity, receipt import и FBS. Базовый функционал WMS, не менявшийся ради этого контура, не включён.

Ключевая оговорка: история разделена на две независимые линии. Ветка `feature/kiz` содержит ранний полный KIZ movement/container contour, но не является предком текущей `feature/kiz-fbs-integration`; их merge-base — `249c54a`. Текущая ветка создана от более поздней ветки `kiz` и получила FBS task-level через merge `9784cfd`. Поэтому возможности ранней ветки отмечены как branch-local и не считаются автоматически доступными из текущего HEAD.

## 1. Timeline

- `2026-09-04`, `feature/kiz`, `fb9688c`: зафиксирован дизайн индивидуального учёта KIZ.
- `2026-09-09`, `feature/kiz`, `f2594dc`: реализован KIZ v1 — реестр, lifecycle, assignment, identified/unidentified, guards, audit и integrity.
- `2026-09-21`, `feature/kiz`, `02b0507`: branch-local контур расширен stable movement identity, явными KIZ transfer/shipment, history и управляемыми container B1/B2/B3/B4/Stage 3C операциями с KIZ holders.
- `2026-09-23`, ветка `kiz` создана независимо от `main@5970490`: ранняя `feature/kiz` в ancestry не вошла.
- `2026-09-26`, `kiz`, `2c64d93`: добавлены raw inbox RabbitMQ `orders.kiz.imported` и ручной business apply сохранённого сообщения.
- `2026-09-28`, `kiz`, `083362b`: receipt-import semantics изменена с `active` на holder-less `registered`, чтобы регистрация не резервировала physical stock.
- `2026-09-29`, `feature/fbs-task-level-processing`, `c632f32`: FBS переведён на per-СЗ результаты, replay classification и task-level retry semantics.
- `2026-10-02`, `kiz`, `6869e81` + `8b71fb8`: добавлена явная activation `registered -> active` без movement.
- `2026-10-02`, `feature/kiz-fbs-integration`, merge `9784cfd`: объединены текущая receipt/activation линия и `feature/fbs-task-level-processing`.
- Worktree после `9784cfd`: реализуются FBS+KIZ Stage 1, optional `organization` и backward-compat hotfix для schema-first rollout. Эти изменения присутствуют в файлах и тестах, но не входят в текущий commit.

## 2. Business capability progression

### Индивидуальный учёт KIZ рядом с количественным остатком

**Статус:** PARTIALLY IMPLEMENTED — реализовано в `feature/kiz`, но эта ветка не интегрирована в текущий HEAD.

**Было:**  
WMS учитывал физический товар главным образом как количество по product/location/batch/container. Конкретная единица товара не имела отдельного lifecycle и holder.

**Стало:**  
В branch-local KIZ v1 добавлен реестр конкретных кодов, active и terminal lifecycle, holder, assignment, immutable audit events и integrity checks. Active KIZ участвуют в расчёте identified stock; разница между physical quantity и active KIZ образует unidentified stock.

**Новая возможность:**  
Один product может одновременно иметь идентифицированные и неидентифицированные физические единицы, а операции могут проверять, что количество не уменьшается ниже числа active KIZ.

**Ограничение:**  
KIZ v1 API/service files из `feature/kiz` отсутствуют в `feature/kiz-fbs-integration@9784cfd`; текущая линия использует только необходимую часть DB-контракта через receipt, activation и Stage 1 worktree.

**Evidence:**
- Branch/commit: `feature/kiz`, `f2594dc`.
- Files: `feature/kiz:app/core/services/kiz_service.py`, `feature/kiz:app/infrastructure/database/repositories/kiz_repository.py`.
- Migration: `feature/kiz:scripts/migrations/20260906_add_kiz_v1.sql`.
- Tests: `feature/kiz:tests/test_kiz_integration.py`, `feature/kiz:tests/test_kiz_lock_protocol.py`, `feature/kiz:tests/test_kiz_unit.py`.

### Physical guards и конкурентная защита KIZ

**Статус:** PARTIALLY IMPLEMENTED — DB/test implementation существует в `feature/kiz`; current receipt/activation fixtures и migrations продолжают этот контракт, но полный branch-local API не смержен.

**Было:**  
Обычное количественное движение не знало, сколько единиц в остатке закреплено за конкретными KIZ. Параллельные assignment и movement могли принимать решения по устаревшему состоянию.

**Стало:**  
Добавлены guards, блокировки KIZ/inventory scope и проверки `physical >= identified`. Legacy movement может расходовать unidentified часть, но не может уменьшить остаток ниже active identified count. Ошибка audit или незавершённый controlled protocol откатывает всю операцию.

**Новая возможность:**  
Legacy количественные операции продолжают работать на свободной части остатка, не теряя уже назначенные KIZ.

**Evidence:**
- Branch/commit: `feature/kiz`, `f2594dc`, далее `02b0507`.
- Migration: `feature/kiz:scripts/migrations/20260906_add_kiz_v1.sql`.
- Tests: `test_inventory_quantity_guard`, `test_inventory_lock_serializes_identity_and_spend`, `test_concurrent_assignment` в `feature/kiz:tests/test_kiz_integration.py`.
- Current regression evidence: `tests/test_kiz_import_b2_postgres.py`, `tests/test_kiz_activation_postgres.py`.

### Stable movement identity

**Статус:** PARTIALLY IMPLEMENTED — реализовано и протестировано в `feature/kiz`; Stage 1 worktree требует этот DB-контракт, но migration отсутствует в ancestry текущего HEAD.

**Было:**  
`wms.movements` partitioned, а один `movement_id` не являлся достаточной глобальной FK identity. Надёжная связь KIZ или СЗ с конкретным movement была затруднена.

**Стало:**  
Добавлен `wms.movement_registry` со стабильным `movement_ref`, backfill существующих movements и trigger-регистрация новых. KIZ operation items, KIZ movement links и позднее FBS task results могут ссылаться на `movement_ref`.

**Новая возможность:**  
История конкретного KIZ и конкретной СЗ может ссылаться на однозначное физическое движение независимо от partition-local особенностей `movement_id`.

**Ограничение:**  
Текущий Stage 1 rollout зависит от предварительного наличия этой схемы; сама migration находится только в `feature/kiz`.

**Evidence:**
- Branch/commit: `feature/kiz`, `02b0507`.
- Migration: `feature/kiz:scripts/migrations/20260910_add_movement_registry.sql`.
- Tests: `feature/kiz:tests/test_movement_registry_integration.py`.
- Current dependency: `scripts/migrations/20261002_fbs_kiz_stage1_preflight.sql`, `scripts/migrations/20261002_add_fbs_kiz_stage1.sql`.

### Явные KIZ transfer и shipment

**Статус:** PARTIALLY IMPLEMENTED — branch-local implementation не входит в текущий HEAD.

**Было:**  
KIZ можно было назначить остатку, но не было controlled physical операций, одновременно изменяющих movement, KIZ holder/lifecycle, event и association.

**Стало:**  
В `feature/kiz` добавлены явные transfer и shipment, operation ledger, idempotency fingerprint, KIZ movement links и lifecycle `shipped`. Смешанная операция может перемещать identified KIZ и unidentified quantity в одной physical quantity, а replay не создаёт второй эффект.

**Новая возможность:**  
Конкретную единицу можно переместить между loose locations или списать в отгрузку с проверяемой связью с physical movement.

**Ограничение:**  
Endpoint/service files transfer и ship отсутствуют в текущей интеграционной ветке; FBS Stage 1 использует более узкий shipment flow внутри FBS.

**Evidence:**
- Branch/commit: `feature/kiz`, `02b0507`.
- Files: `feature/kiz:app/core/services/kiz_transfer_service.py`, `feature/kiz:app/core/services/kiz_ship_service.py`.
- Migrations: `20260910_add_kiz_operations.sql`, `20260910_add_kiz_transfer.sql`, `20260910_add_kiz_movement_links.sql`, `20260911_add_kiz_ship.sql` в `feature/kiz`.
- Tests: `feature/kiz:tests/test_kiz_transfer_integration.py`, `feature/kiz:tests/test_kiz_ship_integration.py`, `feature/kiz:tests/test_kiz_operation_idempotency_integration.py`.

### История конкретного KIZ

**Статус:** PARTIALLY IMPLEMENTED — branch-local implementation не входит в текущий HEAD.

**Было:**  
Не было единой карточки, объединяющей текущее состояние KIZ, lifecycle events и связанные physical movements.

**Стало:**  
В `feature/kiz` добавлен read model истории KIZ: текущее состояние, chronological timeline, movement links, исторические locations и terminal shipment state.

**Новая возможность:**  
По одному коду можно восстановить его назначение, перемещения и отгрузку.

**Ограничение:**  
`kiz_history` endpoint/service отсутствуют в текущем HEAD; activation пишет события, но общей карточки из ранней ветки текущая линия не предоставляет.

**Evidence:**
- Branch/commit: `feature/kiz`, `02b0507`.
- Files: `feature/kiz:app/api/v1/endpoints/kiz_history.py`, `feature/kiz:app/core/services/kiz_history_service.py`.
- Tests: `feature/kiz:tests/test_kiz_history_integration.py`, `feature/kiz:tests/test_kiz_history_unit.py`.

### Управляемый lifecycle контейнера

**Статус:** PARTIALLY IMPLEMENTED — B1/B2.1/B2.2/B3/B4 реализованы в `feature/kiz`, но не интегрированы в текущий HEAD.

**Было:**  
Legacy container paths не давали единого атомарного контракта для создания пустого контейнера, fill, partial/full extract, move и unpack-all; generic movement мог обходить container invariants.

**Стало:**  
Добавлены stable container identity, создание только пустого контейнера, controlled fill/extract/move/unpack-all, operation ledger, replay protection, container locks и запрет generic container movements. Операции сохраняют согласованность `containers`, `container_contents`, inventory projection и movements.

**Новая возможность:**  
Физическое содержимое контейнера можно изменять только через контролируемые операции с атомарным rollback и повторяемым результатом.

**Ограничение:**  
Текущая ветка содержит только legacy `container_service.py`; branch-local `container_operations` endpoints/services отсутствуют.

**Evidence:**
- Branch/commit: `feature/kiz`, `02b0507`.
- Migrations: `20260915_add_container_b1_contract.sql`, `20260915_add_container_b21_fill.sql`, `20260916_add_container_b22_extract.sql`, `20260916_add_container_b3_move_unpack_all.sql`, `20260917_add_container_b4_final.sql` в `feature/kiz`.
- Tests: `feature/kiz:tests/test_container_b1_integration.py`, `test_container_b21_fill_integration.py`, `test_container_b22_extract_integration.py`, `test_container_b3_integration.py`, `test_container_b4_integration.py`.

### KIZ holder внутри контейнера

**Статус:** PARTIALLY IMPLEMENTED — Stage 3C реализован в `feature/kiz`, но не интегрирован в текущий HEAD и не поддержан FBS Stage 1.

**Было:**  
Active KIZ имел только loose location holder и не мог согласованно следовать за единицей товара при fill/extract контейнера.

**Стало:**  
Добавлен взаимоисключающий holder `location_id` или `container_id`. Fill переводит KIZ в container holder, extract/unpack возвращает location holder, move контейнера сохраняет holder на контейнере. Deferred integrity checks сопоставляют KIZ count, container contents и physical projection.

**Новая возможность:**  
Конкретные идентифицированные единицы могут храниться и перемещаться вместе с контейнером.

**Ограничение:**  
Прямая FBS-отгрузка active container KIZ не реализована; Stage 1 возвращает `KIZ_CONTAINER_SHIPMENT_UNSUPPORTED`.

**Evidence:**
- Branch/commit: `feature/kiz`, `02b0507`.
- Migration: `feature/kiz:scripts/migrations/20260918_add_container_c1_kiz.sql`.
- Files: `feature/kiz:app/core/services/container_kiz_service.py`.
- Tests: `feature/kiz:tests/test_container_c1_integration.py`.

### Raw inbox для receipt KIZ

**Статус:** IMPLEMENTED в текущей линии.

**Было:**  
Сообщение KIZ из RabbitMQ не имело отдельного durable inbox и диагностического следа до запуска складской бизнес-логики.

**Стало:**  
Consumer `orders.kiz.imported` сохраняет raw body/payload, Rabbit metadata, parse status и диагностические поля. ACK выполняется после commit; ошибка БД приводит к NACK/requeue. Malformed JSON также сохраняется для анализа. `supply_guid` читается только как временный alias, семантическим полем остаётся `order_guid`.

**Новая возможность:**  
Входящее сообщение можно проверить, повторно разобрать и вручную применить независимо от Rabbit delivery.

**Ограничение:**  
Consumer сохраняет сообщение, но не запускает business apply автоматически.

**Evidence:**
- Branch/commit: `kiz`, `2c64d93`.
- Files: `app/consumer.py`, `app/core/services/kiz_import_service.py`, `app/api/v1/endpoints/kiz_import.py`.
- Migration: `scripts/migrations/20260923_add_kiz_import_inbox.sql`.
- Tests: `tests/test_kiz_import_ingestion.py`.

### Receipt KIZ как registered identity без резерва

**Статус:** IMPLEMENTED в текущей линии.

**Было:**  
Первоначальный B2 создавал receipt-import KIZ сразу `active` в приёмочной location. Это делало его частью identified physical stock и могло блокировать старые loose movements, хотя физический scan ещё не подтверждался.

**Стало:**  
B2.1 создаёт holder-less `registered` KIZ с receipt provenance, не создаёт movement и не меняет inventory. Registered KIZ не входит в identified/unidentified и не ограничивает legacy movement. Receipt limit учитывает registered и active identity, replay идемпотентен, terminal KIZ не реактивируется.

**Новая возможность:**  
WMS может заранее знать коды поставки, не резервируя под них физические единицы и не ломая старую работу с россыпью.

**Evidence:**
- Branch/commit: `kiz`, `083362b`.
- Migrations: `scripts/migrations/20260928_kiz_receipt_b21_preflight.sql`, `scripts/migrations/20260928_kiz_receipt_b21_registered.sql`.
- Tests: `tests/test_kiz_receipt_b21_migration_postgres.py`, `tests/test_kiz_import_b2_postgres.py`.

### Явная activation registered KIZ

**Статус:** IMPLEMENTED в текущей линии.

**Было:**  
Registered identity не была связана с фактически найденной единицей и не участвовала в identified stock.

**Стало:**  
`POST /api/kiz/activate` подтверждает scan, назначает active loose holder и пишет event `activated` без physical movement. Операция проверяет exact loose capacity, работает batch all-or-nothing и идемпотентна по client key.

**Новая возможность:**  
После фактического сканирования единица становится identified, а legacy movement может расходовать только оставшийся unidentified stock.

**Ограничение:**  
Activation непосредственно в container и automatic activation не реализованы.

**Evidence:**
- Branch/commit: `kiz`, `6869e81`, `8b71fb8`.
- Files: `app/core/services/kiz_activation_service.py`, `app/api/v1/endpoints/kiz.py`.
- Migration: `scripts/migrations/20261001_add_kiz_activation.sql`.
- Tests: `tests/test_kiz_activation_postgres.py`, `tests/test_kiz_activation_api.py`, `tests/test_kiz_activation_migration_postgres.py`.

### FBS результат и replay на уровне СЗ

**Статус:** IMPLEMENTED в текущей линии после merge `9784cfd`.

**Было:**  
FBS product group обрабатывалась общим результатом. Уже обработанные, повторные, отсутствующие и новые СЗ было трудно разделить; повтор сообщения мог блокировать новые СЗ либо создавать риск повторного списания.

**Стало:**  
`wms.fbs_shipment_task_results` хранит результат каждого occurrence СЗ. Task-level режим списывает только уникальные новые СЗ, классифицирует confirmed duplicate, inconsistent и not_found, сохраняет movement identity и использует результаты при automatic/manual retry.

**Новая возможность:**  
Повторное сообщение или retry может безопасно пропустить уже списанные СЗ и обработать только новые единицы.

**Ограничение:**  
Режим включается настройкой; default `legacy` сохраняет старое поведение. Исторического backfill для старых shipments нет. Retry claim через `FOR UPDATE SKIP LOCKED` остаётся открытым вопросом.

**Evidence:**
- Branch/commit: `feature/fbs-task-level-processing`, `c632f32`; merge `9784cfd`.
- Files: `app/handlers/write_off_fbs_handler.py`, `app/retry_worker.py`, `app/infrastructure/database/repositories/fbs_shipment_repository.py`.
- Migration: `scripts/migrations/20260929_add_fbs_task_level_processing.sql`.
- Tests: `tests/test_fbs_task_level_processing.py`, `tests/test_fbs_retry_worker.py`.

### FBS shipment с optional KIZ binding и несколькими physical sources

**Статус:** PARTIALLY IMPLEMENTED — код, migrations, docs и tests находятся в worktree, но отсутствуют в commit `9784cfd`.

**Было:**  
FBS payload содержал только `assembly_tasks`; product group списывалась из одной FBS zone и не могла учитывать фактический holder конкретного active KIZ.

**Стало:**  
Добавлено optional additive поле `kiz_bindings`. Каноническая связь СЗ↔KIZ хранится отдельно и конфликтует при смене одной стороны. No-KIZ и registered KIZ списываются из FBS zone; active loose KIZ — из своего holder. В одной product transaction создаётся по одному movement на physical source, а каждая СЗ связывается со своим `movement_ref`. Registered и active loose KIZ переходят в `shipped` атомарно с physical write-off.

**Новая возможность:**  
Одна FBS product group может списать разные физические единицы из разных locations и сохранить точную связь `СЗ -> KIZ -> movement_ref`.

**Ограничение:**  
Active container KIZ оставлен для Stage 2. Для запуска нужны обе migrations от `20261002`, preflight и совместимая stable movement/KIZ schema. Текущие Stage 1 tests проверяют сервисный контракт через fake repository, но отдельного PostgreSQL integration test полного FBS transaction в файле Stage 1 нет.

**Evidence:**
- Worktree files: `app/core/services/fbs_kiz_shipment_service.py`, `app/infrastructure/database/repositories/fbs_kiz_repository.py`, `app/handlers/write_off_fbs_handler.py`.
- Migrations: `scripts/migrations/20261002_add_fbs_kiz_stage1.sql`, `scripts/migrations/20261002_extend_kiz_fbs_shipment.sql`.
- Tests: `tests/test_fbs_kiz_stage1.py`.
- Decision: `docs/decisions/decisions.md`, решение от 2026-10-02.

### Обратная совместимость schema-first FBS rollout

**Статус:** PARTIALLY IMPLEMENTED — hotfix существует в worktree; rollout нельзя доказать только git history.

**Было:**  
Первая версия Stage 1 constraint требовала source identity для каждого `written_off` task result. Старая версия приложения после раннего применения schema продолжала писать все новые source-поля как `NULL`, поэтому legacy FBS transaction откатывалась.

**Стало:**  
Constraint разрешает две формы: полностью legacy row со всеми source-полями `NULL` либо полностью заполненную Stage 1 identity. Частично заполненная identity остаётся запрещённой.

**Новая возможность:**  
Stage 1 schema можно временно использовать со старой версией приложения без отключения consistency check для нового flow.

**Evidence:**
- Files: `scripts/migrations/20261005_fbs_stage1_backward_compat_hotfix.sql`, обновлённый `scripts/migrations/20261002_add_fbs_kiz_stage1.sql`.
- Documentation: `docs/current/known_issues.md`, `scripts/migrations/README.md`.

### Organization provenance receipt KIZ

**Статус:** PARTIALLY IMPLEMENTED — migration, code и tests находятся в worktree, но отсутствуют в commit `9784cfd`.

**Было:**  
Receipt message и KIZ identity не сохраняли optional `supply.organization`; одинаковый KIZ нельзя было проверить на конфликт между двумя непустыми организациями.

**Стало:**  
Optional `organization` сохраняется в inbox и в новом receipt-import KIZ как immutable nullable attribute. Legacy payload без поля продолжает работать. Повтор с другой непустой organization даёт controlled conflict; существующий `NULL` автоматически не переписывается.

**Новая возможность:**  
Receipt provenance KIZ можно разделять по организации без обязательного изменения producer payload.

**Ограничение:**  
Это provenance, а не полная authorization model: FBS `account` не сверяется с `wms.kiz.organization`.

**Evidence:**
- Worktree files: `app/core/services/kiz_import_service.py`, `app/core/schemas/kiz_import.py`, `app/infrastructure/database/queries/kiz_import.py`.
- Migration: `scripts/migrations/20261005_add_kiz_organization.sql`.
- Tests: organization cases в `tests/test_kiz_import_ingestion.py`, `tests/test_kiz_import_b2_postgres.py`.

## 3. Superseded decisions

- Receipt import `active` в приёмочной location из B2 (`20260924`) заменён B2.1: holder-less `registered`, без physical reservation (`20260928`).
- Идея назначать physical holder во время receipt import заменена отдельной explicit activation после scan.
- Предложения внешнего FBS `event_id`, canonical message hash и `fbs_shipment_product_groups` не приняты: текущая модель использует СЗ как unit key, `fbs_kiz_bindings` для canonical binding и `fbs_shipment_task_results` для результата/movement.
- FBS shipment active KIZ из container не включён в Stage 1 и перенесён в отдельный Stage 2.
- `supply_guid` не принят как основной receipt contract: используется только fallback; приоритет у `supply.order_guid`.
- Ранние full KIZ/container APIs реализованы в `feature/kiz`, но не вошли в текущую integration ancestry. Их нельзя считать автоматически доступными в текущем приложении только по наличию branch-local кода и DB-зависимостей.

## 4. Current remaining scope

- Решить branch integration: перенести или явно отказаться от branch-local KIZ card/list/history, explicit transfer/shipment и controlled container operations из `feature/kiz`. Сейчас current HEAD содержит receipt import и activation, но не эти API/services.
- Зафиксировать Stage 1 и organization worktree в истории, выполнить полный migration/deploy verification и PostgreSQL integration smoke для mixed-source FBS transaction.
- Реализовать Stage 2 для shipment active KIZ из container без ослабления holder/container guards.
- Определить activation непосредственно в container и нужен ли automatic activation.
- Связать FBS `account` с KIZ `organization`, если organization должна быть не только provenance, но и ownership/authorization boundary.
- Определить KIZ semantics для kit operations и re-sorting: текущие flows количественные и не выбирают конкретные KIZ.
- Определить поддержку KIZ для batch stock и статусов `damaged`/`quarantine`; текущий active loose scope ориентирован на exact available stock без batch/container.
- Сохранить automatic KIZ import apply выключенным до отдельного решения; сейчас consumer выполняет только raw inbox save/ACK.
- Закрыть race automatic retry и manual retry одной FBS позиции через явный claim/lock protocol.

## 5. Final condensed list

1. Добавили индивидуальный реестр KIZ и lifecycle конкретной единицы в branch-local KIZ-контуре.  
   Возможность: различать identified и unidentified часть одного физического остатка.

2. Добавили physical guard для active KIZ.  
   Было: количественное движение не учитывало закреплённые единицы.  
   Стало: legacy flow может расходовать только свободную unidentified часть.  
   Возможность: сохранять конкретные KIZ при обычных движениях товара.

3. Добавили stable `movement_ref`.  
   Было: `movement_id` не давал удобной глобальной identity для partitioned movements.  
   Стало: KIZ и СЗ могут ссылаться на однозначное физическое движение.  
   Возможность: строить проверяемую историю единицы и отгрузки.

4. Добавили явные KIZ transfer/shipment и историю KIZ в `feature/kiz`.  
   Возможность: перемещать и отгружать конкретную единицу с lifecycle event и physical movement.  
   Ограничение: эти API не интегрированы в текущий HEAD.

5. Добавили controlled container lifecycle и KIZ holder внутри контейнера в `feature/kiz`.  
   Возможность: fill, extract, move и unpack сохраняют container/inventory/KIZ consistency.  
   Ограничение: текущая integration branch не содержит этих services/endpoints.

6. Добавили raw inbox для сообщений receipt KIZ.  
   Возможность: сохранять исходное RabbitMQ message до бизнес-обработки и безопасно анализировать/replay его вручную.

7. Изменили receipt KIZ с `active` на `registered`.  
   Было: импорт мог резервировать physical stock до scan.  
   Стало: зарегистрированный код не имеет holder и не участвует в identified stock.  
   Возможность: старые loose movements продолжают работать без блокировки.

8. Добавили явную activation после scan.  
   Возможность: перевести registered identity в active physical unit без изменения общего количества.

9. Добавили результат FBS по каждой СЗ.  
   Было: повтор или аномалия оценивались на уровне общей product group.  
   Стало: новые, повторные, отсутствующие и inconsistent СЗ имеют отдельные результаты.  
   Возможность: retry списывает только ещё не обработанные единицы.

10. Добавили optional KIZ binding к существующему FBS payload в Stage 1 worktree.  
    Возможность: связать конкретную СЗ с конкретным KIZ, не меняя обязательное поле `assembly_tasks` и legacy payload.

11. Добавили multi-source FBS shipment в Stage 1 worktree.  
    Было: product group списывалась из одной FBS zone.  
    Стало: registered/no-KIZ units идут из FBS zone, active loose KIZ — из фактического holder.  
    Возможность: одна отгрузка создаёт несколько movements и сохраняет связь каждой СЗ со своим movement.

12. Добавили nullable organization provenance в receipt KIZ worktree.  
    Возможность: сохранять организацию из сообщения и обнаруживать конфликт двух непустых организаций для одного KIZ.  
    Ограничение: FBS account ownership пока не проверяется.

AUDIT COMPLETE: YES
