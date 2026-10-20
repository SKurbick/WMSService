# Целевой технический аудит WMS перед разработкой инвентаризации

Дата аудита: 2026-10-18

Репозиторий: `/home/skurbick/PROJECTS/WMSService`

Ветка рабочего дерева: `feature/kiz-fbs-integration`

Проверяемый код: текущее рабочее дерево, включая уже находившиеся в нём незакоммиченные изменения

Предыдущий аудит: [inventory_module_readiness_audit_2026-10-18.md](/home/skurbick/PROJECTS/WMSService/docs/audit/inventory_module_readiness_audit_2026-10-18.md:1)

## Методика и границы достоверности

- **Подтверждено кодом** — вывод прослежен по текущим Python/SQL-файлам и тестам.
- **Подтверждено архивным DDL** — вывод относится к сохранённому production dump от 08.08.2026, а не автоматически к действующей БД.
- **Подтверждено актуальной БД** — таких выводов нет: явно предоставленного read-only подключения не было, к production подключение не выполнялось.
- **Рекомендация** — проектное решение, основанное на обнаруженном контракте; перед миграцией требует production-проверок из раздела F.
- Репозиторий не изменялся, кроме создания этого запрошенного файла отчёта. Тесты, миграции и SQL не запускались.

## A. Краткое заключение

### Итог

Текущий WMS **готов к разработке каркаса** отдельного документа инвентаризации, фиксации области, попыток пересчёта, ревизий и режима без применения остатков. Он **не готов к безопасному включению корректировок** до сверки действующей PostgreSQL и устранения/изоляции расходного `adjust` без подходящей строки остатка.

Уже можно проектировать и разрабатывать additive-часть: документы, зафиксированные локации, строки `product_id + location_id`, попытки пересчёта, согласуемые ревизии, историю решений, непересекающиеся активные области и shadow mode. Существующие `wms.tasks` и `wms.inventory_snapshots` не являются достаточной моделью этого процесса; это подтверждено предыдущим аудитом и их фактическим контрактом ([tasks](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:570), [snapshot query](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:172)).

Безопасные корректировки блокируют:

1. **Не подтверждён runtime-контракт БД.** Архивный DDL старше текущего KИЗ/FBS-кода; текущая ветка использует `wms.kiz` и `wms.movement_registry`, но базовые миграции этих объектов в текущем дереве неполны, а README миграций прямо не доказывает deployment ([README](/home/skurbick/PROJECTS/WMSService/scripts/migrations/README.md:1), [KИЗ-аудит](/home/skurbick/PROJECTS/WMSService/docs/audit/kiz_progress_business_source.md:32)).
2. **Архивный `update_inventory_from_movement()` допускает фиктивный расходный `adjust`.** При отсутствии подходящей inventory row `UPDATE` затрагивает 0 строк, но исключение предусмотрено только для `ship`, `transfer`, `kit_assembly`, `kit_disassembly`; movement остаётся успешным ([функция](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:659), [ветка проверки](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:708)).
3. **`NULL` и `''` опасно смешаны.** Расходный UPDATE сравнивает `COALESCE(batch_number, '')` и `COALESCE(container_code, '')`, тогда как unique constraint различает эти значения; при наличии обеих строк один movement способен сопоставить больше одной строки ([UPDATE](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:694), [unique NULLS NOT DISTINCT](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:3165)).
4. **Агрегат `product_id + location_id` не определяет внутренний источник недостачи.** При нескольких batch/loose/KИЗ-слоях нельзя доказательно выбрать физическую строку для списания.
5. **Delayed FBS не имеет надёжной физической временной границы.** Входное сообщение сохраняется и ACK выполняется до бизнес-списания ([consumer](/home/skurbick/PROJECTS/WMSService/app/consumer.py:33)); поздний DB movement может относиться к физическому событию до пересчёта.

### Рекомендация по типу движения

Для MVP рекомендуется **вариант A: сохранить `movement_type='adjust'`**, но разрешать инвентаризационное применение только через отдельный внутренний атомарный сценарий с неизменяемыми `source_type/source_id/source_item_id`, отдельной таблицей связи/идемпотентности и специальной DB-валидацией, срабатывающей только для этого source. Смысл обычного `adjust` и публичный endpoint не менять.

Причина — не эстетика, а обратная совместимость. Текущие `MovementCreate`, `MovementCreateResponse`, `MovementResponse` и фильтр используют закрытый Python `MovementType` ([schema](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:9), [responses](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:108), [filter](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:159)). После появления строки `inventory_adjustment` старая версия приложения способна упасть при сериализации GET movements. Откат приложения на неё становится небезопасным. При `adjust` диагностическое разделение обеспечивается source-связью, а старые readers продолжают работать.

`inventory_adjustment` имеет смысл рассматривать вторым этапом после того, как все readers станут forward-compatible, подтверждены parent/partition constraints и подготовлен необратимый после первой записи deployment protocol. Сам по себе новый type не исправляет триггер: неизвестный тип в архивной функции попадает в ту же ветку фиктивного расхода.

## B. Проверенный контракт БД

