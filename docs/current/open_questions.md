# Open Questions

Статус: `CURRENT`. Последний triage: 2026-08-08.

Статусы: `open` — требуется решение или проверка; `answered` — ответ подтверждён
источником; `accepted-risk` — ограничение осознанно принято; `obsolete` — вопрос потерял
актуальность. Владелец указывается как ответственная роль, пока конкретный человек или
issue tracker не назначены.

| ID | Статус | Вопрос | Владелец | Основание / связь |
|---|---|---|---|---|
| DB-01 | answered | Runtime подтверждает `ltree` 1.3 в схеме `wms`; источник provisioning/migration её создания не установлен. | DB owner | Runtime audit; `wms_schema.sql` не содержит `CREATE EXTENSION` |
| DB-02 | open | Какая стабильная ссылочная модель нужна для partitioned `wms.movements` без global PK по `movement_id`? | WMS + DB owner | `known_issues.md`, пункты 4 и 8 |
| DB-03 | open | Нужны ли FK для container refs, snapshots и `tasks.related_movement_id`? | WMS + DB owner | `known_issues.md`, пункты 5, 8 и 10 |
| DB-04 | answered | Runtime constraint positive quantity применён как `NOT VALID`; найдена одна legacy violation. | DB owner | Runtime audit 2026-08-08 |
| DB-05 | answered | Runtime side constraint применён как `NOT VALID`; legacy violations не найдены. | DB owner | Runtime audit 2026-08-08 |
| CON-01 | open | Достаточна ли защита от отрицательного inventory в trigger и нужен ли явный pre-check? | WMS + DB owner | `known_issues.md`, пункт 9; runtime concurrency test |
| CON-02 | open | Где нужны row/advisory locks и единый порядок их получения? | WMS owner | `known_issues.md`, пункт 9 |
| CON-03 | open | Как сериализовать параллельные `unpack_from_container`? | WMS + DB owner | `known_issues.md`, пункты 1 и 9 |
| CON-04 | open | Как сериализовать параллельные FBS retry одной позиции? | WMS owner | External FBS tech debt в `known_issues.md` |
| CON-05 | open | Нужны ли advisory locks по ключу физического остатка для критичных списаний? | WMS + DB owner | Runtime concurrency test |
| LOC-01 | open | Где enforce диапазон и смысл `locations.level`? | WMS owner | Код + runtime schema audit |
| LOC-02 | open | Запрещать ли дочернюю локацию под неактивным parent? | Product + WMS owner | Требуется бизнес-решение |
| LOC-03 | open | Запрещать ли размещение/движение в неактивную location? | Product + WMS owner | Требуется бизнес-решение |
| LOC-04 | open | Запретить reparent узла с потомками или каскадно обновлять LTREE path? | WMS + DB owner | `known_issues.md`, пункт 7 |
| LOC-05 | open | Нужно ли менять `location_code` при rename/reparent? | Product + WMS owner | Требуется бизнес-решение |
| LOC-06 | open | Каков полный контракт доступной ячейки и capacity reservation? | Product + WMS owner | `known_issues.md`, пункт 6 |
| CNT-01 | open | Исправлять полную распаковку через `removed/replaced` или менять constraints под `quantity=0/status=empty`? | Product + WMS + DB owner | `known_issues.md`, пункт 1 |
| CNT-02 | open | Какие статусы контейнера должны проверяться DB function/trigger? | Product + WMS owner | Runtime behavior audit |
| CNT-03 | open | Должно ли перемещение parent container каскадно перемещать children и inventory? | Product + WMS owner | Требуется бизнес-решение |
| CNT-04 | open | Должна ли `block_empty_container` блокировать container/contents при проверке? | WMS + DB owner | `known_issues.md`, пункт 9 |
| FBS-01 | open | Нужны ли DB constraints для положительного `task_items.quantity_planned` и непустой task? | WMS + DB owner | Application schema проверяет вход, DB snapshot — нет |
| FBS-02 | open | Какие task transitions требуют row lock? | WMS owner | Runtime concurrency test |
| FBS-03 | open | Как структурно связывать task movements с task/items? | WMS + DB owner | `known_issues.md`, пункты 4 и 8 |
| FBS-04 | open | Нужен ли retry claim через `FOR UPDATE SKIP LOCKED`? | WMS owner | External FBS tech debt в `known_issues.md` |
| FBS-05 | accepted-risk | FBS сейчас списывает агрегированный available остаток без batch/container dimensions. | Product + WMS owner | Текущий pipeline; расширение требует отдельного решения |
| TEST-01 | answered | Production `vector_db` нельзя использовать; нужен отдельный development/stage клон и выделенные fixtures. | QA + DB owner | Подтверждение владельца 2026-08-08; `runtime_concurrency_plan.md` |
| TEST-02 | open | Добавить ли regression-тест полной/частичной распаковки после выбора модели CNT-01? | QA + WMS owner | Блокируется решением CNT-01 |
