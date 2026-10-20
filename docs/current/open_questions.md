# Open Questions

Статус: `CURRENT`. Последний triage: 2026-09-24.

Статусы: `open` — требуется решение или проверка; `answered` — ответ подтверждён
источником; `accepted-risk` — ограничение осознанно принято; `obsolete` — вопрос потерял
актуальность. Владелец указывается как ответственная роль, пока конкретный человек или
issue tracker не назначены.

| ID | Статус | Вопрос | Владелец | Основание / связь |
|---|---|---|---|---|
| DB-01 | answered | Runtime подтверждает `ltree` 1.3 в схеме `wms`; источник provisioning/migration её создания не установлен. | DB owner | Runtime audit; `wms_schema.sql` не содержит `CREATE EXTENSION` |
| DB-02 | answered | Stable identity реализована через `movement_registry.movement_ref` и composite `UNIQUE/FK (movement_id, created_at)`; legacy links пока сохраняются. | WMS + DB owner | `kiz_stage2a_phase1.md`; migration 20260910 |
| DB-03 | open | Нужны ли FK для container refs, snapshots и `tasks.related_movement_id`? | WMS + DB owner | `known_issues.md`, пункты 5, 8 и 10 |
| DB-04 | answered | Runtime constraint positive quantity применён как `NOT VALID`; найдена одна legacy violation. | DB owner | Runtime audit 2026-08-08 |
| DB-05 | answered | Runtime side constraint применён как `NOT VALID`; legacy violations не найдены. | DB owner | Runtime audit 2026-08-08 |
| DB-06 | open | Какая production runtime role должна получить EXECUTE на `wms.transfer_kiz_location` после Phase 4 migration? | DB owner | SECURITY DEFINER function намеренно отозвана у PUBLIC |
| DB-07 | open | Какая production runtime role должна получить EXECUTE на `wms.ship_kiz` после Phase 5 migration? | DB owner | SECURITY DEFINER function намеренно отозвана у PUBLIC |
| CON-01 | open | Достаточна ли защита от отрицательного inventory в trigger и нужен ли явный pre-check? | WMS + DB owner | `known_issues.md`, пункт 9; runtime concurrency test |
| CON-02 | open | Где нужны row/advisory locks и единый порядок их получения? | WMS owner | `known_issues.md`, пункт 9 |
| CON-03 | obsolete | Legacy `unpack_from_container` удаляется B4 migration; controlled extract/unpack-all имеют locks. | WMS + DB owner | Container B4 |
| CON-04 | open | Как сериализовать параллельные FBS retry одной позиции? | WMS owner | External FBS tech debt в `known_issues.md` |
| CON-05 | open | Нужны ли advisory locks по ключу физического остатка для критичных списаний? | WMS + DB owner | Runtime concurrency test |
| LOC-01 | open | Где enforce диапазон и смысл `locations.level`? | WMS owner | Код + runtime schema audit |
| LOC-02 | open | Запрещать ли дочернюю локацию под неактивным parent? | Product + WMS owner | Требуется бизнес-решение |
| LOC-03 | open | Запрещать ли размещение/движение в неактивную location? | Product + WMS owner | Требуется бизнес-решение |
| LOC-04 | open | Запретить reparent узла с потомками или каскадно обновлять LTREE path? | WMS + DB owner | `known_issues.md`, пункт 7 |
| LOC-05 | open | Нужно ли менять `location_code` при rename/reparent? | Product + WMS owner | Требуется бизнес-решение |
| LOC-06 | open | Каков полный контракт доступной ячейки и capacity reservation? | Product + WMS owner | `known_issues.md`, пункт 6 |
| CNT-01 | obsolete | Legacy unpack удалён; controlled extract удаляет current row, unpack-all использует тот же protocol. | Product + WMS + DB owner | Container B2.2/B3/B4 |
| CNT-02 | open | Какие статусы контейнера должны проверяться DB function/trigger? | Product + WMS owner | Runtime behavior audit |
| CNT-03 | open | Должно ли перемещение parent container каскадно перемещать children и inventory? | Product + WMS owner | Требуется бизнес-решение |
| CNT-04 | open | Должна ли `block_empty_container` блокировать container/contents при проверке? | WMS + DB owner | `known_issues.md`, пункт 9 |
| FBS-01 | open | Нужны ли DB constraints для положительного `task_items.quantity_planned` и непустой task? | WMS + DB owner | Application schema проверяет вход, DB snapshot — нет |
| FBS-02 | open | Какие task transitions требуют row lock? | WMS owner | Runtime concurrency test |
| FBS-03 | open | Как структурно связывать task movements с task/items? | WMS + DB owner | `known_issues.md`, пункты 4 и 8 |
| FBS-04 | open | Нужен ли retry claim через `FOR UPDATE SKIP LOCKED`? | WMS owner | External FBS tech debt в `known_issues.md` |
| FBS-05 | accepted-risk | FBS сейчас списывает агрегированный available остаток без batch/container dimensions. | Product + WMS owner | Текущий pipeline; расширение требует отдельного решения |
| FBS-06 | answered | Legacy `movement_id` у failed FBS item не заполняется; частичный movement публикуется через task results. | Product + WMS owner | Решение 2026-09-29; `proposals/fbs_task_level_processing_spec.md`, раздел 10 |
| FBS-07 | open | Какой retention нужен для `fbs_shipment_task_results` и требуется ли партиционирование таблицы при фактическом объеме СЗ? | Product + DB owner | `proposals/fbs_task_level_processing_spec.md`, разделы 17 и 18 |
| FBS-08 | open | Какая авторитетная связь подтверждает соответствие `public.assembly_task` и `product_id`, чтобы отдельно классифицировать product mismatch? | Product + WMS owner | Task-level FBS сохраняет текущую legacy-проверку нового СЗ только по входной product group |
| TEST-01 | answered | Production `vector_db` нельзя использовать; нужен отдельный development/stage клон и выделенные fixtures. | QA + DB owner | Подтверждение владельца 2026-08-08; `runtime_concurrency_plan.md` |
| TEST-02 | open | Добавить ли regression-тест полной/частичной распаковки после выбора модели CNT-01? | QA + WMS owner | Блокируется решением CNT-01 |
| KIZ-01 | open | Какое поле фактически и стабильно присылает producer: `supply.order_guid` или `supply.supply_guid`? | Integration owner | KIZ import R1 raw inbox; подтвердить по stage-сообщениям до следующих фаз |
| KIZ-02 | answered | Receipt import не ищет текущую physical unit: B2.1 создаёт `registered` KIZ без holder и резерва. | Product + WMS owner | Решение 2026-09-28; `wms_kiz_receipt_b21_registered_no_reservation.md` |
| KIZ-03 | answered | Явный `POST /api/kiz/activate` подтверждает scan, проверяет exact loose capacity и атомарно назначает active location holder без movement. | Product + WMS owner | Решение 2026-10-01; `wms_kiz_activation_registered_to_active.md` |
| KIZ-04 | open | Как должны выполняться activation непосредственно в container и последующие transfer/shipment active KIZ? | Product + WMS owner | Намеренно вне loose-only activation scope |
## FBS KIZ Stage 2