| Объект | Фактическое состояние | Источник подтверждения | Расхождения с документацией/runtime | Влияние на инвентаризацию |
|---|---|---|---|---|
| `wms.movements` | В архиве partitioned by `created_at`; `quantity numeric(10,2)`; есть source-поля; CHECK содержит 10 типов | **Архивный DDL:** [таблица](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1391) | Runtime не проверен; Python enum содержит `write_off`, но не содержит архивные `putaway/pick` ([enum](/home/skurbick/PROJECTS/WMSService/app/core/enums.py:18)) | Новый type требует согласования Python, parent и всех partitions; несовпадение уже существует |
| Партиции movements | В архиве отдельные monthly tables, CHECK и индексы; миграции меняли ограничения неодинаковыми способами | **Архивный DDL:** [первая partition](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1502); **миграции:** [kit](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20260707_add_kit_operations.sql:10), [re-sort](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20260715_add_re_sorting_operations.sql:44) | Не проверены active/default/future partitions, `conislocal`, `convalidated` | Вариант B нельзя выпускать до каталожной проверки каждой partition и процесса создания будущих |
| `wms.inventory` | `numeric(10,2)`, статус, batch/container dimensions, nonnegative CHECK, unique `NULLS NOT DISTINCT` по полному ключу | **Архивный DDL:** [table](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1039), [unique](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:3165) | Runtime/data не проверены | Пользовательская строка агрегирует внутренние строки; корректировка обязана сохранять измерения |
| `update_inventory_from_movement()` | Incoming — UPSERT exact key; outgoing — UPDATE с `COALESCE`; missing-row exception не включает `adjust`; затем удаляет nonpositive rows данного product/location | **Архивный DDL:** [function](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:659) | Не проверено действующее определение и KИЗ-guards | Главный blocker расходной корректировки; общий rewrite затрагивает всех writers |
| movement trigger | AFTER INSERT на `wms.movements` вызывает общую функцию | **Архивный DDL:** [trigger](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:5441) | Runtime не проверен | Ошибка триггера откатывает insert; специальный pre-guard можно ограничить inventory source |
| `movement_registry` | Текущий FBS/KИЗ-код требует стабильный `movement_ref` и ищет его по `(movement_id, created_at)` | **Код:** [lookup](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:345); **preflight:** [SQL](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261002_fbs_kiz_stage1_preflight.sql:1) | Объекта нет в архиве 08.08; deployment не подтверждён | Предпочтительная стабильная ссылка, только если existence, FK, trigger и coverage подтверждены |
| KИЗ | Текущий код блокирует KИЗ и loose inventory, сверяет active count; FBS-KИЗ имеет отдельный path | **Код:** [activation locks](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_activation.py:28), [FBS locks](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:180) | Архивный DDL старше KИЗ; runtime triggers не проверены | `container_code IS NULL` не исключает active KИЗ; расход должен учитывать holder lifecycle |
| `wms.locations` | Иерархия через `parent_location_id`/LTREE; состав дерева изменяем | **Архивный DDL и предыдущий аудит:** [locations analysis](/home/skurbick/PROJECTS/WMSService/docs/audit/inventory_module_readiness_audit_2026-10-18.md:230) | Runtime-consistency parent/path не проверена | На старте надо материализовать leaf/location IDs, а не повторно вычислять дерево |
| `inventory_snapshots` | Календарные строки без заголовка документа; назначение — ускорение истории | **Архивный DDL:** [table/comment](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1102); **code:** [insert](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:172) | Нет области, ревизии, связи с документом, batch completeness | Не использовать как authoritative inventory start snapshot |
| `mv_product_stock` | Агрегирует available по product; loose определяет только как `container_code IS NULL` | **Архивный DDL:** [view](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1809) | Не знает active KИЗ и location-level lines | Для построения области/строк непригодно |
| Integrity/recalculate | Ledger строится по сторонам всех movements без allow-list; recalculate прямо заменяет inventory | **Код:** [integrity](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:5), [calculation](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:81) | Runtime KИЗ-совместимость не проверена | Оба варианта типов попадут в ledger; maintenance меняет проекцию без нового movement |

### Реальный путь movement → inventory по архивному DDL

1. Приложение вставляет положительное quantity и направление. Публичная schema принимает только `int >= 1`, хотя БД хранит decimal ([schema](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:46)).
2. AFTER INSERT trigger вызывает `update_inventory_from_movement()`.
3. `to_location_id`: exact UPSERT в available row.
4. `from_location_id`: UPDATE подходящих строк. При существующей строке и недостатке quantity nonnegative CHECK должен откатить statement/transaction. При конкурентных UPDATE PostgreSQL row locks сериализуют writers; второй расход увидит изменённую row и также должен получить CHECK error.
5. При отсутствующей строке `FOUND=false`. Архивная функция отклоняет только перечисленные типы, но не `adjust`; следовательно расходный `adjust` может существовать без эффекта.
6. Неизвестный `inventory_adjustment` в неизменённой функции также не входит в exception list и наследует дефект.

Дробные остатки поддерживает БД, но не публичный Movement API. Для инвентаризации нужен внутренний Decimal-контракт; изменение типа quantity действующего endpoint необязательно и повышает риск регрессии.

## C. Сравнение архитектурных вариантов

| Критерий | A: `adjust` + inventory source | B: `inventory_adjustment` |
|---|---|---|
| Семантика | Тип общий, документ отличим по immutable source/link | Явно отдельный тип |
| DB changes | Additive document/link tables; специальный guard только для `adjust` с inventory source | Всё из A плюс CHECK parent/partitions, trigger/function support и future partition template |
| Старые writers | Не затрагиваются, если guard строго source-scoped | Старые writers продолжают писать известные types после расширения CHECK |
| Старые readers | Видят знакомый `adjust` | Текущий strict Pydantic enum может падать на GET rows нового типа |
| Shared trigger | Нельзя полагаться на текущую ветку `adjust`; нужен strict precondition | Новый type без special logic также фиктивно успешен при missing row |
| История | CASE уже знает `adjust`; документ диагностируется по source | Fallback вернёт raw type, но пользовательское имя потребует изменения ([history CASE](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/operations_history.py:154)) |
| Daily balances/recalc | Уже учитывается по направлению | Автоматически учитывается, поскольку allow-list нет |
| Reports | Сохраняется существующая классификация adjust | Top/ABC count types изменится; ship-only/receive-only отчёты не изменятся ([reports](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/reports.py:21)) |
| Идемпотентность | Не даётся type; обеспечивается unique apply/link identity | Также не даётся type; требуется та же unique identity |
| Rollback приложения | Старое приложение читает записи | После первой записи откат на текущий enum небезопасен |
| DB rollback | Additive objects можно оставить; физические movements не откатывать | Нельзя сузить CHECK, пока строки нового type существуют; удаления ledger запрещены |
| Тестовая стоимость | Средняя, основное — новый special path и все legacy adjust regressions | Высокая: всё из A плюс каждый consumer/reader/partition/deployment pairing |
| Диагностика | Надёжна через source и отдельную ссылку | Удобна по type, но source всё равно нужен для document/item identity |

### Однозначная рекомендация

Выбрать **A для MVP**, при четырёх условиях:

1. `source_type` зарезервирован для inventory и не принимается от действующего публичного endpoint.
2. Один применяемый item/revision имеет unique identity вне partitioned movements; повторный approval возвращает сохранённый результат либо conflict, но не пишет второй movement.
3. Перед расходом выполняется exact row/KИЗ validation и lock; missing row всегда ошибка.
4. Любая неоднозначность batch/NULL-empty/KИЗ переводит строку в recount/technical resolution, а не в эвристику распределения.

Преимущество B — удобная семантическая фильтрация — недостаточно, чтобы принять несовместимость readers и deployment. Вернуться к B можно после выпуска forward-compatible чтения типов и стабильной схемы partitions.

## D. Матрица обратной совместимости

| Операция/компонент | Зависимость от `movement_type` | Изменение для A / B | Риск | Проверка |
|---|---|---|---|---|
| `POST /api/movements` | Strict enum; int quantity; старый INSERT без source | A: не менять. B: enum/request | A низкий; B средний | Legacy contract tests + реальные adjust ± |
| `GET /api/movements` | Response strict enum | A: нет. B: сделать reader forward-compatible до записи нового type | B критический для rollback | Seed unknown type в совместимой fixture, проверить list/product endpoints |
| MovementService | Может работать на внешнем conn, но принимает public schema и не пишет source | Нужен отдельный internal method/repository; можно переиспользовать validation patterns ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:34)) | Средний | Все movements в общей transaction, source/result verified |
| DB trigger | Ветвится по type для missing-row; стороны задают delta | A: source-scoped pre-guard. B: new branch/guard | Высокий | PostgreSQL integration tests каждого legacy type |
| Parent/partitions | CHECK allow-list | A: нет type change. B: расширить и проверить все partitions | Высокий | Catalog query F.2 + insert rollback test в active/future partition |
| Operations history | Known CASE, ELSE raw type | A: существующее имя; отдельный document adapter опционален. B: добавить label | Низкий/средний | Existing unknown-type tests и document history |
| Daily inventory history | Не использует allow-list; учитывает стороны | Нет обязательного изменения | Низкий | [existing test](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_history.py:88) + correction balance |
| Integrity/recalculate | Не использует type allow-list | Оба варианта входят автоматически | Высокий из-за maintenance concurrency | Ledger equality after apply; concurrent recalc prohibited/tested |
| Top/ABC reports | Агрегируют все types/count distinct | A не меняет type count; B меняет метрики | Средний | Golden report до/после inventory movement |
| Turnover | Только `ship` | Нет | Низкий | Existing report test |
| Batch receipt analytics | Только `receive` | Нет | Низкий | Existing report test |
| Standard/HTTP/external FBS | Создают `ship`; retry ожидает эту семантику | Не менять payload/type | Высокий по concurrency, не по enum | delayed/retry/idempotency matrix |
| FBS KИЗ | Проверяет `ship`, movement registry, KИЗ lifecycle | Не менять; inventory path блокирует те же KИЗ/rows | Высокий | Mixed stock and concurrent shipment tests |
| Kit assembly/disassembly | Собственные types/source, transaction and locks | Не менять | Средний | Existing kit suite + overlap with approval |
| Re-sort | Два movements `re_sorting`, canonical advisory lock | Не менять | Средний | Existing suite + overlap/deadlock test |
| Containers/unpack | DB functions создают movements; unpack меняет container→loose | Не менять; event во время count инвалидирует line | Высокий | unpack during count, movement pair visibility |
| KИЗ activation/lifecycle | Может менять eligibility россыпи без обычного quantity movement | Inventory revision учитывает KИЗ event/version | Высокий | activate/unbind during count and approval |
| `mv_product_stock` | Читает inventory; loose = container NULL | Не использовать для scope/eligibility | Средний | Compare with KИЗ-aware diagnostic |
| Snapshots | Не зависит от type | Не использовать как document snapshot | Низкий | Schema contract only |
| Tasks/recount | Создают transfer/adjust отдельными вызовами | Не переиспользовать approval path | Высокий при смешении | Existing task tests unchanged |
| Старые интеграции | Представлены неполно | A сохраняет type; B требует consumer inventory | Не проверено | Поиск вне repo/contract registry обязателен |

### Полнота зависимости от movement type

Подтверждённые явные зависимости: Python enum и Pydantic ([enum](/home/skurbick/PROJECTS/WMSService/app/core/enums.py:18), [schemas](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:9)); DB CHECK и function branch ([DDL](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1409)); history CASE ([query](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/operations_history.py:155)); reports ([query](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/reports.py:28)); FBS `ship` validation ([repository](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_shipment_repository.py:103)); kit/re-sort/KИЗ repositories; tests and docs. Materialized stock, daily balance, integrity and recalculate работают по inventory/направлению и не имеют type allow-list.

Внешние BI/1С/SQL consumers в репозитории полностью не инвентаризированы — это **не проверено** и остаётся эксплуатационным вопросом для B.

## E. Конкурентность и ФБС

### Что гарантирует текущая реализация

- Обычный batch `POST /api/movements` выполняется в одной transaction, а внутренний метод принимает существующее connection ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:34)).
- Kit и re-sort также группируют свои movements с операцией; re-sort использует canonical advisory scope ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/re_sorting_operation_service.py:23)).
- KИЗ activation и FBS-KИЗ блокируют KИЗ/loose inventory, но advisory namespaces различаются (`kiz-activate:` и `fbs-kiz:`), поэтому считать их единым глобальным барьером нельзя ([activation SQL](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_activation.py:41), [FBS repository](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:187)).
- Existing inventory row `FOR UPDATE` способен сериализовать writers одной physical row. Для отсутствующей строки и агрегированной области это predicate lock не создаёт.
- `movement_id` и `created_at` идентифицируют событие, но не фиксируют порядок commit. `movement_registry` стабилизирует ссылку, но не temporal order.
- Standard FBS создаёт shipment/items и обрабатывает группы не как одну глобальную shipment transaction; consumer подтверждает RabbitMQ message после сохранения raw shipment, до завершения business write-off ([consumer](/home/skurbick/PROJECTS/WMSService/app/consumer.py:33), [handler](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:119)). Retry может записать `ship` существенно позже физического события.
- `recalculate-inventory` заменяет projection напрямую и не оставляет нового movement ([queries](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:81)).

### Сравнение механизмов