## Проверки после объединения веток — 2026-10-20

- Конкурентные assignment и explicit transfer/ship могут образовывать deadlock из-за
  порядка блокировок location/inventory и FK. Ошибка воспроизведена также на исходной
  feature/kiz; изменение протокола блокировок требует отдельной задачи.
- Перед production deployment необходимо read-only подтвердить фактическое применение
  migration цепочек KIZ/container и receipt/activation/FBS. Общая цепочка проверяется
  integration tests на локальной одноразовой БД, а не повторным применением в production.

- Требует отдельного решения physical source/provenance для shipment active container KIZ; Stage 1 этот сценарий не реализует и не ослабляет существующие container guards.


## KIZ-01 — протокол MVCC (answered, 2026-09-06)

Разрешить ли служебный UPDATE inventory при assignment без изменения quantity для
создания MVCC-версии? Альтернатива — DB-enforced READ COMMITTED write contract.
Владелец: WMS + DB owner. [Подтверждённый конфликт и минимальная поправка](../proposals/kiz_v1_lock_protocol_review.md).
Ответ пользователя: служебный UPDATE inventory после FOR UPDATE разрешён; quantity/scope неизменны.
Реализовано и проверено реальными concurrency-тестами; вопрос закрыт.

## Container warehouse roots перед B3

B1 определяет warehouse как корневой ancestor `locations.path`. Перед реализацией B3
нужно подтвердить, что каждый production root действительно обозначает один warehouse,
и определить поведение для legacy location с неконсистентными `parent_location_id/path`.
Владелец: WMS domain owner + DB owner. B1 migration от этого ответа не зависит.

## Container Stage 3C open business conditions

1. Нужно ли в будущем разрешать shipment непосредственно из контейнера?
2. Нужно ли kit assembly/disassembly внутри контейнера?
3. Нужно ли re-sorting внутри контейнера?
4. Нужен ли receipt directly into container?
5. Нужны ли nested containers?
6. Нужен ли generic movement endpoint как public API после миграции всех consumers?
7. Когда runtime приложение переводится с superuser на ограниченную DB role?
8. Нужны ли account/owner restrictions для container contents?
9. Какие правила KIZ действуют при будущем container shipment?

## Container Stage 3C after C1

Открыты отдельные будущие решения: shipment/FBS/kit/re-sorting/receipt непосредственно в
контейнере, direct assignment, arbitrary container-to-container KIZ transfer, nesting,
batch-aware KIZ, account/owner restrictions и KIZ-aware adjustment. C1 их не реализует.