| Вариант | Реальная гарантия | Оставшиеся гонки | Изменения/совместимость | Оценка |
|---|---|---|---|---|
| Анализ movement delta между snapshot/count/approval | Корректен, если DB event надёжно расположен относительно физического count | Commit order, движение внутри count, delayed FBS, KИЗ eligibility и maintenance | Минимальные изменения | Недостаточен как единственная защита |
| Короткое ограничение конкретных locations | После подключения **всех** writers даёт физическое окно без WMS movements | Внешнее физическое действие до DB, writer, не знающий claim, maintenance | Требует интеграции каждого writer; не глобальная остановка | Сильный вариант для FBS/high-risk zones, вводить поэтапно |
| Версия остатка/location, меняемая каждым writer | Обнаруживает DB changes и закрывает TOCTOU при lock/recheck | Не объясняет момент физического FBS; пропущенный writer разрушает гарантию | Cross-cutting изменение всех writers/functions | Не вводить глобально для MVP без инвентаризации writers |
| Append-only inventory event sequence с commit marker | Даёт DB-порядок всех подключённых изменений | Та же физическая неоднозначность; высокая цена | Новый общий инфраструктурный контракт | Возможный долгосрочный этап, не MVP |
| Conservative invalidation + recount | Не применяет недоказуемую корректировку | Больше ручных recount | Additive, безопасно | Рекомендуемая базовая политика MVP |

### Рекомендуемый MVP-алгоритм

1. При старте документа одной transaction материализовать leaf `location_id`, создать exclusive active claims на них и снять initial eligible stock. Claim запрещает только вторую активную инвентаризацию, а не обычные движения.
2. Для каждой пары `product_id + location_id` хранить immutable count attempt: `started_at`, `submitted_at`, expected revision/evidence, actual и статус. Ноль хранить как значение, а «не пересчитано» — отдельным состоянием/NULL.
3. Eligibility первой версии: `status='available'`, без контейнера, за вычетом/исключением active KИЗ. Одного `container_code IS NULL` недостаточно. Snapshot хранит также внутренние physical components (batch/container normalization и KИЗ evidence), хотя UI агрегирует пару.
4. Любой релевантный DB movement или KИЗ/container eligibility event, попавший в интервал count attempt, помечает строку ambiguous и требует повторного пересчёта, если источник не предоставляет доказуемую физическую границу.
5. Движение строго после завершения count можно учесть как delta к ожидаемому и физическому target только при надёжной причинной границе. Пользователь подтверждает конкретную новую revision.
6. Approval transaction: lock document/revision; lock items и exact inventory/KИЗ rows в детерминированном порядке `(location_id, product_id, batch NULLS FIRST, container NULLS FIRST)`; повторно получить evidence/current rows; при изменении revision ничего не писать и вернуть refresh/recount; затем проверить все расходы, создать все movements/source links, записать решение и статус.
7. Любая ошибка откатывает документ, links и все movements. Последовательные HTTP-вызовы запрещены.
8. `recalculate-inventory` и аналогичный maintenance на охваченной области во время approval должны либо брать совместимый барьер, либо быть операционно запрещены и инвалидировать затронутые документы.
9. Включать apply feature flag сначала для обычных locations без active KИЗ/container/FBS. Для FBS zones — только после специального режима короткого location window и контроля in-flight/retry; глобально RabbitMQ не останавливать.

Такой MVP не обещает восстановить недоказуемое прошлое. Он гарантирует, что устаревшая revision не применится и ambiguous line будет пересчитана.

### Сценарий 1: было 10, насчитано 8, затем поступило 3

- Если `receive +3` доказуемо произошёл **после** `submitted_at` count attempt: исходное расхождение `8-10=-2`; новая ожидаемая величина 13, ожидаемый физический target 11. Система может предложить adjustment `-2`, показать delta `+3` и потребовать подтверждение новой revision.
- Если receive мог физически происходить во время count, одного DB timestamp недостаточно. Строка переводится в recount.
- В approval transaction новый movement после последней проверки не проскочит незамеченным, если обе операции конфликтуют по тем же locked rows; при отсутствии строки/иной dimension нужен дополнительный scope lock/claim.

### Сценарий 2: было 10, физически отгрузили 2, насчитано 8, consumer позже записал `ship -2`

Фактически расхождения нет. Однако ledger показывает count 8 до позднего movement и может ошибочно вывести shortage `-2`, а затем ещё учесть ship. Из `created_at` позднего movement невозможно доказать время физической отгрузки; `received_at` означает приём сообщения, не физический момент. Автоматическая реконструкция **невозможна** без доверенного immutable external event time/ID и доказанного protocol.

Безопасное поведение: дождаться завершения in-flight/retry для location, показать позднее FBS событие ответственному и повторно пересчитать только эту строку. Альтернатива для FBS — короткое блокирование write-off/физической операции конкретной location, а не всей очереди/WMS. Простое «прибавить все movements после count» здесь запрещено.

### Атомарность и большие документы

Существующий `MovementService.create_movement_in_transaction()` подтверждает возможность работы на переданном connection, но напрямую непригоден: public `MovementCreate` не содержит source metadata и ограничивает quantity integer ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:55), [legacy INSERT](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/movements.py:5)). Нужен внутренний method/repository с Decimal и source identity; он может переиспользовать location validation, но не публичный HTTP-контракт.

Для больших документов подготовку/валидацию UI можно делать частями, но финальный apply остаётся одной transaction. Нужны разумный лимит строк документа, `lock_timeout`, детерминированный lock order и метрики длительности. Если размер не укладывается в безопасное окно, документ делится по непересекающимся locations **до старта**, а не коммитится частями. Insert в отсутствующую future movement partition должен быть preflight-проверен до включения apply.

### Batch и агрегированная строка UI

Если одна UI-строка состоит из нескольких physical rows с разными `batch_number`, недостача не сообщает, из какой партии она исчезла. Нельзя пропорционально/FIFO распределять без принятого бизнес-правила. Для MVP:

- auto-apply разрешён только при одной однозначной eligible physical row;
- при нескольких rows система показывает internal breakdown и требует технического выбора/повторного пересчёта;
- излишек может создаваться в loose `batch_number IS NULL` только после явного утверждения такого правила бизнесом;
- строки active KИЗ не участвуют в корректируемом количестве; если loose physical quantity и active KИЗ образуют mixed stock, корректируется только доказуемая unidentified часть.

## F. Проверки production PostgreSQL

Все запросы ниже read-only. Они **не выполнялись**. Запускать только с явно выданным read-only пользователем. Начинать отдельную диагностическую сессию так:

```sql
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SET LOCAL idle_in_transaction_session_timeout = '30s';
-- один или несколько безопасных запросов
COMMIT;
```

Не вызывать прикладные функции. `pg_get_functiondef` ниже только читает системный каталог и не исполняет исследуемую функцию.

### F.1. Версия, объекты и признаки migration tracking — низкая нагрузка

**Проверяет:** server/runtime identity и наличие критических объектов. **Ожидается:** movements/inventory/locations и, для текущего KИЗ-кода, kiz/movement_registry. **Отклонение:** отсутствующий registry/KИЗ блокирует соответствующий design; найденная migration table позволяет сверить применённые версии.

```sql
SELECT current_database(), current_user, version(),
       current_setting('transaction_isolation'),
       current_setting('server_version_num');

SELECT n.nspname, c.relname, c.relkind, c.relispartition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'wms'
  AND c.relname IN ('movements','inventory','locations','inventory_snapshots',
                    'movement_registry','kiz','mv_product_stock')
ORDER BY c.relname;

SELECT schemaname, tablename
FROM pg_tables
WHERE tablename ~ '(alembic|migration|schema.*version)'
ORDER BY schemaname, tablename;
```

### F.2. Партиции, constraints и индексы movements — низкая нагрузка

**Проверяет:** parent/children, bounds, локальность/валидность CHECK, определения constraints и индексов. **Ожидается:** active и future partition, одинаковый разрешённый набор типов, validated constraints. **Отклонение:** любой child с отличающимся CHECK или отсутствующая future partition блокирует B и apply deployment.

```sql
SELECT pt.level, pt.relid::regclass AS relation,
       pg_get_expr(c.relpartbound, c.oid) AS partition_bound
FROM pg_partition_tree('wms.movements'::regclass) pt
JOIN pg_class c ON c.oid = pt.relid
ORDER BY pt.level, relation::text;

SELECT con.conrelid::regclass AS relation, con.conname, con.contype,
       con.convalidated, con.conislocal, con.coninhcount,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
WHERE con.conrelid IN (SELECT relid FROM pg_partition_tree('wms.movements'::regclass))
ORDER BY relation::text, con.conname;

SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'wms'
  AND tablename IN (
      SELECT relid::regclass::text::text
      FROM pg_partition_tree('wms.movements'::regclass)
  )
ORDER BY tablename, indexname;
```

Примечание: последнее сравнение имени может зависеть от `search_path`; при пустом результате использовать catalog join по `pg_index.indrelid` вместо заключения об отсутствии индексов.

### F.3. Колонки, triggers и точные определения функций — низкая нагрузка

**Проверяет:** фактические types/defaults, все non-internal triggers и определения связанных функций. **Ожидается:** AFTER INSERT inventory trigger; KИЗ guards, если этап развёрнут. **Отклонение:** сравнить definition с архивом построчно; наличие дополнительных guards меняет выводы о расходе.

```sql
SELECT a.attrelid::regclass AS relation, a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS default_expr
FROM pg_attribute a
LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
WHERE a.attrelid IN ('wms.movements'::regclass, 'wms.inventory'::regclass,
                     'wms.locations'::regclass)
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY relation::text, a.attnum;

SELECT t.tgrelid::regclass AS relation, t.tgname,
       pg_get_triggerdef(t.oid, true) AS trigger_definition,
       t.tgfoid::regprocedure AS function_identity
FROM pg_trigger t
WHERE NOT t.tgisinternal
  AND t.tgrelid IN ('wms.movements'::regclass, 'wms.inventory'::regclass,
                    'wms.kiz'::regclass)
ORDER BY relation::text, t.tgname;

SELECT p.oid::regprocedure AS identity, pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='wms'
  AND p.proname IN ('update_inventory_from_movement','move_container_inventory',
                    'register_container','unpack_from_container')
ORDER BY identity::text;
```

Если `wms.kiz` отсутствует, убрать его cast из trigger query: `::regclass` отсутствующего объекта завершает только запрос ошибкой, ничего не меняя.

### F.4. Недавний runtime-контракт movements — низкая/средняя нагрузка с partition pruning

**Проверяет:** реально встречающиеся типы/source, decimals и malformed sides за 30 дней. **Ожидается:** только разрешённые types, quantity > 0, хотя бы одна сторона; source coverage соответствует операциям. **Отклонение:** неизвестный type должен быть добавлен в compatibility matrix; zero/negative/both-null — integrity incident.

```sql
SELECT movement_type, source_type, count(*) AS rows,
       count(*) FILTER (WHERE quantity <> trunc(quantity)) AS fractional_rows,
       min(created_at), max(created_at)
FROM wms.movements
WHERE created_at >= now() - interval '30 days'
GROUP BY movement_type, source_type
ORDER BY movement_type, source_type NULLS FIRST;

SELECT movement_id, created_at, movement_type, product_id, quantity,
       from_location_id, to_location_id, batch_number, container_code
FROM wms.movements
WHERE created_at >= now() - interval '30 days'
  AND (quantity <= 0 OR (from_location_id IS NULL AND to_location_id IS NULL))
ORDER BY created_at DESC
LIMIT 100;
```

### F.5. Inventory dimensions и данные — средняя нагрузка, полный scan inventory

**Проверяет:** batch/container NULL-vs-empty, fractions, inactive locations, распределение available и количество внутренних rows. **Ожидается:** empty string отсутствует; fractions согласованы бизнесом; inactive locations не содержат активный остаток; mixed dimensions известны. **Отклонение:** alias collision блокирует расходный inventory adjustment; multi-row pairs требуют allocation policy.

```sql
SET LOCAL statement_timeout = '15s';

SELECT
  count(*) AS rows_total,
  count(*) FILTER (WHERE status='available') AS available_rows,
  count(*) FILTER (WHERE status='available' AND quantity > 0) AS positive_available,
  count(*) FILTER (WHERE batch_number IS NOT NULL AND batch_number <> '') AS nonempty_batch,
  count(*) FILTER (WHERE batch_number = '') AS empty_batch,
  count(*) FILTER (WHERE container_code IS NULL) AS null_container,
  count(*) FILTER (WHERE container_code = '') AS empty_container,
  count(*) FILTER (WHERE container_code IS NOT NULL AND container_code <> '') AS in_container,
  count(*) FILTER (WHERE quantity <> trunc(quantity)) AS fractional_rows
FROM wms.inventory;

SELECT product_id, location_id, status,
       count(*) AS physical_rows,
       array_agg(DISTINCT batch_number) AS batches,
       array_agg(DISTINCT container_code) AS containers,
       sum(quantity) AS quantity
FROM wms.inventory
WHERE status='available' AND quantity > 0
GROUP BY product_id, location_id, status
HAVING count(*) > 1
ORDER BY physical_rows DESC
LIMIT 200;

SELECT product_id, location_id, status,
       bool_or(batch_number IS NULL) AS has_null_batch,
       bool_or(batch_number = '') AS has_empty_batch,
       bool_or(container_code IS NULL) AS has_null_container,
       bool_or(container_code = '') AS has_empty_container
FROM wms.inventory
GROUP BY product_id, location_id, status
HAVING (bool_or(batch_number IS NULL) AND bool_or(batch_number = ''))
    OR (bool_or(container_code IS NULL) AND bool_or(container_code = ''))
LIMIT 200;

SELECT i.product_id, i.location_id, i.quantity, l.location_code, l.is_active
FROM wms.inventory i
JOIN wms.locations l ON l.location_id=i.location_id
WHERE i.status='available' AND i.quantity > 0 AND NOT l.is_active
LIMIT 200;
```

Поле активности надо сверить по F.3: если runtime использует другое имя, адаптировать последний query, а не делать вывод по ошибке.

### F.6. Иерархия locations — средняя нагрузка

**Проверяет:** parent/path relation. **Ожидается:** parent path — непосредственный ancestor, depth отличается на 1; root semantics единообразны. **Отклонение:** scope expansion по path ненадёжен до исправления данных/правила.

```sql
SELECT l.location_id, l.location_code, l.parent_location_id,
       l.path AS child_path, p.path AS parent_path
FROM wms.locations l
LEFT JOIN wms.locations p ON p.location_id=l.parent_location_id
WHERE l.parent_location_id IS NOT NULL
  AND (p.location_id IS NULL
       OR NOT (p.path @> l.path)
       OR nlevel(l.path) <> nlevel(p.path) + 1)
LIMIT 200;
```

### F.7. KИЗ и loose stock — запускать только если F.1 подтвердил таблицы

Названия status/holder columns необходимо сначала сверить F.3 или миграциями действующей версии. Ниже — шаблоны; **не запускать вслепую**. Проверяется: active KИЗ без holder, active count > loose physical, mixed unidentified+active stock. Отклонение блокирует auto-adjust соответствующей pair.

```sql
-- Адаптировать имена колонок к pg_attribute до запуска.
SELECT count(*) AS registered_without_active_holder
FROM wms.kiz k
WHERE k.status = 'registered'
  AND k.current_location_id IS NULL;

WITH physical AS (
  SELECT product_id, location_id, sum(quantity) AS qty
  FROM wms.inventory
  WHERE status='available' AND container_code IS NULL
  GROUP BY product_id, location_id
), active_kiz AS (
  SELECT product_id, current_location_id AS location_id, count(*)::numeric AS qty
  FROM wms.kiz
  WHERE status='active' AND current_location_id IS NOT NULL
  GROUP BY product_id, current_location_id
)
SELECT p.product_id, p.location_id, p.qty AS physical_qty,
       coalesce(k.qty,0) AS active_kiz_qty,
       p.qty-coalesce(k.qty,0) AS unidentified_qty
FROM physical p
LEFT JOIN active_kiz k USING(product_id, location_id)
WHERE coalesce(k.qty,0) > p.qty
   OR (coalesce(k.qty,0) > 0 AND p.qty-coalesce(k.qty,0) > 0)
LIMIT 200;
```

### F.8. movement_registry — низкая для recent, высокая для полного coverage

**Проверяет:** constraints/triggers и coverage последних 7 дней. **Ожидается:** одна registry row на movement identity. **Отклонение:** registry нельзя использовать как FK-источник inventory links до backfill/repair.

```sql
SELECT conname, contype, convalidated, pg_get_constraintdef(oid, true)
FROM pg_constraint
WHERE conrelid='wms.movement_registry'::regclass
ORDER BY conname;

SELECT t.tgname, pg_get_triggerdef(t.oid, true)
FROM pg_trigger t
WHERE t.tgrelid='wms.movements'::regclass AND NOT t.tgisinternal
ORDER BY t.tgname;

SELECT count(*) AS missing_recent_registry_rows
FROM wms.movements m
LEFT JOIN wms.movement_registry r
  ON r.movement_id=m.movement_id AND r.created_at=m.created_at
WHERE m.created_at >= now()-interval '7 days'
  AND r.movement_ref IS NULL;
```

### F.9. Тяжёлая сверка ledger — только replica/maintenance window, не запускать автоматически

**Проверяет:** полный movement ledger против inventory по exact dimensions. **Нагрузка:** полный scan всех partitions, aggregate и full outer join; может быть высокой. **Ожидается:** 0 rows. **Отклонение:** сначала классифицировать maintenance/KИЗ/legacy причины; не запускать recalculate автоматически.

Использовать read-only `VALIDATE_INTEGRITY` из [system.py](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:5), предварительно выполнить обычный `EXPLAIN` без `ANALYZE`. Не вызывать endpoint `recalculate-inventory`.

Исторически доказать «фиктивный adjust при отсутствующей row» только по текущему inventory нельзя: последующие движения меняют состояние. Для доказательства нужны audit logs/point-in-time replica либо контролируемый transaction test на отдельной тестовой БД с `ROLLBACK`.

## G. План изменений без реализации

Ниже — предварительная совместимая последовательность, не готовые миграции и не команда на реализацию.

### Этап 0. Runtime preflight и фиксация контракта

1. Выполнить F.1–F.8 read-only на production/актуальном clone.
2. Сохранить definitions parent/partitions, inventory/movement/KИЗ triggers, registry coverage и dimension diagnostics.
3. Инвентаризировать внешние consumers movement type за пределами репозитория.
4. На отдельной disposable PostgreSQL воспроизвести missing adjust, insufficient stock, NULL/empty collision и concurrent spend. Production для этого не использовать.

Gate: до закрытия runtime definition, alias collision и KИЗ contract apply остаётся выключенным.

### Этап 1. Additive domain schema

Предварительно необходимы отдельные сущности (имена рабочие):

- document: warehouse, type, lifecycle status, immutable started/approval revisions, creator/decision metadata;
- scope selection до старта и materialized document locations после старта;
- active location claims, обеспечивающие non-overlap;
- document items `product_id + location_id` с initial expected и eligibility summary;
- immutable count attempts и line revisions;
- approval decisions/rejections/recount transitions;
- adjustment applications/links с unique idempotency key и стабильной movement reference.

Не переиспользовать `tasks` как aggregate root: существующий discrepancy path создаёт transfer/adjust по иной семантике и не имеет document-wide atomic approval ([task service](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:570)). `tasks` можно позднее использовать только как исполнительное поручение, связанное с документом.

Не переиспользовать `inventory_snapshots`: таблица не хранит scope/document/revision/internal eligibility. Initial values должны быть частью immutable document revision.

Active overlap необходимо обеспечивать в БД, не pre-check API. Практичный additive вариант — одна active-claim row на `location_id` с unique/PK location и FK document; создание всех claims — transaction. При закрытии claims удаляются/деактивируются в той же transaction. Partial scope по SKU всё равно claims все выбранные locations согласно бизнес-правилу запрета пересечения.

### Этап 2. Backend только для чтения/counting

Добавить API жизненного цикла: create/edit scope; preview; start; get lines; submit count attempt; refresh/revision; submit for approval; reject-close; return selected affected lines to recount. На этом этапе `INVENTORY_MODULE_ENABLED=true`, `INVENTORY_APPLY_ENABLED=false`.

Snapshot start выполняется одной transaction с materialization locations and claims. При `READ COMMITTED` каждый statement видит свой snapshot, поэтому scope materialization и inventory capture должны использовать одну coherent strategy: либо `REPEATABLE READ` transaction плюс conflict/retry, либо explicit locks/version validation. `REPEATABLE READ` сам не блокирует новые writers и не решает post-snapshot movements.

### Этап 3. Source-scoped safety contract для варианта A

Additive DB validation должна действовать только на `movement_type='adjust' AND source_type=<inventory>`:

- проверять обязательные document/item/revision source IDs;
- запрещать both-null/both-filled sides, если inventory correction должна быть односторонней;
- exact comparison через `IS NOT DISTINCT FROM`, не расширяющий `NULL` до `''`;
- для outgoing блокировать exact row, отклонять missing/insufficient quantity;
- учитывать active KИЗ/holder guard;
- отклонять alias collision, пока данные/общая функция не нормализованы;
- обеспечивать uniqueness application вне partition key.

Предпочтительно отдельный BEFORE guard для inventory source и неизменённый legacy AFTER updater: это сужает blast radius. Но применимость этого подхода зависит от runtime trigger order и KИЗ guards. Замена общей `update_inventory_from_movement()` допустима только после regression matrix всех writers.

### Этап 4. Внутреннее атомарное approval

Новый internal service принимает connection, Decimal и source metadata. Порядок одной transaction:

1. lock document и approved revision;
2. получить/создать idempotency application row;
3. lock items и scopes в canonical order;
4. перечитать movements/KИЗ/current inventory и доказать revision current;
5. проверить все расходные physical components до первой вставки;
6. вставить все movements и получить registry identities;
7. записать immutable links/results;
8. перевести document в approved/applied и освободить claims;
9. commit.

Unique key уровня `(document_id, approval_revision)` и/или `(document_item_id, approved_revision)` должен исключать второй физический эффект. Повтор с тем же idempotency key возвращает прежний результат; повтор с отличающимся payload/revision — `409`, не новая корректировка.

### Этап 5. Совместимый deployment

1. DB migration создаёт только additive tables/indexes/guard, распознаваемые старой версией как неиспользуемые.
2. Deploy приложения с обоими flags выключенными.
3. Выполнить schema/read-only smoke и legacy regression.
4. Включить module без apply; провести shadow documents и сравнить с inventory/ledger.
5. Включить apply на пилотных обычных locations без batch/KИЗ/container/FBS ambiguity.
6. Расширять eligibility по результатам метрик; FBS — отдельный gate.

Старая версия приложения совместима с расширенной БД варианта A, пока source-scoped trigger не затрагивает старые inserts. После первых inventory adjustments rollback приложения означает выключение новых flags/возврат к предыдущей версии; уже записанные movements не удаляются и не «откатываются» обратными DML. Additive tables/guard можно оставить до отдельного change window.

Для B порядок иной: сначала все readers делают forward-compatible, затем расширяется CHECK parent/future partitions, затем writer deploy, и лишь потом flag. После первой записи нового type откат допустим только на forward-compatible reader version. Сужение CHECK/удаление rows не является rollback strategy.

### Этап 6. FBS/KИЗ/container

- Подключить event evidence для KИЗ activation/holder changes и container unpack.
- Добавить location-scoped короткое окно для FBS или trusted external physical timestamp protocol.
- Проверить standard, external, HTTP и retry paths отдельно; конфигурация допускает разные режимы ([settings](/home/skurbick/PROJECTS/WMSService/app/shared/config.py:42)).
- Только после этого разрешать apply для соответствующих zones.

### Наблюдаемость и эксплуатация

Обязательны метрики: active claims age, ambiguous/recount ratio по source, stale approvals, idempotent replays, adjustment failures по SQLSTATE, transaction/lock duration, deadlocks, FBS in-flight/retry age и registry misses. Audit должен показывать document/revision/item ↔ stable movement identity, а не только свободный `reason`.

## H. Тестовая матрица

Все DB integration tests выполнять на изолированной БД. Existing tests, способные пересоздавать schema/data, на production не запускать.

| Группа | Обязательный сценарий | Ожидаемая гарантия |
|---|---|---|
| Legacy adjust | Положительный/отрицательный `adjust` до и после миграции | Старый endpoint/response и фактический delta неизменны |
| Missing row | Legacy outgoing adjust зафиксировать как baseline; inventory-source outgoing без row | Новый special path полностью отклонён; movement/link/status отсутствуют |
| Insufficient | Row меньше расхода | Вся approval transaction rollback |
| Positive correction | Excess в однозначную loose row | Movement, inventory, link, status коммитятся вместе |
| Negative correction | Shortage из однозначной row | Exact row уменьшается один раз |
| NULL/empty | Отдельные NULL и `''` batch/container rows | Inventory path не затрагивает обе; collision rejected |
| Decimal | `0.01`, максимальная scale/precision, rounding boundary | Decimal сохраняется без int/float loss либо валидно отклоняется контрактом |
| Concurrency | Два расхода одной row | Не возникает negative/fake success; один ждёт/ошибается корректно |
| Concurrency | Movement между line validation и approval insert | Revision/lock recheck ловит изменение, stale apply отсутствует |
| Scope | Два старта одной location одновременно | Ровно один получает active claim |
| Hierarchy | Parent zone меняется после старта | Document locations не меняются |
| Multi-batch | Одна UI pair, две batch rows | Auto-apply запрещён без принятой allocation rule |
| KИЗ mixed | Physical loose содержит active и unidentified | Корректируется только доказуемая eligible часть; guard не нарушен |
| KИЗ lifecycle | Activation/unbind во время count/approval | Line invalidated/recount или serialized |
| Container | Unpack container→loose во время count | Line invalidated, оба movements видимы |
| FBS delayed | Физический ship до count, DB ship после count | Нет автоматической двойной недостачи; обязательный recount |
| FBS retry | Одно сообщение/retry/duplicate workers | Один ship effect; inventory revision обновлена один раз |
| Standard/external/HTTP FBS | Каждый path отдельно | Existing payload/status/movements совместимы |
| Approval replay | Тот же idempotency key дважды | Второй ответ возвращает сохранённый result, movement не дублируется |
| Approval conflict | Тот же key с другим revision/payload | Conflict без side effect |
| Atomic rollback | Ошибка последнего из N movements | 0 из N movements, links/status неизменны |
| Deadlock | Approval, ship, kit, re-sort берут overlap rows | Нет циклического order либо controlled retry |
| Large document | Максимальный поддерживаемый размер | Укладывается в lock/statement timeout, коммит один |
| History | Inventory correction в operation history/detail | Стабильное имя/source, неизвестные types не ломают reader |
| Integrity | Validate/recalculate ledger после correction | Ledger совпадает; correction учитывается ровно один раз |
| Reports | Top/ABC/turnover/batch reports | Зафиксировано ожидаемое влияние; ship/receive semantics не изменены |
| Old app + new DB | Предыдущая версия пишет/читает все legacy types | Совместима при A; для B — до первой записи нового type либо после reader backport |
| New app + old DB | Flags выключены | Приложение стартует и не обращается к отсутствующим objects либо deployment явно блокируется preflight |
| Partition boundary | Approval в момент смены месяца | Active/future partition принимает row и имеет triggers/constraints/indexes |
| Maintenance | Recalculate конкурирует с active inventory | Операция запрещена/serialized/invalidation доказана, silent change невозможен |

Минимально переиспользуются: movement batch atomicity tests, [inventory history no-allow-list test](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_history.py:88), [operations history fallback tests](/home/skurbick/PROJECTS/WMSService/tests/test_operations_history.py:112), kit/re-sort suites, KИЗ PostgreSQL tests и FBS stage tests. Нужен новый PostgreSQL integration layer именно для trigger/partition/concurrency; unit mocks это не доказывают.

## I. Открытые вопросы

### Блокирующие безопасное применение

1. Каково **фактическое** определение `wms.update_inventory_from_movement()` и всех triggers на movements/inventory/KИЗ в актуальной БД? Архив недостаточен.
2. Установлены ли `movement_registry` и базовые KИЗ migrations, покрыты ли registry все movements, и какая версия является deployment truth?
3. Существуют ли в inventory одновременно NULL/empty aliases, nonempty batch rows, fractional rows и multi-row eligible pairs? Без результата F.5 нельзя определить auto-apply eligibility.
4. Как фактически связаны active KИЗ с physical inventory и какие DB guards уже запрещают уменьшение ниже active count? Названия/состояния current worktree нельзя считать production contract.
5. Какие locations обрабатываются standard/external/HTTP/retry FBS и есть ли trustworthy physical event timestamp/id во внешнем payload? Без него сценарий delayed FBS автоматически неразрешим.
6. Какие внешние consumers/BI/1С читают `movement_type` закрытым allow-list? Для A не блокирует MVP, для B блокирует выбор/deployment.
7. Кто и как запускает `recalculate-inventory`/container DB functions в production, и можно ли ввести совместимый location/application barrier на время approval?

### Требующие решения до детального проектирования, но не блокирующие shadow mode

1. Какое правило принять для излишка/недостачи агрегированной пары при нескольких batch rows: запрет auto-apply, явный technical allocation или утверждённый deterministic rule?
2. Какой максимальный размер документа и допустимое approval lock window принять эксплуатационно?
3. Нужен ли document-level adapter в operations history или достаточно line movements + отдельного inventory API?
4. Какой источник авторства использовать при RabbitMQ/system approval и какой retention нужен count-attempt evidence?
5. Для каких zones первый pilot гарантированно исключает FBS, containers и active KИЗ?

### Некритичный технический долг

- Рассогласовать/унифицировать Python/DB списки `write_off` против `putaway/pick`, не смешивая это с MVP без отдельной compatibility оценки.
- Сделать movement readers forward-compatible с неизвестными types даже при выборе A.
- Централизовать lock order/namespaces writers; сейчас advisory locks локальны операциям.
- Автоматизировать inventory migrations ledger и future partition preflight.
- Исправить документацию только после подтверждения runtime contract.

## Финальная оценка готовности

- **Разработка документа, scope, count history и shadow reconciliation:** можно начинать additive способом.
- **Approval с физическими корректировками:** нельзя включать до production preflight и специального source-scoped safety contract.
- **Рекомендуемый movement type MVP:** существующий `adjust`, не меняя его старую семантику; обязательны source/link/idempotency и строгий internal apply path.
- **`inventory_adjustment`:** отложить; вернуться после forward-compatible readers и инвентаризации partitions/external consumers.
- **FBS без остановки всей очереди:** возможно только консервативно — ordinary events актуализировать, ambiguous delayed physical events отправлять на line recount; для FBS zones нужен отдельный короткий location window либо новый trustworthy external event protocol.
