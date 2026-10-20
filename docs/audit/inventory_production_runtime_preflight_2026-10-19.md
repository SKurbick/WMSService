# Production runtime preflight PostgreSQL WMS перед инвентаризацией

Дата: 19.10.2026. Проверена доступная настроенная БД `vector_db`, PostgreSQL 17.4. Контрольная отметка одной из диагностических транзакций: `2026-10-19 11:49:14.034237+00` (14:49:14 МСК). Код — текущее рабочее дерево, включая существовавшие до аудита незакоммиченные изменения. Факт его deployment в production не установлен.

Исходные материалы: [первый аудит](inventory_module_readiness_audit_2026-10-18.md), [целевой аудит](inventory_targeted_technical_audit_2026-10-18.md). Их архивные предположения ниже заменены результатами чтения действующей БД там, где проверка состоялась.

Метки: **БД** — «Подтверждено действующей БД»; **Код** — «Подтверждено текущим кодом»; **Архив** — «Подтверждено только архивной схемой»; **Вывод** — «Аналитический вывод или рекомендация»; **Не проверено** — необходимого доказательства нет. Ссылки Q/C/F относятся к приложениям с точными выполненными SELECT, результатами каталогов и определениями функций.

## 1. Краткое резюме

**БД.** Самостоятельное подключение состоялось. Все диагностические сессии имели `default_transaction_read_only=on`; каждый запрос выполнялся в `asyncpg.transaction(readonly=True)`, с `statement_timeout=10s`, `lock_timeout=1s`. Использована существующая настроенная роль `vector_admin`: каталог подтвердил superuser-права. Отдельных read-only credentials не было, поэтому ограничение обеспечено режимом сессии и транзакций. Пароли/DSN не выводились. Ошибок SQL в выполненной диагностике не было; при исключении transaction context предусмотрен ROLLBACK. Прикладные функции не вызывались; их тела читались через `pg_get_functiondef`. Вызванные `wms.nlevel` и LTREE-оператор предварительно опознаны как функции расширения LTREE, а не прикладная логика.

**БД.** Inventory небольшой: **1 105 строк**, **1 101 пара SKU/локация**, **3 783 079 единиц**. Все строки положительные, `available`, `container_code IS NULL`. Пустых batch/container, дробных количеств, фактических NULL/empty collisions и положительных остатков на неактивных locations не найдено. Но **6 строк с непустой партией** уже существуют; **3 пары** имеют несколько physical rows именно из-за batch.

**БД.** В КИЗ 10 записей: 2 active, 4 registered без holder, 3 deactivated, 1 shipped. Есть **1 mixed pair**: `testwild` в `PUSHKINO-ПРИЁМКА`, physical=3, active KИЗ=2, unidentified=1. Отрицательного calculated unidentified не найдено. Наличие registered без holder — штатная модель, а не нарушение.

**БД + Вывод.** Обычный `adjust` поддерживается общей функцией и реально используется: 21 movement за последние 7 дней. Аудит не обнаружил оснований считать весь adjust неисправным. Подтверждён узкий пограничный дефект: **расходный adjust при отсутствии подходящей available row может записаться без расходного эффекта**. NULL/empty collision также возможен по определению UPDATE, но **в текущих данных отсутствует**. Эти выводы получены чтением definitions, без пробных списаний.

**БД.** `movement_registry` существует; PK, unique координат, FK и immutable trigger установлены. Среди **6 627 movements последних 7 дней пропусков registry — 0**. `movement_ref` пригоден как стабильная FK-ссылка будущего модуля. Он не задаёт порядок commit.

**БД.** Партиции существуют только январь–декабрь 2026; последний upper bound — **2026-12-31 21:00 UTC = 2027-01-01 00:00 МСК**. Партиций 2027/default partition нет. Признаков pg_cron/pg_partman или SQL-функции создания partitions в проверенных каталогах нет. Внешний scheduler не проверен.

**Вывод.** Можно разрабатывать документы, фиксируемые области, count attempts и shadow reconciliation. Для применения корректировок рекомендуется прежний `adjust` с отдельным inventory source и атомарным внутренним approval. Обычный `POST /api/movements` менять не требуется. До включения apply необходимо закрыть точный расход, идемпотентность и временную неоднозначность FBS/КИЗ. Одна предварительная BEFORE-проверка не должна объявляться полной защитой от всех гонок.

## 2. Результаты диагностики

Все числа ниже — результаты действующей БД, а не архивного DDL. Независимые запросы выполнялись в отдельных коротких read-only транзакциях; это не единый глобальный snapshot всей системы.

| Проверка | Фактический результат | Риск | Вывод для MVP |
|---|---|---|---|
| Server/database/read-only | PostgreSQL 17.4, vector_db, read_only=on | Роль обладает правом записи, использовалась только read-only сессия | Для регулярных проверок выдать отдельную read-only роль |
| Inventory count / available / positive loose | 1 105 / 1 105 / 1 105 | Нет обнаруженной аномалии по этим признакам | Точный scoped snapshot недорог |
| Aggregated stock | 1 101 pair, quantity 3 783 079.00 | UI pair не всегда одна physical row | Хранить internal decomposition |
| Nonempty batch | 6 rows, 300.00 units, 2 SKU, 3 locations | Недостача агрегата не определяет потерянную партию | Не списывать весь delta с batch=NULL |
| Empty batch/container | 0 / 0 | Возможность появления в будущем не ограничена найденным CHECK | Проверять/защищать inventory apply |
| Fractional / zero rows | 0 / 0 | DB допускает decimals | Внутренний Decimal; public schema int не менять |
| Multi-row pairs | 3; причина batch в 3, container/status в 0; max 3 rows/pair | Неоднозначный technical allocation | Auto-apply только при определённом распределении |
| Actual COALESCE collisions | 0 normalized keys с count>1 | Потенциальный, не текущая порча | Не обобщать дефект на все adjust |
| Inactive location stock | 0 rows, 0 locations | Нет текущей аномалии | Сохранять eligibility validation при старте |
| КИЗ lifecycle | active 2; registered 4; deactivated 3; shipped 1 | Holderless registered не привязаны к физическому count | Не вычитать registered из произвольной location |
| Mixed/negative unidentified | 1 mixed pair; negative pairs 0 | Физически отделить маркированную единицу по агрегату нельзя | Требуется count protocol или исключение affected pair из auto-apply |
| Registry recent coverage | 6 627 movements, 0 missing | Полная история не сверялась | FK movement_ref пригоден для новых movements |
| Locations | 5 338, 1 root PUSHKINO; invalid parent/path edges=0; duplicate paths=0 | level не всегда соответствует depth | Scope вычислять по path/IDs |
| Level mismatch | 1 location, id=44: level=3, path depth=5 | Нельзя использовать level как доказательство дерева | Некритично для materialized path scope |
| Partitions | 12 monthly 2026; 2027/default нет | С 01.01.2027 МСК новые movements без partition не вставятся | Отдельная эксплуатационная задача до конца года |
| Parent/partition CHECK | types validated; quantity>0 и has_side NOT VALID в parent и всех partitions | Историческая проверка не завершена | Новые записи этими CHECK всё равно проверяются |
| Partition indexes/triggers | 10 indexes в parent/каждой partition; valid/ready=true; 3 movement triggers включены на всех | Нет обнаруженного разрыва текущих partitions | Future partition creation должна сохранять контракт |
| Recent malformed movements | quantity<=0:0; both sides NULL:0; empty batch/container:0 | За 7 дней, не вся история | Не считать историческую валидность доказанной |
| Recent movement types | adjust 21; kit_assembly 75; kit_disassembly 10; receive 380; re_sorting 40; ship 5 313; transfer 788 | Не все ship являются FBS | Разделять по links, не только type/location |
| Recent ship sources | PUSHKINO-ФБС:5 281; PUSHKINO-ФБО:32; source_type=NULL | Источник ship не равен pipeline provenance | Для inventory обязательный source |
| FBS successful links | 5 174 success items → 5 071 distinct linked ship movements; unresolved=0 | Несколько items могут ссылаться на одно движение | Нельзя считать movement per message/item |
| FBS date delta | movement.created_at − shipment_date: от 10.328 мс до 1 дня 58 мин 58.002218 сек | shipment_date не доказанное время physical removal | Автоматическая temporal reconstruction невозможна |
| FBS last 7 days | standard only; items: success 5 174, failed 9 228, retry_exhausted 4 222 | Причины failures и физическое состояние не исследованы | Отсутствие pending не доказывает отсутствие delayed physical effects |
| FBS KИЗ bindings | 0; task-results table существует, 260 362 rows | Схема есть, actual KИЗ-aware FBS traffic не доказан | Код path анализировать отдельно от production usage |
| Migration tracking | public.schema_migrations есть, подходящих имён kiz/wms/container/movement нет | Нет достоверного WMS applied-version ledger | Definitions — основной источник runtime truth |

### Размеры

**БД.** Размеры включают indexes/TOAST; movements — сумма leaf tables, а не нулевой размер partitioned parent.

| Relation | Heap bytes | Total bytes | Оценка rows из каталога |
|---|---:|---:|---:|
| inventory | 147 456 | 540 672 | 1 104; точный count 1 105 |
| movements, все 12 partitions | 15 335 424 | 31 145 984 | 43 109 |
| movement_registry | 2 973 696 | 7 176 192 | 42 824 |
| kiz | 8 192 | 155 648 | 10 |
| kiz_events | 8 192 | 49 152 | статистика не собрана |
| kiz_movement_links | 8 192 | 40 960 | статистика не собрана |
| fbs_shipment_items | 156 164 096 | 184 803 328 | 395 886 |
| fbs_shipments | 109 838 336 | 127 991 808 | 100 481 |

Оценки rows нельзя вычитать друг из друга для вывода о missing registry: statistики обновляются независимо. Полная историческая сверка registry/ledger не выполнялась. Проверки больших FBS таблиц ограничены недавним окном, кроме одного точного count task-results; все завершились в пределах timeout.

### Актуальный контракт структуры

**БД.** Полный список колонок приведён в C.1. Существенные поля:

- inventory: bigint inventory_id с sequence default, SKU, location_id, numeric(10,2) quantity, nullable status default available, batch_number, container_code, timestamps.
- movements: bigint movement_id, created_at timestamptz default now(), numeric(10,2), sides, batch/container, from_container_id/to_container_id, source_type varchar(64), source_id/source_item_id bigint, metadata.
- registry: movement_ref bigint GENERATED ALWAYS AS IDENTITY, movement_id, **movement_created_at**, registered_at. Предыдущий шаблон SELECT с registry.created_at был бы неверен; реально выполнена проверка по movement_created_at.
- КИЗ: **lifecycle_status**, **location_id**, **container_id**, closed_at, origin_type/reference, organization. Полей status/current_location_id из условного шаблона предыдущего аудита здесь нет.
- locations: parent_location_id, path **wms.ltree**, level, zone_type, is_active; отдельного warehouse_id нет. Склад определяется корневой location.

**БД.** Inventory unique: `UNIQUE NULLS NOT DISTINCT(product_id, location_id, status, batch_number, container_code)`; quantity CHECK >=0. Registry unique координат `(movement_id,movement_created_at)`, FK к movements `(movement_id,created_at)`, immutable mapping. Movements types: receive, putaway, transfer, pick, ship, unpack, adjust, kit_assembly, kit_disassembly, re_sorting.

**БД + Вывод.** Immutable registry защищает identity mapping, а не все поля movement. В каталоге triggers movements не обнаружено общего запрета UPDATE quantity/source. Поэтому неизменяемость будущих inventory movements/source links нужно обеспечить отдельно для inventory source и учитывать прямые привилегированные writers; на комментарий «append-only» полагаться нельзя. Исторические movements не изменялись.

**Код + БД.** Python enum отличается: write_off присутствует в Python, отсутствует в DB; putaway/pick наоборот ([enum](/home/skurbick/PROJECTS/WMSService/app/core/enums.py:18)). Это существующее рассогласование, не причина вводить новый type.

### Locations и фиксируемая область

**БД.** Все 5 338 locations попадают под root PUSHKINO (id=1, path=1). Parent FK, GiST(path), parent index присутствуют. Проверки непосредственных parent/path edges, duplicate paths и root coverage не нашли разрыва. Одна level mismatch — id=44, path `1.38.40.43.44`; для scope использовать path, не level.

**БД + Вывод.** generate_location_path обновляет path самой изменённой строки; каскадное переписывание descendants в полученном trigger/function не реализовано. Даже при текущей целостности динамический scope нельзя вычислять заново после запуска документа. Материализация exact location IDs в coherent snapshot реализуема. При конкурентном изменении дерева требуется повторная проверка/lock выбранных nodes либо сериализованный start protocol.

**Вывод.** Additive active claims с unique location_id реализуемы: все IDs документа вставляются одной transaction; конфликт любой location откатывает весь start. Claim хранится до выхода документа из active lifecycle. SKU не участвует в unique key. Это защищает от двух инвентаризаций, но само по себе не блокирует movements.

## 3. Реальный путь adjust → inventory

### Нормальный приход и расход

**Код.** Публичный MovementCreate принимает положительный integer quantity; знак задаёт from/to, не отрицательное число ([schema](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:46)). Весь batch проходит одну transaction, доступен внутренний метод на переданном conn ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:34)).

**БД.** Порядок row triggers одинаков на parent/partitions:

1. BEFORE INSERT `trg_guard_controlled_container_movement`: любое non-NULL container_code требует source_type=container_operation и source IDs. Пустой container_code также non-NULL.
2. После CHECK/FK и вставки AFTER INSERT `trg_register_movement_identity` создаёт registry mapping.
3. AFTER INSERT `trg_update_inventory_from_movement` применяет projection. Эти AFTER triggers одного события следуют алфавитному порядку имён: register до update.
4. На inventory UPDATE сначала timestamp trigger, затем `trg_kiz_inventory_guard`; для DELETE работает guard. Ошибка откатывает movement, mapping и projection в той же transaction.

**БД.** Для прихода функция делает exact-key INSERT ON CONFLICT и увеличивает available quantity. Для расхода UPDATE уменьшает matching available rows. При существующей однозначной строке, достаточном eligible quantity и отсутствии КИЗ-конфликта это нормальный рабочий механизм adjust. Его production использование подтверждено recent rows; тестовых movements аудит не создавал.

### Пограничные случаи

**БД + Вывод.**

- **Отсутствующая строка:** UPDATE затрагивает 0 rows. Exception list включает ship/transfer/kit types, но не adjust. Поэтому чисто расходный adjust может завершиться без уменьшения. Inventory guard не поможет: при 0 affected rows ему нечего проверять. Это не означает, что такой дефект исторически уже случался.
- **Недостаточный остаток:** при существующей row quantity станет отрицательным — CHECK >=0 отклоняет statement. Если active КИЗ требуют сохранить больший floor, P7501 может возникнуть ещё до CHECK.
- **Concurrent расход:** UPDATE существующей row сериализуется row lock. После ожидания второй расход работает с актуальной quantity; negative CHECK/КИЗ guard должны отклонить недопустимый эффект. Поведенческая concurrency-проверка в production не выполнялась.
- **NULL/empty:** расход сравнивает COALESCE(value,'') в batch и container. Exact unique допускает отдельные NULL и ''. Если одновременно существуют несколько matching available rows, UPDATE может уменьшить каждую на полное quantity. Сейчас collisions=0; это потенциальный путь, не обнаруженный массовый дефект.
- **Приход NULL/empty:** UPSERT использует exact unique key и не объединяет NULL с ''. Поэтому входящий legacy movement с batch='' может создать alias row.
- **Zero cleanup:** DELETE после расхода удаляет quantity<=0 по product/location без фильтра status/batch/container. Положительный чужой остаток не удаляет; unrelated zero rows может удалить. Сейчас zero rows=0.
- **Обе стороны:** функция сначала выполняет приход, затем расход; для inventory adjustment следует требовать ровно одну сторону. Общий existing API допускает обе стороны и не должен менять смысл.
- **Decimal:** numeric(10,2) поддерживает сотые; public response quantity:int может отвергать дробное значение. Нельзя вводить fractional inventory movements без оценки старых readers, даже если внутренний метод использует Decimal.

### Реальная КИЗ-защита

**БД.** `guard_kiz_inventory()` действует на UPDATE/DELETE. Для OLD.status не available или OLD.batch_number не NULL он пропускает изменение. Для batch=NULL определяет active КИЗ по holder. Уменьшение до remaining < identified вызывает **P7501 «Недостаточно неидентифицированного остатка»**. Увеличение unchanged-key row разрешено сразу. Container move имеет отдельное controlled authorization исключение.

**БД.** Active lifecycle требует ровно один holder, registered — ни одного. `guard_kiz_identity` разрешает activation/transfer/ship/container holder transitions только через authorization protocol. Deferred `require_kiz_final_holder_integrity` проверяет active capacity на commit, а complete-shipment/container triggers — полноту operation graph.

**Вывод.** Эти guards защищают active capacity, но не превращают count агрегата в доказательство, какие физические единицы были немаркированными. Они также не обеспечивают revision/idempotency будущего inventory document.

## 4. КИЗ, партии, контейнеры

### Фактическая детализация

**БД.** Три multi-row пары принадлежат testwild:

| Location | Physical decomposition |
|---|---|
| PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L01 | batch NULL:4; BATCH-TEST-001:93; BATCH-TEST-003:90 |
| PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L02 | batch NULL:35; BATCH-TEST-001:7 |
| PUSHKINO-ХРАНЕНИЕ-ТЕСТ-07-S02-L01 | batch NULL:1; BATCH-TEST-003:10 |

Всего nonempty batch rows — 6, в том числе строки второго SKU; приведённая таблица показывает именно multi-row pairs. Название testwild не является основанием удалить/игнорировать эти production rows.

**Вывод.** UI можно агрегировать по product_id+location_id. Но physical components должны сохраняться в snapshot/application links. При shortage 2 в row 4+93+90 агрегат не определяет потерянную партию. Auto-apply допускается при однозначном компоненте либо утверждённом technical allocation; угадывать FIFO/пропорции нельзя. Партии не нужно добавлять как пользовательский фильтр.

### КИЗ и unidentified

**БД.** Active loose КИЗ занимают capacity exact available row с batch=NULL и container=NULL. Registered holderless capacity не занимают. В существующей mixed pair physical 3, active 2, unidentified 1. Для batch=NULL loose rows сумма unidentified — 3 782 777; это не весь SKU/location aggregate: batch rows 300 единиц посчитаны отдельно.

**БД.** Есть 2 activated events и 3 deactivated events без movement_ref. Activation уменьшает unidentified capacity, deactivation увеличивает её без изменения physical inventory. Assigned/registered events тоже не имеют movement link; shipped имеет. Следовательно только movement ledger недостаточен для eligibility revision.

**Вывод.** Для MVP считать unidentified quantity как physical−active допустимо как учётный показатель, но сотрудник должен физически исключать active маркированные единицы. Если этого нельзя гарантировать, mixed pair переводится в recount/manual resolution либо отключается для auto-apply. Holderless registered не позволяют определить, в какой location физически лежит маркированная единица. Вычитать их из произвольного склада нельзя. Это ограничение первой версии, а не ошибочное состояние registered.

### Runtime containers отличается от архивной модели

**БД + Код.** В текущем inventory container rows=0, active contained КИЗ=0. При этом production уже содержит controlled container operation protocol: BEFORE movement provenance guard, deferred completeness, holder transitions, extract helper. `register_container` теперь разрешает только пустое содержимое. В полном списке функций wms отсутствуют legacy `unpack_from_container` и `move_container_inventory`; старый container location trigger также отсутствует.

**Код.** Текущее рабочее дерево всё ещё вызывает `wms.unpack_from_container`, register с contents и прямой UPDATE container.location ([queries](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/containers.py:5), [location update](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/containers.py:65), [unpack](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/containers.py:79)). Это подтверждённое расхождение local-code/runtime DB contract, не доказательство того, какая версия backend развёрнута. Эти paths в production не вызывались.

**Вывод.** При проектировании учитывать controlled fill/extract как outgoing/incoming **россыпи**, хотя общий quantity SKU в location может не меняться. Не восстанавливать архивные container functions в рамках inventory. Перед расширением pilot на container-adjacent locations установить фактического writer нового protocol.

### FBS и время physical removal

**Код.** Standard/external consumers используют общий handler и ACK после raw save, до списания ([consumer](/home/skurbick/PROJECTS/WMSService/app/consumer.py:33)); HTTP endpoint вызывает тот же процесс. Обычная ветка создаёт ship с NULL source через MovementService ([handler](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:179)); item movement_id записывается в той же product-group transaction. Task-level results хранят movement_id+movement_created_at и optional movement_ref ([repository](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_shipment_repository.py:84)). Retry использует тот же group path ([worker](/home/skurbick/PROJECTS/WMSService/app/retry_worker.py:80)).

**Код.** KИЗ-aware FBS: active loose КИЗ списываются из их holder location, registered/unbound — из configured FBS zone; на каждую source location создаётся отдельный ship, source_type=kiz_operation, operation/item IDs, registry ref и shipped event; все источники product group атомарны ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/fbs_kiz_shipment_service.py:45), [movement creation](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:314)). Эта ветка может затрагивать не только ФБС-зону.

**БД.** За 7 дней наблюдались только standard shipments, ship movements с NULL source, FBS bindings=0. Поэтому runtime активность external/HTTP/KИЗ-aware веток не доказана; наличие кода/таблиц не доказывает deployment flags. Ship из ФБО нельзя автоматически назвать FBS.

**БД + Вывод.** shipment_date заполнена у выбранных FBS items; разница до movement достигает >24 часов. Это доказывает различие timestamps, но не причину задержки и не время physical removal. Raw payload/logistics process не предоставляет проверенной причинной границы в рамках этого аудита. Полезный timestamp есть, его физическая семантика **не проверена**.

## 5. Возможность безопасного approval

### Минимальный механизм

**Вывод.** Сохранить adjust. Добавить inventory source/document/item links, immutable application record и DB unique idempotency identity вне partitioned movements. Registry movement_ref использовать FK; один пользовательский item может иметь несколько component adjustments, поэтому key должен включать component/application identity.

Одной transaction на одном connection:

1. Заблокировать document, проверить lifecycle и approved revision; повтор применённого approval вернуть сохранённым результатом.
2. Заблокировать apply identity и eligible physical components; проверить scope, Decimal/целочисленность поддерживаемого pilot, source и ровно одну сторону.
3. Собрать locks в согласованном порядке. Existing KИЗ paths берут KИЗ раньше inventory: нельзя добавить противоположный inventory→КИЗ order без deadlock оценки. Для KИЗ нужны locks/evidence lifecycle; для missing incoming row row-lock не существует.
4. Повторно проверить current inventory, eligible КИЗ и движение/evidence относительно count. Проверить **все** расходы до применения; stale revision требует refresh/recount.
5. Вставить все source-linked movements; сверить точный фактический эффект по каждому physical component; записать movement_ref links.
6. Зафиксировать решение, статус applied, response/idempotency result и освобождение claims.
7. Ошибка любого statement или deferred trigger откатывает всё. Частичные commits/HTTP-вызовы по строкам недопустимы.

**Код.** Возможность передать conn в MovementService есть, но public DTO int и legacy INSERT не передают source ([service](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:55), [SQL](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/movements.py:5)). Нужен отдельный внутренний метод/SQL с source и точным quantity; публичный endpoint сохраняется.

### BEFORE guard против изменения общей функции

| Решение | Что обеспечивает | Чего не обеспечивает | Совместимость |
|---|---|---|---|
| Source-specific BEFORE movement guard | Проверяет document/revision/source, exact row existence, quantity, известные alias rows; может взять row locks | Сам не меняет COALESCE UPDATE; новая alias row может появиться между guard и updater; нет собственного affected-row count | Узкий additive predicate, legacy movements не затрагивает |
| Только application precheck | Удобная доменная ошибка и блокировка existing rows | Не защищает DB contract от другого writer; zero-row и phantom/race остаются | Мало изменений, недостаточно как единственная защита |
| Source-specific strict branch в общей AFTER function | Для inventory source выполняет exact-key mutation, проверяет RETURNING/affected count; missing row всегда ошибка | Нужны revision/КИЗ/idempotency отдельно | Требует replacement shared function, но legacy ветку можно сохранить буквально |
| Общая замена COALESCE/exception semantics для всех types | Унифицирует весь WMS | Меняет existing adjust и другие writers | Наибольший риск, для MVP не рекомендуется |
| BEFORE + проверка фактического эффекта после updater | Может сохранить legacy updater и откатывать несоответствие | Нужен доказанный trigger order, baseline, concurrent isolation и учёт всех matching components | Возможный вариант, сложнее доказать, чем exact source branch |

**Вывод.** Отдельная inventory-only проверка реализуема без изменения поведения обычного adjust. Но рекомендация предыдущего аудита «BEFORE guard + неизменённый AFTER достаточно» уточняется: **это условный механизм, а не доказательство полной конкурентной безопасности**. При строгом требовании exact расхода предпочтительна небольшая source-specific ветка в существующей AFTER function с early return после inventory-only обработки и неизменённым legacy fallback. Replacement общей функции требует отдельного deployment review и regression tests; это не разрешение менять её сейчас.

Можно сначала реализовать BEFORE guard и internal service, оставив apply выключенным, затем выбрать exact branch либо доказанный effect-verification protocol на stage. Наличие сейчас 0 alias rows снижает текущий риск, но не заменяет гарантию.

### Конкурентность, КИЗ и TOCTOU

**Вывод.** Row locks защищают existing exact rows, а не всю location/SKU predicate область. Отдельные advisory namespaces (`kiz-activate:`, `fbs-kiz:`, kit/re-sort) не образуют общего барьера ([activation locks](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_activation.py:40), [FBS locks](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:173)). Activation берёт inventory lock до mutation, но terminal lifecycle changes/внешние writers могут иметь другой protocol. Полную защиту eligibility revision без проверки всех writers заявлять нельзя.

Для первого pilot выбрать ordinary locations/SKU без mixed/holderless-КИЗ ambiguity и batch ambiguity. Добавить короткий lock/recheck на момент approval, immutable approved revision и conservative invalidation count attempts. Версии/claims, которые читают только новые endpoints, не синхронизируют legacy writers. Массовое внедрение глобального location version в каждый writer сейчас не требуется; для каждой допущенной категории нужно доказать локальный protocol. READ COMMITTED видит новые committed данные между statements; REPEATABLE READ даёт snapshot и serialization errors на конкурентно обновлённых rows, но не фиксирует физический момент движения и не останавливает склад.

**Вывод.** Current `recalculate-inventory` DELETE/INSERT available выполняет без нового movement ([repository](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/system_repository.py:39)). Active КИЗ guard может отклонить DELETE даже если дальнейшее восстановление вернуло бы quantity. Maintenance должен быть согласован с inventory approval через operational exclusion/общий барьер либо инвалидировать affected revisions. Этот endpoint не запускался.

### Два временных сценария

**Вывод.**

- Учёт 10, count 8, затем доказуемо receive+3: expected становится 13, physical target 11, shortage остаётся 2. Ответственный подтверждает новую revision; movement during count — recount affected line.
- Учёт 10, physical FBS removal2, count8, поздний ship2: из DB created_at нельзя доказать, что эти две единицы уже исключены из count. Автоматическая корректировка способна удвоить недостачу. Нужен повторный count после разрешения in-flight/retry либо trusted external physical boundary. Подтверждение пользователем «просто согласен с delta» не создаёт отсутствующее доказательство.

Большой документ: подготовку можно разбивать, final apply — одна transaction с лимитом размера, timeout, deterministic lock order. Нет безопасного «частично применённого» approval.

## 6. Матрица обратной совместимости

| Компонент | Реальная зависимость | Потенциальное влияние inventory source | Обязательная проверка |
|---|---|---|---|
| POST/GET movements | adjust enum известен; request/response quantity:int | Source остаётся internal; fractional output может сломать старый reader | Старые adjust ±, bulk, response serialization |
| MovementService/FBS ordinary | Общий INSERT, source NULL, transaction | Inventory predicate не должен срабатывать | Standard/external/HTTP/retry regressions |
| AFTER inventory updater | Общий для каждого writer | Replacement затрагивает функцию всех types | Сравнить legacy fallback, весь trigger integration suite |
| Registry | AFTER register до updater, FK pair | Новые links через movement_ref | Rollback поздней ошибки, coverage, immutable mapping |
| KИЗ inventory guard | UPDATE/DELETE floor active | Может отклонить shortage | mixed stock, activation/terminal transition concurrency |
| KИЗ deferred complete triggers | Проверки при commit | Ошибка на commit должна откатить document целиком | Late failure test на isolated DB |
| Controlled container guard | Non-NULL container требует container_operation | Inventory source обязан писать container=NULL | Fill/extract/move regressions |
| Existing container HTTP | Часть local SQL не соответствует runtime protocol | Не использовать архивные helpers | Deployment/schema contract сверка до расширения pilot |
| Kit/re-sort | Shared updater, own source/locks | Inventory branch не должна затронуть | Existing operations и overlap/deadlocks |
| Tasks approval/recount | Создают обычные transfer/adjust | Новый document не заменяет их workflow | Existing statuses/history unchanged |
| Operations history | adjust CASE уже есть; новый source остаётся standalone | Документу может потребоваться adapter позднее | adjust отображается; нет duplication после adapter |
| Daily balances | Все movements по сторонам, type allow-list нет | Корректировки попадут автоматически | Expected close balance |
| Integrity/recalculate | All-type ledger, exact dimensions | Коррекция должна совпасть с projection; maintenance отдельный риск | Scoped ledger verification + maintenance exclusion |
| mv_product_stock | Считает loose только container NULL | Содержит active КИЗ; не eligibility source | Не использовать для inventory expected |
| Old application + additive DB | Тип adjust остаётся знаком | Безопасно при сохранении legacy semantics и integer pilot | Старое приложение с новой schema/guard |
| Future partitions | Нет 2027 | Потенциальная остановка всех writers на границе года | Create/process проверять отдельным change до Jan2027 |

**Код.** History sources/type mapping: [operations history](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/operations_history.py:149). Daily ledger: [inventory history](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/inventory_history.py:21). System ledger: [system query](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:5). Новый movement type для MVP не нужен и не устраняет concurrency/source problems.

## 7. Блокирующие проблемы

### Подтверждённые фактами

1. **БД + Вывод.** Missing-row outgoing adjust не является fail-closed. Для inventory apply нужен отдельный exact path; обычный adjust в штатных сценариях остаётся рабочим.
2. **БД + Вывод.** Multi-batch pair и mixed КИЗ pair реально существуют. Без allocation/count protocol auto-apply таких строк недоказуем.
3. **Код + Вывод.** Существующий internal movement method не записывает inventory source/idempotency и связан с public DTO. Нужна отдельная транзакционная операция approval.
4. **БД + Код + Вывод.** KИЗ eligibility меняется без movement; movement-only revision недостаточна.
5. **БД + Вывод.** Нет partitions после 2026; это эксплуатационный blocker movements с 01.01.2027 МСК, не blocker разработки документов сейчас.

### Неподтверждённые, требующие проверки до включения соответствующего apply

1. Physical event timing/semantics FBS shipment_date. Пока не доказано — FBS ambiguous lines только recount; глобально очередь останавливать не требуется.
2. Точный deployment backend/flags и внешние DB writers; local worktree не доказывает production binary.
3. Concurrent activation/terminal lifecycle/alias creation/maintenance protocol. Нужны isolated PostgreSQL integration tests, не production writes.
4. Полный исторический ledger integrity и registry coverage. Recent coverage хорошее, историческое утверждение не делается.
5. Внешний partition scheduler. В проверенных каталогах и исходниках не найден, вне БД не исследован.
6. Физическое исключение holderless registered и active marked units из count россыпи: требуется операционная граница count, а не новый пользовательский фильтр.

### Некритичные для ordinary-location shadow MVP

- Один location level mismatch при корректных parent/path edges.
- Existing Python/DB type enum mismatch.
- Отсутствие подтверждённого WMS migration ledger.
- Legacy container code/runtime discrepancy: отдельная существующая совместимость, не исправлять в inventory task; расширение pilot зависит от выяснения deployed writer.
- Source fields пусты у старых manual/ordinary FBS movements; старую историю не переписывать.
- SQL checks NOT VALID не означают, что новые записи не проверяются; историческую validation завершать отдельной задачей.
- Фактические alias collisions, дробные/zero rows, negative unidentified и inactive-location stock **не обнаружены**; не объявлять их существующей порчей.

## 8. Рекомендуемая последовательность разработки

1. Зафиксировать этот runtime contract и owner будущих partitions; получить отдельную read-only роль для повторяемой диагностики.
2. Additive documents/scope/claims/count attempts/revisions/decisions, без physical apply. На старте materialize locations, snapshot exact components, eligibility evidence.
3. Реализовать UI/API counting и shadow reconciliation; зафиксировать NULL=not counted и actual=0 как разные состояния. Согласовывать document revision целиком.
4. На isolated PostgreSQL17 воспроизвести текущие definitions и подготовить tests: adjust legacy ±; missing/insufficient; exact batch; NULL/empty; decimal response; concurrent расходы/КИЗ; idempotent approval; rollback N movements/deferred errors; registry/history/recalc.
5. Выбрать строгий source-specific apply protocol: предпочтительно exact source branch с неизменённой legacy веткой либо доказанный BEFORE+effect verification. Отдельно ревью shared function deployment.
6. Включить application feature flags module=true/apply=false; провести shadow documents и сверку.
7. Отдельным flag разрешить apply pilot ordinary locations и однозначных components. Ошибка/stale revision — ни одного movement, affected lines на recount.
8. Отдельно подключить batch allocation, mixed КИЗ, controlled container adjacency и FBS physical-window protocol. Не включать их автоматически по факту наличия таблиц.
9. При rollback приложения отключить apply; additive schema и уже записанные корректировки сохранить. Физические movements не удалять и исторический ledger не переписывать.

Создан только этот отчёт. Endpoint, workers, настройки, миграции и тесты не изменялись. DB statements ограничены чтением и session-local настройками диагностических read-only транзакций.

## Приложение Q. Выполненные read-only SQL и агрегированные результаты

Все SELECT ниже действительно выполнены, если прямо не оговорено обратное. Session wrapper:

Первое подключение выполнило следующий parameterized SELECT (значение `$1` — `transaction_read_only`): результат database=vector_db, role=vector_admin, version=PostgreSQL 17.4, read_only=on.

```sql
SELECT current_database() AS database, current_user AS role,
       version() AS version, current_setting($1) AS read_only;
```

```sql
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';
-- один SELECT из списка
COMMIT;
```

Соединение дополнительно установлено с default_transaction_read_only=on. При исключении asyncpg transaction context выполняет ROLLBACK. Timeout не увеличивался. Оценка нагрузки: catalog-only для C-запросов; inventory/КИЗ/tree — полный scan малых таблиц; movements/FBS — семидневное окно; full historical ledger/registry scan не выполнялся.

### C: каталоги

#### sizes

```sql
SELECT c.oid::regclass::text AS relation,c.relkind,c.reltuples::bigint AS estimated_rows,pg_relation_size(c.oid) AS heap_bytes,pg_total_relation_size(c.oid) AS total_bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='wms' AND (c.relname IN ('inventory','movements','kiz','movement_registry','kiz_events','kiz_movement_links','locations') OR c.relispartition) ORDER BY total_bytes DESC;
```

Результат: 139 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### columns

```sql
SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,pg_get_expr(d.adbin,d.adrelid) AS default_expr FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='wms' AND c.relname IN ('inventory','movements','kiz','movement_registry','kiz_events','kiz_movement_links','locations','fbs_shipments','fbs_shipment_items') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum;
```

Результат: 103 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### constraints

```sql
SELECT conrelid::regclass::text AS relation,conname,contype,convalidated,conislocal,pg_get_constraintdef(oid,true) AS definition FROM pg_constraint WHERE conrelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='wms' AND c.relname IN ('inventory','movements','movement_registry','kiz','kiz_events','kiz_movement_links','locations')) ORDER BY relation,conname;
```

Результат: 61 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### indexes

```sql
SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='wms' AND tablename IN ('inventory','movements','movement_registry','kiz','kiz_events','kiz_movement_links','locations') ORDER BY tablename,indexname;
```

Результат: 40 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### triggers

```sql
SELECT t.tgrelid::regclass::text AS relation,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true) AS definition,t.tgfoid::regprocedure::text AS function FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname='wms' AND NOT c.relispartition ORDER BY relation,t.tgname;
```

Результат: 37 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### partitions

```sql
SELECT pt.relid::regclass::text AS relation,pt.level,pg_get_expr(c.relpartbound,c.oid) AS bounds FROM pg_partition_tree('wms.movements'::regclass) pt JOIN pg_class c ON c.oid=pt.relid ORDER BY relation;
```

Результат: 13 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### functions

```sql
SELECT p.oid::regprocedure::text AS identity,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='wms' AND p.prokind='f' ORDER BY identity;
```

Результат: 125 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### extensions

```sql
SELECT extname,extversion FROM pg_extension ORDER BY extname;
```

```json
{
  "query": "extensions",
  "rows": [
    {
      "extname": "btree_gist",
      "extversion": "1.7"
    },
    {
      "extname": "ltree",
      "extversion": "1.3"
    },
    {
      "extname": "pg_stat_statements",
      "extversion": "1.11"
    },
    {
      "extname": "pgcrypto",
      "extversion": "1.3"
    },
    {
      "extname": "plpgsql",
      "extversion": "1.0"
    }
  ]
}
```

#### role

```sql
SELECT rolname,rolsuper,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=current_user;
```

```json
{
  "query": "role",
  "rows": [
    {
      "rolname": "vector_admin",
      "rolsuper": true,
      "rolcreaterole": true,
      "rolcreatedb": true
    }
  ]
}
```

### Q: агрегаты

#### inventory_stats

```sql
SELECT count(*) AS total,count(*) FILTER(WHERE status='available') AS available,count(*) FILTER(WHERE status='available' AND quantity>0 AND container_code IS NULL) AS positive_loose,count(*) FILTER(WHERE batch_number IS NOT NULL AND batch_number<>'') AS nonempty_batch,count(*) FILTER(WHERE batch_number='') AS empty_batch,count(*) FILTER(WHERE container_code='') AS empty_container,count(*) FILTER(WHERE quantity<>trunc(quantity)) AS fractional,count(*) FILTER(WHERE quantity=0) AS zero_rows FROM wms.inventory;
```

```json
{
  "query": "inventory_stats",
  "rows": [
    {
      "total": 1105,
      "available": 1105,
      "positive_loose": 1105,
      "nonempty_batch": 6,
      "empty_batch": 0,
      "empty_container": 0,
      "fractional": 0,
      "zero_rows": 0
    }
  ]
}
```

#### inventory_groups

```sql
SELECT status,CASE WHEN container_code IS NULL THEN 'loose' WHEN container_code='' THEN 'empty' ELSE 'container' END AS holder,count(*) AS rows,sum(quantity) AS quantity,count(DISTINCT (product_id,location_id)) AS pairs FROM wms.inventory GROUP BY status,holder ORDER BY status,holder;
```

```json
{
  "query": "inventory_groups",
  "rows": [
    {
      "status": "available",
      "holder": "loose",
      "rows": 1105,
      "quantity": "3783079.00",
      "pairs": 1101
    }
  ]
}
```

#### multi_rows

```sql
WITH g AS (SELECT product_id,location_id,count(*) AS rows,count(DISTINCT coalesce(batch_number,'<NULL>')) AS batches,count(DISTINCT coalesce(container_code,'<NULL>')) AS containers,count(DISTINCT coalesce(status,'<NULL>')) AS statuses,count(*) FILTER(WHERE status='available' AND quantity>0 AND container_code IS NULL) AS positive_loose_rows FROM wms.inventory GROUP BY product_id,location_id) SELECT count(*) FILTER(WHERE rows>1) AS multi_pairs,count(*) FILTER(WHERE rows>1 AND batches>1) AS batch_causes,count(*) FILTER(WHERE rows>1 AND containers>1) AS container_causes,count(*) FILTER(WHERE rows>1 AND statuses>1) AS status_causes,count(*) FILTER(WHERE positive_loose_rows>1) AS multi_eligible_pairs,max(rows) AS max_rows_per_pair FROM g;
```

```json
{
  "query": "multi_rows",
  "rows": [
    {
      "multi_pairs": 3,
      "batch_causes": 3,
      "container_causes": 0,
      "status_causes": 0,
      "multi_eligible_pairs": 3,
      "max_rows_per_pair": 3
    }
  ]
}
```

#### alias_collisions

```sql
SELECT count(*) AS collision_keys FROM (SELECT product_id,location_id,status,coalesce(batch_number,'') AS b,coalesce(container_code,'') AS c FROM wms.inventory GROUP BY product_id,location_id,status,coalesce(batch_number,''),coalesce(container_code,'') HAVING count(*)>1) s;
```

```json
{
  "query": "alias_collisions",
  "rows": [
    {
      "collision_keys": 0
    }
  ]
}
```

#### inactive

```sql
SELECT count(*) AS rows,count(DISTINCT i.location_id) AS locations,coalesce(sum(i.quantity),0) AS quantity,count(*) FILTER(WHERE i.status='available') AS available_rows FROM wms.inventory i JOIN wms.locations l USING(location_id) WHERE i.quantity>0 AND l.is_active IS DISTINCT FROM true;
```

```json
{
  "query": "inactive",
  "rows": [
    {
      "rows": 0,
      "locations": 0,
      "quantity": "0",
      "available_rows": 0
    }
  ]
}
```

#### kiz_states

```sql
SELECT lifecycle_status,CASE WHEN location_id IS NOT NULL THEN 'location' WHEN container_id IS NOT NULL THEN 'container' ELSE 'none' END AS holder,count(*) AS rows FROM wms.kiz GROUP BY lifecycle_status,holder ORDER BY lifecycle_status,holder;
```

```json
{
  "query": "kiz_states",
  "rows": [
    {
      "lifecycle_status": "active",
      "holder": "location",
      "rows": 2
    },
    {
      "lifecycle_status": "deactivated",
      "holder": "location",
      "rows": 3
    },
    {
      "lifecycle_status": "registered",
      "holder": "none",
      "rows": 4
    },
    {
      "lifecycle_status": "shipped",
      "holder": "none",
      "rows": 1
    }
  ]
}
```

#### kiz_capacity

```sql
WITH p AS (SELECT product_id,location_id,quantity FROM wms.inventory WHERE status='available' AND batch_number IS NULL AND container_code IS NULL), k AS (SELECT product_id,location_id,count(*) AS active FROM wms.kiz WHERE lifecycle_status='active' AND container_id IS NULL GROUP BY product_id,location_id), x AS (SELECT coalesce(p.product_id,k.product_id) AS product_id,coalesce(p.location_id,k.location_id) AS location_id,coalesce(p.quantity,0) AS physical,coalesce(k.active,0) AS active FROM p FULL JOIN k USING(product_id,location_id)) SELECT count(*) FILTER(WHERE active>0) AS active_pairs,count(*) FILTER(WHERE active>0 AND physical>active) AS mixed_pairs,count(*) FILTER(WHERE physical<active) AS negative_unidentified_pairs,coalesce(sum(active),0) AS active_units,coalesce(sum(physical-active),0) AS unidentified_units FROM x;
```

```json
{
  "query": "kiz_capacity",
  "rows": [
    {
      "active_pairs": 1,
      "mixed_pairs": 1,
      "negative_unidentified_pairs": 0,
      "active_units": "2",
      "unidentified_units": "3782777.00"
    }
  ]
}
```

#### kiz_events

```sql
SELECT event_type,from_status,to_status,count(*) AS rows,count(*) FILTER(WHERE movement_ref IS NULL) AS no_movement,min(occurred_at) AS first,max(occurred_at) AS last FROM wms.kiz_events GROUP BY event_type,from_status,to_status ORDER BY event_type;
```

```json
{
  "query": "kiz_events",
  "rows": [
    {
      "event_type": "activated",
      "from_status": "registered",
      "to_status": "active",
      "rows": 2,
      "no_movement": 2,
      "first": "2026-10-01 09:22:48.233882+00:00",
      "last": "2026-10-01 09:22:59.700247+00:00"
    },
    {
      "event_type": "assigned",
      "from_status": null,
      "to_status": "active",
      "rows": 6,
      "no_movement": 6,
      "first": "2026-09-08 14:20:47.737189+00:00",
      "last": "2026-09-24 22:01:44.093162+00:00"
    },
    {
      "event_type": "deactivated",
      "from_status": "active",
      "to_status": "deactivated",
      "rows": 3,
      "no_movement": 3,
      "first": "2026-09-08 14:24:42.688750+00:00",
      "last": "2026-09-18 08:47:52.795907+00:00"
    },
    {
      "event_type": "registered",
      "from_status": null,
      "to_status": "registered",
      "rows": 4,
      "no_movement": 4,
      "first": "2026-10-01 07:28:57.005946+00:00",
      "last": "2026-10-01 09:22:00.421470+00:00"
    },
    {
      "event_type": "shipped",
      "from_status": "active",
      "to_status": "shipped",
      "rows": 1,
      "no_movement": 0,
      "first": "2026-09-11 08:32:12.273657+00:00",
      "last": "2026-09-11 08:32:12.273657+00:00"
    }
  ]
}
```

#### kiz_link_count

```sql
SELECT count(*) AS links FROM wms.kiz_movement_links;
```

```json
{
  "query": "kiz_link_count",
  "rows": [
    {
      "links": 15
    }
  ]
}
```

#### locations_roots

```sql
SELECT location_id,location_code,path::text,level,is_active FROM wms.locations WHERE parent_location_id IS NULL ORDER BY location_id;
```

```json
{
  "query": "locations_roots",
  "rows": [
    {
      "location_id": 1,
      "location_code": "PUSHKINO",
      "path": "1",
      "level": 0,
      "is_active": true
    }
  ]
}
```

#### tree_integrity

```sql
SELECT count(*) AS total,count(*) FILTER(WHERE parent_location_id IS NULL) AS roots,count(*) FILTER(WHERE path IS NULL) AS null_paths FROM wms.locations;
```

```json
{
  "query": "tree_integrity",
  "rows": [
    {
      "total": 5338,
      "roots": 1,
      "null_paths": 0
    }
  ]
}
```

#### tree_mismatch

```sql
SELECT count(*) AS invalid_edges FROM wms.locations l LEFT JOIN wms.locations p ON p.location_id=l.parent_location_id WHERE l.parent_location_id IS NOT NULL AND (p.location_id IS NULL OR NOT(p.path OPERATOR(wms.@>) l.path) OR wms.nlevel(l.path)<>wms.nlevel(p.path)+1);
```

```json
{
  "query": "tree_mismatch",
  "rows": [
    {
      "invalid_edges": 0
    }
  ]
}
```

#### duplicate_paths

```sql
SELECT count(*) AS duplicated_paths FROM (SELECT path FROM wms.locations GROUP BY path HAVING count(*)>1) s;
```

```json
{
  "query": "duplicate_paths",
  "rows": [
    {
      "duplicated_paths": 0
    }
  ]
}
```

#### recent_types

```sql
SELECT movement_type,source_type,count(*) AS rows,count(*) FILTER(WHERE quantity<>trunc(quantity)) AS fractional FROM wms.movements WHERE created_at>=now()-interval '7 days' GROUP BY movement_type,source_type ORDER BY movement_type,source_type;
```

```json
{
  "query": "recent_types",
  "rows": [
    {
      "movement_type": "adjust",
      "source_type": null,
      "rows": 21,
      "fractional": 0
    },
    {
      "movement_type": "kit_assembly",
      "source_type": "kit_operation",
      "rows": 75,
      "fractional": 0
    },
    {
      "movement_type": "kit_disassembly",
      "source_type": "kit_operation",
      "rows": 10,
      "fractional": 0
    },
    {
      "movement_type": "receive",
      "source_type": null,
      "rows": 380,
      "fractional": 0
    },
    {
      "movement_type": "re_sorting",
      "source_type": "re_sorting_operation",
      "rows": 40,
      "fractional": 0
    },
    {
      "movement_type": "ship",
      "source_type": null,
      "rows": 5313,
      "fractional": 0
    },
    {
      "movement_type": "transfer",
      "source_type": null,
      "rows": 788,
      "fractional": 0
    }
  ]
}
```

#### recent_registry

```sql
SELECT count(*) AS recent_movements,count(*) FILTER(WHERE r.movement_ref IS NULL) AS missing_registry FROM wms.movements m LEFT JOIN wms.movement_registry r ON r.movement_id=m.movement_id AND r.movement_created_at=m.created_at WHERE m.created_at>=now()-interval '7 days';
```

```json
{
  "query": "recent_registry",
  "rows": [
    {
      "recent_movements": 6627,
      "missing_registry": 0
    }
  ]
}
```

#### fbs_states

```sql
SELECT s.source,i.status,count(*) AS items,count(*) FILTER(WHERE i.movement_id IS NOT NULL) AS with_movement,count(*) FILTER(WHERE i.shipment_date IS NOT NULL) AS with_shipment_date FROM wms.fbs_shipment_items i JOIN wms.fbs_shipments s USING(shipment_id) WHERE i.created_at>=now()-interval '7 days' GROUP BY s.source,i.status ORDER BY s.source,i.status;
```

```json
{
  "query": "fbs_states",
  "rows": [
    {
      "source": "standard",
      "status": "failed",
      "items": 9228,
      "with_movement": 0,
      "with_shipment_date": 9228
    },
    {
      "source": "standard",
      "status": "retry_exhausted",
      "items": 4222,
      "with_movement": 0,
      "with_shipment_date": 4222
    },
    {
      "source": "standard",
      "status": "success",
      "items": 5174,
      "with_movement": 5174,
      "with_shipment_date": 5174
    }
  ]
}
```

#### fbs_locations

```sql
SELECT m.movement_type,m.source_type,l.location_code,count(*) AS movements FROM wms.movements m JOIN wms.locations l ON l.location_id=m.from_location_id WHERE m.created_at>=now()-interval '7 days' AND m.movement_type='ship' GROUP BY m.movement_type,m.source_type,l.location_code ORDER BY movements DESC;
```

```json
{
  "query": "fbs_locations",
  "rows": [
    {
      "movement_type": "ship",
      "source_type": null,
      "location_code": "PUSHKINO-ФБС",
      "movements": 5281
    },
    {
      "movement_type": "ship",
      "source_type": null,
      "location_code": "PUSHKINO-ФБО",
      "movements": 32
    }
  ]
}
```

#### partition_constraints

```sql
SELECT c.conrelid::regclass::text AS relation,c.conname,c.convalidated,c.conislocal,pg_get_constraintdef(c.oid,true) AS definition FROM pg_constraint c WHERE c.conrelid IN(SELECT relid FROM pg_partition_tree('wms.movements'::regclass)) AND c.contype='c' ORDER BY relation,c.conname;
```

Результат: 39 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### partition_trigger_summary

```sql
SELECT t.tgrelid::regclass::text AS relation,t.tgname,t.tgenabled FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid IN(SELECT relid FROM pg_partition_tree('wms.movements'::regclass)) ORDER BY relation,t.tgname;
```

Результат: 39 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### partition_automation

```sql
SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE (n.nspname IN ('cron','partman') OR c.relname ILIKE '%partition%' OR c.relname ILIKE '%migration%' OR c.relname ILIKE '%alembic%') ORDER BY n.nspname,c.relname;
```

```json
{
  "query": "partition_automation",
  "rows": [
    {
      "nspname": "pg_catalog",
      "relname": "pg_partitioned_table",
      "relkind": "b'r'"
    },
    {
      "nspname": "pg_catalog",
      "relname": "pg_partitioned_table_partrelid_index",
      "relkind": "b'i'"
    },
    {
      "nspname": "public",
      "relname": "schema_migrations",
      "relkind": "b'r'"
    },
    {
      "nspname": "public",
      "relname": "schema_migrations_pkey",
      "relkind": "b'i'"
    },
    {
      "nspname": "purchase_spec",
      "relname": "alembic_version",
      "relkind": "b'r'"
    },
    {
      "nspname": "purchase_spec",
      "relname": "alembic_version_pkey",
      "relkind": "b'i'"
    },
    {
      "nspname": "supplier",
      "relname": "alembic_version",
      "relkind": "b'r'"
    },
    {
      "nspname": "supplier",
      "relname": "alembic_version_pkc",
      "relkind": "b'i'"
    },
    {
      "nspname": "ved",
      "relname": "alembic_version",
      "relkind": "b'r'"
    },
    {
      "nspname": "ved",
      "relname": "alembic_version_pkc",
      "relkind": "b'i'"
    },
    {
      "nspname": "wb_excise",
      "relname": "schema_migrations",
      "relkind": "b'r'"
    },
    {
      "nspname": "wb_excise",
      "relname": "schema_migrations_pkey",
      "relkind": "b'i'"
    }
  ]
}
```

#### session_info

```sql
SELECT now() AS observed_at,current_setting('TimeZone') AS timezone,current_setting('transaction_read_only') AS read_only,current_setting('statement_timeout') AS timeout;
```

```json
{
  "query": "session_info",
  "rows": [
    {
      "observed_at": "2026-10-19 11:49:14.034237+00:00",
      "timezone": "Etc/UTC",
      "read_only": "on",
      "timeout": "10s"
    }
  ]
}
```

### Q: уточнения

#### sizes_compact

```sql
SELECT c.relname,c.reltuples::bigint AS estimated_rows,pg_relation_size(c.oid) AS heap_bytes,pg_total_relation_size(c.oid) AS total_bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='wms' AND c.relname IN ('inventory','kiz','movement_registry','kiz_events','kiz_movement_links','fbs_shipments','fbs_shipment_items','fbs_kiz_bindings','fbs_shipment_item_task_results') UNION ALL SELECT 'movements_all_partitions',sum(c.reltuples)::bigint,sum(pg_relation_size(c.oid))::bigint,sum(pg_total_relation_size(c.oid))::bigint FROM pg_partition_tree('wms.movements'::regclass) p JOIN pg_class c ON c.oid=p.relid WHERE p.isleaf;
```

```json
{
  "query": "sizes_compact",
  "rows": [
    {
      "relname": "fbs_kiz_bindings",
      "estimated_rows": -1,
      "heap_bytes": 0,
      "total_bytes": 49152
    },
    {
      "relname": "fbs_shipment_items",
      "estimated_rows": 395886,
      "heap_bytes": 156164096,
      "total_bytes": 184803328
    },
    {
      "relname": "fbs_shipments",
      "estimated_rows": 100481,
      "heap_bytes": 109838336,
      "total_bytes": 127991808
    },
    {
      "relname": "inventory",
      "estimated_rows": 1104,
      "heap_bytes": 147456,
      "total_bytes": 540672
    },
    {
      "relname": "kiz",
      "estimated_rows": 10,
      "heap_bytes": 8192,
      "total_bytes": 155648
    },
    {
      "relname": "kiz_events",
      "estimated_rows": -1,
      "heap_bytes": 8192,
      "total_bytes": 49152
    },
    {
      "relname": "kiz_movement_links",
      "estimated_rows": -1,
      "heap_bytes": 8192,
      "total_bytes": 40960
    },
    {
      "relname": "movement_registry",
      "estimated_rows": 42824,
      "heap_bytes": 2973696,
      "total_bytes": 7176192
    },
    {
      "relname": "movements_all_partitions",
      "estimated_rows": 43109,
      "heap_bytes": 15335424,
      "total_bytes": 31145984
    }
  ]
}
```

#### batch_breakdown

```sql
SELECT i.product_id,l.location_code,i.batch_number,i.container_code,i.status,i.quantity FROM wms.inventory i JOIN wms.locations l USING(location_id) WHERE (i.product_id,i.location_id) IN(SELECT product_id,location_id FROM wms.inventory GROUP BY product_id,location_id HAVING count(*)>1) ORDER BY i.product_id,i.location_id,i.batch_number NULLS FIRST;
```

```json
{
  "query": "batch_breakdown",
  "rows": [
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L01",
      "batch_number": null,
      "container_code": null,
      "status": "available",
      "quantity": "4.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L01",
      "batch_number": "BATCH-TEST-001",
      "container_code": null,
      "status": "available",
      "quantity": "93.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L01",
      "batch_number": "BATCH-TEST-003",
      "container_code": null,
      "status": "available",
      "quantity": "90.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L02",
      "batch_number": null,
      "container_code": null,
      "status": "available",
      "quantity": "35.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-01-S01-L02",
      "batch_number": "BATCH-TEST-001",
      "container_code": null,
      "status": "available",
      "quantity": "7.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-07-S02-L01",
      "batch_number": null,
      "container_code": null,
      "status": "available",
      "quantity": "1.00"
    },
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ХРАНЕНИЕ-ТЕСТ-07-S02-L01",
      "batch_number": "BATCH-TEST-003",
      "container_code": null,
      "status": "available",
      "quantity": "10.00"
    }
  ]
}
```

#### batch_total

```sql
SELECT count(*) AS rows,sum(quantity) AS quantity,count(DISTINCT product_id) AS products,count(DISTINCT location_id) AS locations FROM wms.inventory WHERE batch_number IS NOT NULL AND batch_number<>'';
```

```json
{
  "query": "batch_total",
  "rows": [
    {
      "rows": 6,
      "quantity": "300.00",
      "products": 2,
      "locations": 3
    }
  ]
}
```

#### kiz_mixed_detail

```sql
SELECT p.product_id,l.location_code,p.quantity AS physical,count(k.kiz_id) AS active,p.quantity-count(k.kiz_id) AS unidentified FROM wms.inventory p JOIN wms.locations l USING(location_id) JOIN wms.kiz k ON k.product_id=p.product_id AND k.location_id=p.location_id AND k.container_id IS NULL AND k.lifecycle_status='active' WHERE p.status='available' AND p.batch_number IS NULL AND p.container_code IS NULL GROUP BY p.product_id,l.location_code,p.quantity;
```

```json
{
  "query": "kiz_mixed_detail",
  "rows": [
    {
      "product_id": "testwild",
      "location_code": "PUSHKINO-ПРИЁМКА",
      "physical": "3.00",
      "active": 2,
      "unidentified": "1.00"
    }
  ]
}
```

#### root_coverage

```sql
SELECT r.location_code,count(l.location_id) AS locations,count(*) FILTER(WHERE l.level<>wms.nlevel(l.path)-1) AS level_mismatches FROM wms.locations r JOIN wms.locations l ON r.path OPERATOR(wms.@>) l.path WHERE r.parent_location_id IS NULL GROUP BY r.location_code;
```

```json
{
  "query": "root_coverage",
  "rows": [
    {
      "location_code": "PUSHKINO",
      "locations": 5338,
      "level_mismatches": 1
    }
  ]
}
```

#### recent_fbs_links

```sql
SELECT s.source,count(DISTINCT i.item_id) AS success_items,count(DISTINCT m.movement_id) AS linked_movements,count(*) FILTER(WHERE m.movement_id IS NULL) AS unresolved_items,min(m.created_at-i.shipment_date) AS minimum_date_delta,max(m.created_at-i.shipment_date) AS maximum_date_delta FROM wms.fbs_shipment_items i JOIN wms.fbs_shipments s USING(shipment_id) LEFT JOIN wms.movements m ON m.movement_id=i.movement_id AND m.movement_type='ship' WHERE i.created_at>=now()-interval '7 days' AND i.status='success' GROUP BY s.source;
```

```json
{
  "query": "recent_fbs_links",
  "rows": [
    {
      "source": "standard",
      "success_items": 5174,
      "linked_movements": 5071,
      "unresolved_items": 0,
      "minimum_date_delta": "0:00:00.010328",
      "maximum_date_delta": "1 day, 0:58:58.002218"
    }
  ]
}
```

#### fbs_binding_stats

```sql
SELECT to_regclass('wms.fbs_kiz_bindings')::text AS bindings,to_regclass('wms.fbs_shipment_item_task_results')::text AS task_results;
```

```json
{
  "query": "fbs_binding_stats",
  "rows": [
    {
      "bindings": "wms.fbs_kiz_bindings",
      "task_results": null
    }
  ]
}
```

#### all_fbs_sources

```sql
SELECT source,status,count(*) AS shipments FROM wms.fbs_shipments WHERE received_at>=now()-interval '7 days' GROUP BY source,status ORDER BY source,status;
```

```json
{
  "query": "all_fbs_sources",
  "rows": [
    {
      "source": "standard",
      "status": "completed",
      "shipments": 884
    },
    {
      "source": "standard",
      "status": "failed",
      "shipments": 3987
    },
    {
      "source": "standard",
      "status": "partially_completed",
      "shipments": 1
    }
  ]
}
```

#### recent_invalid

```sql
SELECT count(*) FILTER(WHERE quantity<=0) AS nonpositive,count(*) FILTER(WHERE from_location_id IS NULL AND to_location_id IS NULL) AS no_side,count(*) FILTER(WHERE batch_number='') AS empty_batch,count(*) FILTER(WHERE container_code='') AS empty_container FROM wms.movements WHERE created_at>=now()-interval '7 days';
```

```json
{
  "query": "recent_invalid",
  "rows": [
    {
      "nonpositive": 0,
      "no_side": 0,
      "empty_batch": 0,
      "empty_container": 0
    }
  ]
}
```

#### partition_indexes

```sql
SELECT p.relid::regclass::text AS relation,count(*) AS indexes,bool_and(i.indisvalid) AS all_valid,bool_and(i.indisready) AS all_ready FROM pg_partition_tree('wms.movements'::regclass) p JOIN pg_index i ON i.indrelid=p.relid GROUP BY p.relid ORDER BY relation;
```

```json
{
  "query": "partition_indexes",
  "rows": [
    {
      "relation": "wms.movements",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_01",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_02",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_03",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_04",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_05",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_06",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_07",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_08",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_09",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_10",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_11",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    },
    {
      "relation": "wms.movements_2026_12",
      "indexes": 10,
      "all_valid": true,
      "all_ready": true
    }
  ]
}
```

#### partition_automation_functions

```sql
SELECT n.nspname,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang WHERE l.lanname IN ('plpgsql','sql') AND (p.proname ILIKE '%partition%' OR p.prosrc ILIKE '%CREATE TABLE%PARTITION OF%') ORDER BY n.nspname,p.proname;
```

```json
{
  "query": "partition_automation_functions",
  "rows": []
}
```

#### views

```sql
SELECT schemaname,matviewname,definition FROM pg_matviews WHERE schemaname='wms';
```

```json
{
  "query": "views",
  "rows": [
    {
      "schemaname": "wms",
      "matviewname": "mv_product_stock",
      "definition": " SELECT product_id,\n    sum(quantity) AS total_quantity,\n    count(DISTINCT location_id) AS locations_count,\n    sum(\n        CASE\n            WHEN (container_code IS NOT NULL) THEN quantity\n            ELSE (0)::numeric\n        END) AS in_containers,\n    sum(\n        CASE\n            WHEN (container_code IS NULL) THEN quantity\n            ELSE (0)::numeric\n        END) AS loose\n   FROM wms.inventory\n  WHERE ((status)::text = 'available'::text)\n  GROUP BY product_id;"
    }
  ]
}
```

#### migration_columns

```sql
SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='schema_migrations' ORDER BY ordinal_position;
```

```json
{
  "query": "migration_columns",
  "rows": [
    {
      "column_name": "filename",
      "data_type": "text"
    },
    {
      "column_name": "applied_at_db",
      "data_type": "timestamp with time zone"
    },
    {
      "column_name": "checksum",
      "data_type": "text"
    }
  ]
}
```

### Q: финальная сверка

#### location_level_mismatch

```sql
SELECT location_id,location_code,parent_location_id,path::text,level,wms.nlevel(path) AS path_depth FROM wms.locations WHERE level<>wms.nlevel(path)-1 LIMIT 10;
```

```json
{
  "query": "location_level_mismatch",
  "rows": [
    {
      "location_id": 44,
      "location_code": "PUSHKINO-ХРАНЕНИЕ-01-S01-S03",
      "parent_location_id": 43,
      "path": "1.38.40.43.44",
      "level": 3,
      "path_depth": 5
    }
  ]
}
```

#### fbs_runtime_columns

```sql
SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='wms' AND c.relname IN ('fbs_shipment_task_results','fbs_kiz_bindings') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum;
```

Результат: 37 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### fbs_runtime_counts

```sql
SELECT (SELECT count(*) FROM wms.fbs_kiz_bindings) AS bindings,(SELECT count(*) FROM wms.fbs_shipment_task_results) AS task_results;
```

```json
{
  "query": "fbs_runtime_counts",
  "rows": [
    {
      "bindings": 0,
      "task_results": 260362
    }
  ]
}
```

#### wms_migration_evidence

```sql
SELECT filename,applied_at_db FROM public.schema_migrations WHERE filename ILIKE '%kiz%' OR filename ILIKE '%wms%' OR filename ILIKE '%container%' OR filename ILIKE '%movement%' ORDER BY applied_at_db DESC LIMIT 100;
```

```json
{
  "query": "wms_migration_evidence",
  "rows": []
}
```

#### registry_identity

```sql
SELECT c.relname,a.attname,a.attidentity FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE a.attrelid IN ('wms.movement_registry'::regclass,'wms.kiz'::regclass) AND a.attnum>0 AND NOT a.attisdropped;
```

Результат: 19 metadata rows; существенные определения/структура приведены в C/F ниже и в разделах 2–4.

#### relation_rls

```sql
SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c WHERE c.oid IN ('wms.inventory'::regclass,'wms.kiz'::regclass,'wms.movements'::regclass,'wms.movement_registry'::regclass);
```

```json
{
  "query": "relation_rls",
  "rows": [
    {
      "relname": "inventory",
      "relrowsecurity": false,
      "relforcerowsecurity": false
    },
    {
      "relname": "movements",
      "relrowsecurity": false,
      "relforcerowsecurity": false
    },
    {
      "relname": "kiz",
      "relrowsecurity": false,
      "relforcerowsecurity": false
    },
    {
      "relname": "movement_registry",
      "relrowsecurity": false,
      "relforcerowsecurity": false
    }
  ]
}
```

## Приложение C. Runtime metadata

### C.1. Полный список колонок проверенных таблиц

| Таблица | Колонка | Тип | NOT NULL | Default |
|---|---|---|---|---|
| fbs_shipment_items | item_id | integer | true | nextval('wms.fbs_shipment_items_item_id_seq'::regclass) |
| fbs_shipment_items | shipment_id | integer | true | — |
| fbs_shipment_items | product_id | character varying | true | — |
| fbs_shipment_items | quantity | integer | true | — |
| fbs_shipment_items | author | character varying | true | — |
| fbs_shipment_items | supply_id | character varying | true | — |
| fbs_shipment_items | account | character varying | true | — |
| fbs_shipment_items | assembly_tasks | jsonb | true | — |
| fbs_shipment_items | warehouse_id | integer | true | — |
| fbs_shipment_items | delivery_type | character varying | true | — |
| fbs_shipment_items | wb_warehouse | character varying | false | — |
| fbs_shipment_items | shipment_date | timestamp with time zone | false | — |
| fbs_shipment_items | status | character varying(20) | true | 'new'::character varying |
| fbs_shipment_items | error_message | text | false | — |
| fbs_shipment_items | retry_count | integer | true | 0 |
| fbs_shipment_items | max_retries | integer | true | 5 |
| fbs_shipment_items | next_retry_at | timestamp with time zone | false | — |
| fbs_shipment_items | movement_id | integer | false | — |
| fbs_shipment_items | created_at | timestamp with time zone | true | now() |
| fbs_shipment_items | updated_at | timestamp with time zone | true | now() |
| fbs_shipment_items | task_resolution_status | character varying(40) | false | — |
| fbs_shipments | shipment_id | integer | true | nextval('wms.fbs_shipments_shipment_id_seq'::regclass) |
| fbs_shipments | received_at | timestamp with time zone | true | now() |
| fbs_shipments | raw_message | jsonb | true | — |
| fbs_shipments | total_items | integer | true | — |
| fbs_shipments | status | character varying(30) | true | 'processing'::character varying |
| fbs_shipments | error_message | text | false | — |
| fbs_shipments | completed_at | timestamp with time zone | false | — |
| fbs_shipments | source | character varying(30) | true | 'standard'::character varying |
| inventory | inventory_id | bigint | true | nextval('wms.inventory_inventory_id_seq'::regclass) |
| inventory | product_id | character varying(50) | true | — |
| inventory | location_id | bigint | true | — |
| inventory | quantity | numeric(10,2) | true | — |
| inventory | status | character varying(50) | false | 'available'::character varying |
| inventory | batch_number | character varying(50) | false | — |
| inventory | container_code | character varying(100) | false | — |
| inventory | created_at | timestamp with time zone | false | now() |
| inventory | updated_at | timestamp with time zone | false | now() |
| kiz | kiz_id | bigint | true | — |
| kiz | kiz_code | text | true | — |
| kiz | product_id | character varying(50) | true | — |
| kiz | location_id | bigint | false | — |
| kiz | lifecycle_status | character varying | true | 'active'::character varying |
| kiz | origin_type | character varying | true | 'warehouse_assignment'::character varying |
| kiz | origin_reference | text | false | — |
| kiz | assigned_at | timestamp with time zone | true | now() |
| kiz | closed_at | timestamp with time zone | false | — |
| kiz | created_at | timestamp with time zone | true | now() |
| kiz | updated_at | timestamp with time zone | true | now() |
| kiz | created_by | character varying(100) | true | — |
| kiz | metadata | jsonb | true | '{}'::jsonb |
| kiz | container_id | bigint | false | — |
| kiz | organization | text | false | — |
| kiz_events | kiz_event_id | bigint | true | — |
| kiz_events | kiz_id | bigint | true | — |
| kiz_events | event_type | character varying | true | — |
| kiz_events | from_status | character varying | false | — |
| kiz_events | to_status | character varying | true | — |
| kiz_events | product_id | character varying(50) | true | — |
| kiz_events | location_id | bigint | false | — |
| kiz_events | author | character varying(100) | true | — |
| kiz_events | reason | text | false | — |
| kiz_events | metadata | jsonb | true | '{}'::jsonb |
| kiz_events | occurred_at | timestamp with time zone | true | now() |
| kiz_events | movement_ref | bigint | false | — |
| kiz_events | container_id | bigint | false | — |
| kiz_movement_links | kiz_id | bigint | true | — |
| kiz_movement_links | movement_ref | bigint | true | — |
| locations | location_id | bigint | true | nextval('wms.locations_location_id_seq'::regclass) |
| locations | parent_location_id | bigint | false | — |
| locations | location_code | character varying(100) | true | — |
| locations | path | wms.ltree | true | — |
| locations | name | character varying(255) | true | — |
| locations | zone_type | character varying(50) | false | — |
| locations | level | integer | true | — |
| locations | max_weight | numeric(10,2) | false | — |
| locations | max_volume | numeric(10,3) | false | — |
| locations | is_active | boolean | false | true |
| locations | is_pickable | boolean | false | false |
| locations | metadata | jsonb | false | — |
| locations | created_at | timestamp with time zone | false | now() |
| locations | updated_at | timestamp with time zone | false | now() |
| movement_registry | movement_ref | bigint | true | — |
| movement_registry | movement_id | bigint | true | — |
| movement_registry | movement_created_at | timestamp with time zone | true | — |
| movement_registry | registered_at | timestamp with time zone | true | now() |
| movements | movement_id | bigint | true | nextval('wms.movements_movement_id_seq'::regclass) |
| movements | movement_type | character varying(50) | true | — |
| movements | product_id | character varying(50) | true | — |
| movements | from_location_id | bigint | false | — |
| movements | to_location_id | bigint | false | — |
| movements | quantity | numeric(10,2) | true | — |
| movements | batch_number | character varying(50) | false | — |
| movements | container_code | character varying(100) | false | — |
| movements | from_container_id | bigint | false | — |
| movements | to_container_id | bigint | false | — |
| movements | user_name | character varying(100) | false | — |
| movements | reason | text | false | — |
| movements | metadata | jsonb | false | — |
| movements | created_at | timestamp with time zone | true | now() |
| movements | source_type | character varying(64) | false | — |
| movements | source_id | bigint | false | — |
| movements | source_item_id | bigint | false | — |

### C.2. Parent constraints, inventory, registry, КИЗ и locations

Приведены local constraints; partition-internal FK entries registry исключены из повторения. Все перечисленные валидированы, кроме явно NOT VALID.

| Relation | Constraint | Definition | Validated |
|---|---|---|---|
| wms.inventory | chk_inventory_status | CHECK (status::text = ANY (ARRAY['available'::character varying, 'damaged'::character varying, 'quarantine'::character varying]::text[])) | true |
| wms.inventory | fk_inventory_location | FOREIGN KEY (location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.inventory | fk_inventory_product | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT | true |
| wms.inventory | inventory_pkey | PRIMARY KEY (inventory_id) | true |
| wms.inventory | inventory_quantity_check | CHECK (quantity >= 0::numeric) | true |
| wms.inventory | uq_inventory | UNIQUE NULLS NOT DISTINCT (product_id, location_id, status, batch_number, container_code) | true |
| wms.kiz | chk_kiz_lifecycle_holder | CHECK (lifecycle_status::text = 'registered'::text AND closed_at IS NULL AND location_id IS NULL AND container_id IS NULL OR lifecycle_status::text = 'active'::text AND closed_at IS NULL AND ((location_id IS NOT NULL)::integer + (container_id IS NOT NULL)::integer) = 1 OR (lifecycle_status::text = ANY (ARRAY['error'::character varying, 'deactivated'::character varying]::text[])) AND closed_at IS NOT NULL AND NOT (location_id IS NOT NULL AND container_id IS NOT NULL) OR lifecycle_status::text = 'shipped'::text AND closed_at IS NOT NULL AND location_id IS NULL AND container_id IS NULL) | true |
| wms.kiz | chk_kiz_lifecycle_status | CHECK (lifecycle_status::text = ANY (ARRAY['registered'::character varying, 'active'::character varying, 'error'::character varying, 'deactivated'::character varying, 'shipped'::character varying]::text[])) | true |
| wms.kiz | chk_kiz_organization | CHECK (organization IS NULL OR btrim(organization) <> ''::text) | true |
| wms.kiz | fk_kiz_container | FOREIGN KEY (container_id) REFERENCES wms.containers(container_id) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.kiz | kiz_created_by_check | CHECK (created_by::text ~ '[^[:space:]]'::text) | true |
| wms.kiz | kiz_kiz_code_check | CHECK (kiz_code <> ''::text AND kiz_code !~ '^[[:space:]]\|[[:space:]]$'::text) | true |
| wms.kiz | kiz_location_id_fkey | FOREIGN KEY (location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.kiz | kiz_metadata_check | CHECK (jsonb_typeof(metadata) = 'object'::text) | true |
| wms.kiz | kiz_origin_type_check | CHECK (origin_type::text = ANY (ARRAY['warehouse_assignment'::character varying, 'receipt_import'::character varying]::text[])) | true |
| wms.kiz | kiz_pkey | PRIMARY KEY (kiz_id) | true |
| wms.kiz | kiz_product_id_fkey | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT | true |
| wms.kiz | trg_kiz_final_holder_integrity | TRIGGER DEFERRABLE INITIALLY DEFERRED | true |
| wms.kiz | uq_kiz_code | UNIQUE (kiz_code) | true |
| wms.kiz_events | chk_kiz_events_event_type | CHECK (event_type::text = ANY (ARRAY['registered'::character varying, 'activated'::character varying, 'assigned'::character varying, 'marked_as_error'::character varying, 'deactivated'::character varying, 'shipped'::character varying]::text[])) | true |
| wms.kiz_events | chk_kiz_events_from_status | CHECK (from_status::text = ANY (ARRAY['registered'::character varying, 'active'::character varying, 'error'::character varying, 'deactivated'::character varying, 'shipped'::character varying]::text[])) | true |
| wms.kiz_events | chk_kiz_events_holder_shape | CHECK (NOT (location_id IS NOT NULL AND container_id IS NOT NULL)) | true |
| wms.kiz_events | chk_kiz_events_to_status | CHECK (to_status::text = ANY (ARRAY['registered'::character varying, 'active'::character varying, 'error'::character varying, 'deactivated'::character varying, 'shipped'::character varying]::text[])) | true |
| wms.kiz_events | chk_kiz_events_transition | CHECK (event_type::text = 'registered'::text AND from_status IS NULL AND to_status::text = 'registered'::text AND location_id IS NULL AND container_id IS NULL AND movement_ref IS NULL OR event_type::text = 'activated'::text AND from_status::text = 'registered'::text AND to_status::text = 'active'::text AND location_id IS NOT NULL AND container_id IS NULL AND movement_ref IS NULL OR event_type::text = 'assigned'::text AND from_status IS NULL AND to_status::text = 'active'::text AND movement_ref IS NULL OR event_type::text = 'marked_as_error'::text AND from_status::text = 'active'::text AND to_status::text = 'error'::text AND movement_ref IS NULL AND reason IS NOT NULL AND reason ~ '[^[:space:]]'::text OR event_type::text = 'deactivated'::text AND from_status::text = 'active'::text AND to_status::text = 'deactivated'::text AND movement_ref IS NULL AND reason IS NOT NULL AND reason ~ '[^[:space:]]'::text OR event_type::text = 'shipped'::text AND (from_status::text = ANY (ARRAY['registered'::character varying, 'active'::character varying]::text[])) AND to_status::text = 'shipped'::text AND location_id IS NOT NULL AND container_id IS NULL AND movement_ref IS NOT NULL) | true |
| wms.kiz_events | fk_kiz_events_container | FOREIGN KEY (container_id) REFERENCES wms.containers(container_id) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.kiz_events | fk_kiz_events_link | FOREIGN KEY (kiz_id, movement_ref) REFERENCES wms.kiz_movement_links(kiz_id, movement_ref) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.kiz_events | kiz_events_author_check | CHECK (author::text ~ '[^[:space:]]'::text) | true |
| wms.kiz_events | kiz_events_kiz_id_fkey | FOREIGN KEY (kiz_id) REFERENCES wms.kiz(kiz_id) ON DELETE RESTRICT | true |
| wms.kiz_events | kiz_events_location_id_fkey | FOREIGN KEY (location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.kiz_events | kiz_events_metadata_check | CHECK (jsonb_typeof(metadata) = 'object'::text) | true |
| wms.kiz_events | kiz_events_pkey | PRIMARY KEY (kiz_event_id) | true |
| wms.kiz_events | kiz_events_product_id_fkey | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT | true |
| wms.kiz_movement_links | fk_kiz_movement_links_kiz | FOREIGN KEY (kiz_id) REFERENCES wms.kiz(kiz_id) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.kiz_movement_links | fk_kiz_movement_links_movement | FOREIGN KEY (movement_ref) REFERENCES wms.movement_registry(movement_ref) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.kiz_movement_links | pk_kiz_movement_links | PRIMARY KEY (kiz_id, movement_ref) | true |
| wms.locations | chk_zone_type | CHECK (zone_type::text = ANY (ARRAY['receiving'::character varying, 'storage'::character varying, 'picking'::character varying, 'packing'::character varying, 'shipping'::character varying, 'quarantine'::character varying, NULL::character varying]::text[])) | true |
| wms.locations | fk_parent_location | FOREIGN KEY (parent_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.locations | locations_location_code_key | UNIQUE (location_code) | true |
| wms.locations | locations_pkey | PRIMARY KEY (location_id) | true |
| wms.movement_registry | fk_movement_registry_movement | FOREIGN KEY (movement_id, movement_created_at) REFERENCES wms.movements(movement_id, created_at) ON UPDATE RESTRICT ON DELETE RESTRICT | true |
| wms.movement_registry | movement_registry_pkey | PRIMARY KEY (movement_ref) | true |
| wms.movement_registry | uq_movement_registry_coordinate | UNIQUE (movement_id, movement_created_at) | true |
| wms.movements | chk_movement_type | CHECK (movement_type::text = ANY (ARRAY['receive'::character varying, 'putaway'::character varying, 'transfer'::character varying, 'pick'::character varying, 'ship'::character varying, 'unpack'::character varying, 'adjust'::character varying, 'kit_assembly'::character varying, 'kit_disassembly'::character varying, 're_sorting'::character varying]::text[])) | true |
| wms.movements | chk_movements_has_side | CHECK (from_location_id IS NOT NULL OR to_location_id IS NOT NULL) NOT VALID | false |
| wms.movements | chk_movements_quantity_positive | CHECK (quantity > 0::numeric) NOT VALID | false |
| wms.movements | fk_movement_from_location | FOREIGN KEY (from_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.movements | fk_movement_product | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT | true |
| wms.movements | fk_movement_to_location | FOREIGN KEY (to_location_id) REFERENCES wms.locations(location_id) ON DELETE RESTRICT | true |
| wms.movements | uq_movements_movement_id_created_at | UNIQUE (movement_id, created_at) | true |

### C.3. Индексы

| Таблица | Index | Definition |
|---|---|---|
| inventory | idx_inventory_batch | CREATE INDEX idx_inventory_batch ON wms.inventory USING btree (batch_number) |
| inventory | idx_inventory_container | CREATE INDEX idx_inventory_container ON wms.inventory USING btree (container_code) WHERE (container_code IS NOT NULL) |
| inventory | idx_inventory_location | CREATE INDEX idx_inventory_location ON wms.inventory USING btree (location_id) |
| inventory | idx_inventory_product | CREATE INDEX idx_inventory_product ON wms.inventory USING btree (product_id) |
| inventory | idx_inventory_product_location | CREATE INDEX idx_inventory_product_location ON wms.inventory USING btree (product_id, location_id) |
| inventory | idx_inventory_status | CREATE INDEX idx_inventory_status ON wms.inventory USING btree (status) |
| inventory | inventory_pkey | CREATE UNIQUE INDEX inventory_pkey ON wms.inventory USING btree (inventory_id) |
| inventory | uq_inventory | CREATE UNIQUE INDEX uq_inventory ON wms.inventory USING btree (product_id, location_id, status, batch_number, container_code) NULLS NOT DISTINCT |
| kiz | idx_kiz_active_container_product | CREATE INDEX idx_kiz_active_container_product ON wms.kiz USING btree (container_id, product_id, kiz_id) WHERE ((lifecycle_status)::text = 'active'::text) |
| kiz | idx_kiz_active_receipt_origin | CREATE INDEX idx_kiz_active_receipt_origin ON wms.kiz USING btree (origin_type, origin_reference, product_id, kiz_id) WHERE ((lifecycle_status)::text = 'active'::text) |
| kiz | idx_kiz_active_scope | CREATE INDEX idx_kiz_active_scope ON wms.kiz USING btree (product_id, location_id) WHERE ((lifecycle_status)::text = 'active'::text) |
| kiz | idx_kiz_current_receipt_origin | CREATE INDEX idx_kiz_current_receipt_origin ON wms.kiz USING btree (origin_type, origin_reference, product_id, lifecycle_status, kiz_id) WHERE ((lifecycle_status)::text = ANY ((ARRAY['registered'::character varying, 'active'::character varying])::text[])) |
| kiz | idx_kiz_location_status_id | CREATE INDEX idx_kiz_location_status_id ON wms.kiz USING btree (location_id, lifecycle_status, kiz_id) |
| kiz | idx_kiz_organization | CREATE INDEX idx_kiz_organization ON wms.kiz USING btree (organization, kiz_id) WHERE (organization IS NOT NULL) |
| kiz | idx_kiz_product_status_id | CREATE INDEX idx_kiz_product_status_id ON wms.kiz USING btree (product_id, lifecycle_status, kiz_id) |
| kiz | kiz_pkey | CREATE UNIQUE INDEX kiz_pkey ON wms.kiz USING btree (kiz_id) |
| kiz | uq_kiz_code | CREATE UNIQUE INDEX uq_kiz_code ON wms.kiz USING btree (kiz_code) |
| kiz_events | idx_kiz_events_history | CREATE INDEX idx_kiz_events_history ON wms.kiz_events USING btree (kiz_id, occurred_at, kiz_event_id) |
| kiz_events | kiz_events_pkey | CREATE UNIQUE INDEX kiz_events_pkey ON wms.kiz_events USING btree (kiz_event_id) |
| kiz_movement_links | idx_kiz_movement_links_movement_ref_kiz_id | CREATE INDEX idx_kiz_movement_links_movement_ref_kiz_id ON wms.kiz_movement_links USING btree (movement_ref, kiz_id) |
| kiz_movement_links | pk_kiz_movement_links | CREATE UNIQUE INDEX pk_kiz_movement_links ON wms.kiz_movement_links USING btree (kiz_id, movement_ref) |
| locations | idx_locations_active | CREATE INDEX idx_locations_active ON wms.locations USING btree (is_active) WHERE (is_active = true) |
| locations | idx_locations_code | CREATE INDEX idx_locations_code ON wms.locations USING btree (location_code) |
| locations | idx_locations_parent | CREATE INDEX idx_locations_parent ON wms.locations USING btree (parent_location_id) |
| locations | idx_locations_path | CREATE INDEX idx_locations_path ON wms.locations USING gist (path) |
| locations | idx_locations_zone_type | CREATE INDEX idx_locations_zone_type ON wms.locations USING btree (zone_type) WHERE (zone_type IS NOT NULL) |
| locations | locations_location_code_key | CREATE UNIQUE INDEX locations_location_code_key ON wms.locations USING btree (location_code) |
| locations | locations_pkey | CREATE UNIQUE INDEX locations_pkey ON wms.locations USING btree (location_id) |
| movement_registry | movement_registry_pkey | CREATE UNIQUE INDEX movement_registry_pkey ON wms.movement_registry USING btree (movement_ref) |
| movement_registry | uq_movement_registry_coordinate | CREATE UNIQUE INDEX uq_movement_registry_coordinate ON wms.movement_registry USING btree (movement_id, movement_created_at) |
| movements | idx_movements_container | CREATE INDEX idx_movements_container ON ONLY wms.movements USING btree (container_code) |
| movements | idx_movements_created | CREATE INDEX idx_movements_created ON ONLY wms.movements USING btree (created_at) |
| movements | idx_movements_from_location | CREATE INDEX idx_movements_from_location ON ONLY wms.movements USING btree (from_location_id) |
| movements | idx_movements_kit_operation | CREATE INDEX idx_movements_kit_operation ON ONLY wms.movements USING btree (source_id, created_at) WHERE ((source_type)::text = 'kit_operation'::text) |
| movements | idx_movements_product | CREATE INDEX idx_movements_product ON ONLY wms.movements USING btree (product_id) |
| movements | idx_movements_product_created | CREATE INDEX idx_movements_product_created ON ONLY wms.movements USING btree (product_id, created_at) |
| movements | idx_movements_source | CREATE INDEX idx_movements_source ON ONLY wms.movements USING btree (source_type, source_id, created_at) |
| movements | idx_movements_to_location | CREATE INDEX idx_movements_to_location ON ONLY wms.movements USING btree (to_location_id) |
| movements | idx_movements_type | CREATE INDEX idx_movements_type ON ONLY wms.movements USING btree (movement_type) |
| movements | uq_movements_movement_id_created_at | CREATE UNIQUE INDEX uq_movements_movement_id_created_at ON ONLY wms.movements USING btree (movement_id, created_at) |

### C.4. Партиции

| Relation | Bounds UTC |
|---|---|
| wms.movements | — |
| wms.movements_2026_01 | FOR VALUES FROM ('2025-12-31 21:00:00+00') TO ('2026-01-31 21:00:00+00') |
| wms.movements_2026_02 | FOR VALUES FROM ('2026-01-31 21:00:00+00') TO ('2026-02-28 21:00:00+00') |
| wms.movements_2026_03 | FOR VALUES FROM ('2026-02-28 21:00:00+00') TO ('2026-03-31 21:00:00+00') |
| wms.movements_2026_04 | FOR VALUES FROM ('2026-03-31 21:00:00+00') TO ('2026-04-30 21:00:00+00') |
| wms.movements_2026_05 | FOR VALUES FROM ('2026-04-30 21:00:00+00') TO ('2026-05-31 21:00:00+00') |
| wms.movements_2026_06 | FOR VALUES FROM ('2026-05-31 21:00:00+00') TO ('2026-06-30 21:00:00+00') |
| wms.movements_2026_07 | FOR VALUES FROM ('2026-06-30 21:00:00+00') TO ('2026-07-31 21:00:00+00') |
| wms.movements_2026_08 | FOR VALUES FROM ('2026-07-31 21:00:00+00') TO ('2026-08-31 21:00:00+00') |
| wms.movements_2026_09 | FOR VALUES FROM ('2026-08-31 21:00:00+00') TO ('2026-09-30 21:00:00+00') |
| wms.movements_2026_10 | FOR VALUES FROM ('2026-09-30 21:00:00+00') TO ('2026-10-31 21:00:00+00') |
| wms.movements_2026_11 | FOR VALUES FROM ('2026-10-31 21:00:00+00') TO ('2026-11-30 21:00:00+00') |
| wms.movements_2026_12 | FOR VALUES FROM ('2026-11-30 21:00:00+00') TO ('2026-12-31 21:00:00+00') |

### C.5. Все non-internal triggers на непартиционных wms relations

Все 37 полученных triggers включены (tgenabled=O). На parent и 12 movement partitions отдельно подтверждено по 3 movement trigger; constraints наследуются. Данные catalog, не команды для выполнения.

```text
CREATE TRIGGER trg_active_content_container_state BEFORE INSERT OR UPDATE OF container_id, status ON wms.container_contents FOR EACH ROW EXECUTE FUNCTION wms.guard_active_content_container_state();
CREATE TRIGGER trg_sync_container_contents_to_inventory AFTER INSERT ON wms.container_contents FOR EACH ROW EXECUTE FUNCTION wms.sync_container_to_inventory();
CREATE CONSTRAINT TRIGGER trg_container_operation_items_complete AFTER INSERT OR UPDATE OF movement_ref, outgoing_movement_ref, incoming_movement_ref ON wms.container_operation_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_container_operation();
CREATE TRIGGER trg_container_operation_items_guard BEFORE DELETE OR UPDATE ON wms.container_operation_items FOR EACH ROW EXECUTE FUNCTION wms.guard_container_operation_item_update();
CREATE CONSTRAINT TRIGGER trg_container_operations_complete AFTER INSERT OR UPDATE OF result_payload ON wms.container_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_container_operation();
CREATE TRIGGER trg_container_operations_guard BEFORE DELETE OR UPDATE ON wms.container_operations FOR EACH ROW EXECUTE FUNCTION wms.guard_container_operation_update();
CREATE TRIGGER trg_container_empty_state BEFORE UPDATE OF status ON wms.containers FOR EACH ROW EXECUTE FUNCTION wms.guard_container_empty_state();
CREATE TRIGGER trg_container_identity_immutable BEFORE DELETE OR UPDATE OF container_id, qr_code ON wms.containers FOR EACH ROW EXECUTE FUNCTION wms.guard_container_identity();
CREATE TRIGGER trg_containers_updated_at BEFORE UPDATE ON wms.containers FOR EACH ROW EXECUTE FUNCTION wms.update_containers_timestamp();
CREATE TRIGGER trg_fbs_kiz_bindings_identity BEFORE DELETE OR UPDATE ON wms.fbs_kiz_bindings FOR EACH ROW EXECUTE FUNCTION wms.guard_fbs_kiz_binding_identity();
CREATE TRIGGER trg_fbs_item_updated_at BEFORE UPDATE ON wms.fbs_shipment_items FOR EACH ROW EXECUTE FUNCTION wms.update_fbs_item_updated_at();
CREATE TRIGGER trg_inventory_updated_at BEFORE UPDATE ON wms.inventory FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_timestamp();
CREATE TRIGGER trg_kiz_inventory_guard BEFORE DELETE OR UPDATE ON wms.inventory FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_inventory();
CREATE CONSTRAINT TRIGGER trg_kiz_final_holder_integrity AFTER INSERT OR UPDATE ON wms.kiz DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_kiz_final_holder_integrity();
CREATE TRIGGER trg_kiz_identity_guard BEFORE DELETE OR UPDATE ON wms.kiz FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_identity();
CREATE TRIGGER trg_kiz_organization_immutable BEFORE UPDATE OF organization ON wms.kiz FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_organization_immutable();
CREATE CONSTRAINT TRIGGER trg_complete_kiz_container_holder AFTER INSERT ON wms.kiz_container_holder_authorizations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_container_holder();
CREATE TRIGGER trg_kiz_events_immutable BEFORE DELETE OR UPDATE ON wms.kiz_events FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_event_immutable();
CREATE TRIGGER trg_kiz_import_message_kiz_immutable BEFORE DELETE OR UPDATE ON wms.kiz_import_message_kiz FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_import_message_kiz_immutable();
CREATE CONSTRAINT TRIGGER trg_complete_kiz_location_transfer AFTER INSERT ON wms.kiz_location_update_authorizations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_location_transfer();
CREATE TRIGGER trg_kiz_movement_links_immutable BEFORE DELETE OR UPDATE ON wms.kiz_movement_links FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_movement_link_immutable();
CREATE TRIGGER trg_kiz_operation_items_guard BEFORE DELETE OR UPDATE ON wms.kiz_operation_items FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_operation_item_update();
CREATE TRIGGER trg_kiz_operations_guard BEFORE DELETE OR UPDATE ON wms.kiz_operations FOR EACH ROW EXECUTE FUNCTION wms.guard_kiz_operation_update();
CREATE CONSTRAINT TRIGGER trg_kiz_operations_result_at_commit AFTER INSERT OR UPDATE OF result_payload ON wms.kiz_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_kiz_operation_result_at_commit();
CREATE CONSTRAINT TRIGGER trg_complete_kiz_shipment AFTER INSERT ON wms.kiz_shipment_authorizations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.require_complete_kiz_shipment();
CREATE TRIGGER trg_generate_location_code BEFORE INSERT ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.generate_location_code();
CREATE TRIGGER trg_generate_location_path BEFORE INSERT OR UPDATE OF parent_location_id ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.generate_location_path();
CREATE TRIGGER trg_locations_updated_at BEFORE UPDATE ON wms.locations FOR EACH ROW EXECUTE FUNCTION wms.update_locations_timestamp();
CREATE TRIGGER trg_movement_registry_immutable BEFORE DELETE OR UPDATE ON wms.movement_registry FOR EACH ROW EXECUTE FUNCTION wms.guard_movement_registry_immutable();
CREATE TRIGGER trg_guard_controlled_container_movement BEFORE INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION wms.guard_controlled_container_movement();
CREATE TRIGGER trg_register_movement_identity AFTER INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION wms.register_movement_identity();
CREATE TRIGGER trg_update_inventory_from_movement AFTER INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_from_movement();
CREATE TRIGGER trg_operation_locations_updated_at BEFORE UPDATE ON wms.operation_locations FOR EACH ROW EXECUTE FUNCTION wms.update_updated_at_column();
CREATE TRIGGER trg_receipt_items_updated_at BEFORE UPDATE ON wms.receipt_items FOR EACH ROW EXECUTE FUNCTION wms.update_inventory_timestamp();
CREATE TRIGGER trg_re_sorting_operations_updated_at BEFORE UPDATE ON wms.re_sorting_operations FOR EACH ROW EXECUTE FUNCTION wms.update_updated_at_column();
CREATE TRIGGER trg_stock_reservation_orders_updated_at BEFORE UPDATE ON wms.stock_reservation_orders FOR EACH ROW EXECUTE FUNCTION wms.update_updated_at_column();
CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON wms.tasks FOR EACH ROW EXECUTE FUNCTION wms.update_updated_at_column();
```

### C.6. Дополнительные FBS runtime columns

| Таблица | Колонка | Тип |
|---|---|---|
| fbs_kiz_bindings | binding_id | bigint |
| fbs_kiz_bindings | task_id | bigint |
| fbs_kiz_bindings | kiz_code | text |
| fbs_kiz_bindings | kiz_id | bigint |
| fbs_kiz_bindings | product_id | character varying |
| fbs_kiz_bindings | first_shipment_id | integer |
| fbs_kiz_bindings | first_item_id | integer |
| fbs_kiz_bindings | state | character varying(40) |
| fbs_kiz_bindings | created_at | timestamp with time zone |
| fbs_kiz_bindings | updated_at | timestamp with time zone |
| fbs_shipment_task_results | result_id | bigint |
| fbs_shipment_task_results | shipment_id | integer |
| fbs_shipment_task_results | item_id | integer |
| fbs_shipment_task_results | occurrence_index | integer |
| fbs_shipment_task_results | task_id | bigint |
| fbs_shipment_task_results | product_id | character varying |
| fbs_shipment_task_results | outcome | character varying(40) |
| fbs_shipment_task_results | effect_quantity | smallint |
| fbs_shipment_task_results | movement_id | bigint |
| fbs_shipment_task_results | movement_created_at | timestamp with time zone |
| fbs_shipment_task_results | existing_success_item_id | integer |
| fbs_shipment_task_results | existing_movement_id | bigint |
| fbs_shipment_task_results | existing_movement_created_at | timestamp with time zone |
| fbs_shipment_task_results | is_shipped_before | boolean |
| fbs_shipment_task_results | reason | text |
| fbs_shipment_task_results | details | jsonb |
| fbs_shipment_task_results | attempt_count | integer |
| fbs_shipment_task_results | first_processed_at | timestamp with time zone |
| fbs_shipment_task_results | last_processed_at | timestamp with time zone |
| fbs_shipment_task_results | last_error | text |
| fbs_shipment_task_results | created_at | timestamp with time zone |
| fbs_shipment_task_results | updated_at | timestamp with time zone |
| fbs_shipment_task_results | kiz_binding_id | bigint |
| fbs_shipment_task_results | movement_ref | bigint |
| fbs_shipment_task_results | source_kind | character varying(40) |
| fbs_shipment_task_results | source_location_id | bigint |
| fbs_shipment_task_results | source_container_id | bigint |

## Приложение F. Точные definitions действующих функций

**БД.** Текст получен только через pg_get_functiondef; ни одна из этих функций не вызывалась. Блоки являются снимком metadata, не предлагаемыми миграциями. Из 125 функций wms (включая extension functions) ниже приведены функции, определяющие stock/КИЗ/registry/container/location protocol. Прямое INSERT/UPDATE/DELETE wms.inventory среди прочитанных функций обнаружено только в update_inventory_from_movement; внешние SQL writers этим не исключаются.

### wms.update_inventory_from_movement()

```sql
CREATE OR REPLACE FUNCTION wms.update_inventory_from_movement()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    affected_location_id BIGINT;
    change_quantity DECIMAL(10,2);
    current_qty DECIMAL(10,2);
BEGIN
    -- Приход в локацию
    IF NEW.to_location_id IS NOT NULL THEN
        affected_location_id := NEW.to_location_id;
        change_quantity := NEW.quantity;

        INSERT INTO wms.inventory (
            product_id,
            location_id,
            quantity,
            status,
            batch_number,
            container_code
        )
        VALUES (
            NEW.product_id,
            affected_location_id,
            change_quantity,
            'available',
            NEW.batch_number,
            NEW.container_code
        )
        ON CONFLICT (product_id, location_id, status, batch_number, container_code)
        DO UPDATE SET
            quantity = wms.inventory.quantity + EXCLUDED.quantity,
            updated_at = NOW();
    END IF;

    -- Расход из локации
    IF NEW.from_location_id IS NOT NULL THEN
        affected_location_id := NEW.from_location_id;
        change_quantity := -ABS(NEW.quantity);

        UPDATE wms.inventory
        SET quantity = quantity + change_quantity,
            updated_at = NOW()
        WHERE product_id = NEW.product_id
          AND location_id = affected_location_id
          AND status = 'available'
          AND COALESCE(batch_number, '') = COALESCE(NEW.batch_number, '')
          AND COALESCE(container_code, '') = COALESCE(NEW.container_code, '');

        IF NOT FOUND
           AND NEW.movement_type IN (
               'ship',
               'transfer',
               'kit_assembly',
               'kit_disassembly'
           )
        THEN
            SELECT COALESCE(SUM(quantity), 0)
            INTO current_qty
            FROM wms.inventory
            WHERE product_id = NEW.product_id
              AND location_id = affected_location_id;

            RAISE EXCEPTION
                'Недостаточно остатка для списания: product_id=%, location_id=%, movement_type=%, запрошено=%, текущий остаток=%',
                NEW.product_id,
                affected_location_id,
                NEW.movement_type,
                ABS(change_quantity),
                current_qty;
        END IF;

        DELETE FROM wms.inventory
        WHERE product_id = NEW.product_id
          AND location_id = affected_location_id
          AND quantity <= 0;
    END IF;

    RETURN NEW;
END;
$function$
```

### wms.guard_kiz_inventory()

```sql
CREATE OR REPLACE FUNCTION wms.guard_kiz_inventory()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE identified bigint; remaining numeric; holder_container_id bigint; authorized_operation bigint;
BEGIN
 IF OLD.status<>'available' OR OLD.batch_number IS NOT NULL THEN
   IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END IF;
 remaining=0;
 IF TG_OP='UPDATE' AND ROW(OLD.product_id,OLD.location_id,OLD.status,OLD.batch_number,OLD.container_code)
    IS NOT DISTINCT FROM ROW(NEW.product_id,NEW.location_id,NEW.status,NEW.batch_number,NEW.container_code) THEN
   IF NEW.quantity>=OLD.quantity THEN RETURN NEW; END IF; remaining=NEW.quantity;
 END IF;
 IF OLD.container_code IS NULL THEN
   SELECT count(*) INTO identified FROM wms.kiz WHERE product_id=OLD.product_id
     AND location_id=OLD.location_id AND container_id IS NULL AND lifecycle_status='active';
 ELSE
   SELECT container_id INTO holder_container_id FROM wms.containers WHERE qr_code=OLD.container_code;
   authorized_operation=NULLIF(current_setting('wms.kiz_container_operation_id',true),'')::bigint;
   IF authorized_operation IS NOT NULL AND EXISTS(
      SELECT 1 FROM wms.container_operations o WHERE o.operation_id=authorized_operation
        AND o.operation_type='move' AND o.container_id=holder_container_id AND o.result_payload IS NULL
   ) THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
   SELECT count(*) INTO identified FROM wms.kiz WHERE product_id=OLD.product_id
     AND container_id=holder_container_id AND location_id IS NULL AND lifecycle_status='active';
 END IF;
 IF identified>0 AND remaining<identified THEN
   RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Недостаточно неидентифицированного остатка',
     DETAIL=jsonb_build_object('product_id',OLD.product_id,'location_id',OLD.location_id,
       'container_code',OLD.container_code,'physical_quantity',OLD.quantity,
       'identified_quantity',identified,'requested_remaining',remaining)::text;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $function$
```

### wms.guard_controlled_container_movement()

```sql
CREATE OR REPLACE FUNCTION wms.guard_controlled_container_movement()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.container_code IS NOT NULL
       AND (
           NEW.source_type IS DISTINCT FROM 'container_operation'
           OR NEW.source_id IS NULL
           OR NEW.source_item_id IS NULL
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Container inventory movement requires controlled container_operation provenance';
    END IF;
    RETURN NEW;
END;
$function$
```

### wms.register_movement_identity()

```sql
CREATE OR REPLACE FUNCTION wms.register_movement_identity()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    INSERT INTO wms.movement_registry (movement_id, movement_created_at)
    VALUES (NEW.movement_id, NEW.created_at);
    RETURN NEW;
END;
$function$
```

### wms.guard_movement_registry_immutable()

```sql
CREATE OR REPLACE FUNCTION wms.guard_movement_registry_immutable()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'movement_registry mappings are immutable';
END;
$function$
```

### wms.guard_kiz_identity()

```sql
CREATE OR REPLACE FUNCTION wms.guard_kiz_identity()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Hard delete КИЗ запрещён';
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM
       ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status IN ('registered', 'active')
       AND OLD.closed_at IS NULL
       AND OLD.container_id IS NULL
       AND (
           OLD.lifecycle_status = 'registered' AND OLD.location_id IS NULL
           OR OLD.lifecycle_status = 'active' AND OLD.location_id IS NOT NULL
       )
       AND NEW.lifecycle_status = 'shipped'
       AND NEW.location_id IS NULL
       AND NEW.container_id IS NULL
       AND NEW.closed_at IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM wms.kiz_shipment_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND (
                 OLD.lifecycle_status = 'registered'
                 OR auth.from_location_id = OLD.location_id
             )
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata)
       AND OLD.lifecycle_status = 'registered'
       AND OLD.location_id IS NULL AND OLD.container_id IS NULL AND OLD.closed_at IS NULL
       AND NEW.lifecycle_status = 'active'
       AND NEW.location_id IS NOT NULL AND NEW.container_id IS NULL AND NEW.closed_at IS NULL
       AND EXISTS (
           SELECT 1
           FROM wms.kiz_activation_authorizations auth
           JOIN wms.kiz_operations operation USING (operation_id)
           JOIN wms.kiz_operation_items item
             ON item.operation_id = operation.operation_id
            AND item.external_line_id = OLD.kiz_code
            AND item.movement_ref IS NULL
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND auth.location_id = NEW.location_id
             AND operation.operation_type = 'activate'
             AND operation.source_system = 'api'
             AND operation.result_payload IS NULL
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.container_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id, OLD.container_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND NEW.location_id IS DISTINCT FROM OLD.location_id
       AND EXISTS (
           SELECT 1 FROM wms.kiz_location_update_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND auth.from_location_id = OLD.location_id
             AND auth.to_location_id = NEW.location_id
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata, NEW.lifecycle_status, NEW.closed_at)
       IS NOT DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.origin_type, OLD.origin_reference, OLD.assigned_at, OLD.created_at,
           OLD.created_by, OLD.metadata, OLD.lifecycle_status, OLD.closed_at)
       AND OLD.lifecycle_status = 'active'
       AND EXISTS (
           SELECT 1 FROM wms.kiz_container_holder_authorizations auth
           WHERE auth.transaction_id = txid_current()
             AND auth.kiz_id = OLD.kiz_id
             AND auth.product_id = OLD.product_id
             AND (
                 auth.direction = 'fill'
                 AND OLD.location_id = auth.location_id
                 AND OLD.container_id IS NULL
                 AND NEW.location_id IS NULL
                 AND NEW.container_id = auth.container_id
                 OR auth.direction = 'extract'
                 AND OLD.location_id IS NULL
                 AND OLD.container_id = auth.container_id
                 AND NEW.location_id = auth.location_id
                 AND NEW.container_id IS NULL
             )
       ) THEN
        NEW.updated_at = now();
        RETURN NEW;
    END IF;

    IF ROW(NEW.kiz_id, NEW.kiz_code, NEW.product_id, NEW.location_id, NEW.container_id,
           NEW.origin_type, NEW.origin_reference, NEW.assigned_at, NEW.created_at,
           NEW.created_by, NEW.metadata)
       IS DISTINCT FROM ROW(OLD.kiz_id, OLD.kiz_code, OLD.product_id,
           OLD.location_id, OLD.container_id, OLD.origin_type, OLD.origin_reference,
           OLD.assigned_at, OLD.created_at, OLD.created_by, OLD.metadata)
       OR OLD.lifecycle_status <> 'active'
       OR NEW.lifecycle_status NOT IN ('error', 'deactivated') THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимое изменение КИЗ';
    END IF;

    NEW.closed_at = now();
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$
```

### wms.require_kiz_final_holder_integrity()

```sql
CREATE OR REPLACE FUNCTION wms.require_kiz_final_holder_integrity()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE physical numeric; contents numeric; identified bigint; holder record;
BEGIN
 IF NEW.lifecycle_status<>'active' THEN RETURN NULL; END IF;
 IF NEW.container_id IS NULL THEN
   SELECT count(*) INTO identified FROM wms.kiz WHERE lifecycle_status='active'
     AND product_id=NEW.product_id AND location_id=NEW.location_id AND container_id IS NULL;
   SELECT COALESCE(i.quantity,0) INTO physical FROM (SELECT 1) seed LEFT JOIN wms.inventory i
     ON i.product_id=NEW.product_id AND i.location_id=NEW.location_id AND i.status='available'
    AND i.batch_number IS NULL AND i.container_code IS NULL;
   IF identified>physical THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Final loose KIZ quantity exceeds physical inventory'; END IF;
 ELSE
   SELECT c.location_id,c.qr_code INTO holder FROM wms.containers c WHERE c.container_id=NEW.container_id;
   IF NOT FOUND OR holder.location_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Contained KIZ holder has no valid location'; END IF;
   SELECT count(*) INTO identified FROM wms.kiz WHERE lifecycle_status='active'
     AND product_id=NEW.product_id AND container_id=NEW.container_id AND location_id IS NULL;
   SELECT COALESCE(i.quantity,0) INTO physical FROM (SELECT 1) seed LEFT JOIN wms.inventory i
     ON i.product_id=NEW.product_id AND i.location_id=holder.location_id AND i.status='available'
    AND i.batch_number IS NULL AND i.container_code=holder.qr_code;
   SELECT COALESCE(cc.quantity,0) INTO contents FROM (SELECT 1) seed LEFT JOIN wms.container_contents cc
     ON cc.container_id=NEW.container_id AND cc.product_id=NEW.product_id
    AND cc.batch_number IS NULL AND cc.status='active';
   IF identified>physical OR identified>contents THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Final contained KIZ quantity exceeds physical projection'; END IF;
 END IF;
 RETURN NULL;
END $function$
```

### wms.require_complete_kiz_shipment()

```sql
CREATE OR REPLACE FUNCTION wms.require_complete_kiz_shipment()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM wms.kiz_operation_items item
        JOIN wms.kiz_operations operation USING (operation_id)
        JOIN wms.movement_registry registry ON registry.movement_ref = item.movement_ref
        JOIN wms.movements movement
          ON movement.movement_id = registry.movement_id
         AND movement.created_at = registry.movement_created_at
        JOIN wms.kiz_movement_links link
          ON link.kiz_id = NEW.kiz_id AND link.movement_ref = item.movement_ref
        JOIN wms.kiz kiz ON kiz.kiz_id = NEW.kiz_id
        JOIN wms.kiz_events event
          ON event.kiz_id = NEW.kiz_id
         AND event.movement_ref = item.movement_ref
         AND event.event_type = 'shipped'
         AND event.from_status IN ('registered', 'active')
         AND event.to_status = 'shipped'
        JOIN wms.locations source_location
          ON source_location.location_id = NEW.from_location_id
        CROSS JOIN LATERAL jsonb_array_elements(operation.result_payload->'items') result_item
        WHERE item.operation_item_id = NEW.operation_item_id
          AND operation.operation_type = 'ship'
          AND operation.result_payload IS NOT NULL
          AND movement.movement_type = 'ship'
          AND movement.source_type = 'kiz_operation'
          AND movement.source_id = operation.operation_id
          AND movement.source_item_id = item.operation_item_id
          AND movement.product_id = NEW.product_id
          AND movement.from_location_id = NEW.from_location_id
          AND movement.to_location_id IS NULL
          AND movement.quantity = NEW.quantity
          AND kiz.product_id = NEW.product_id
          AND kiz.lifecycle_status = 'shipped'
          AND kiz.location_id IS NULL
          AND kiz.container_id IS NULL
          AND kiz.closed_at IS NOT NULL
          AND event.product_id = NEW.product_id
          AND event.location_id = NEW.from_location_id
          AND result_item->>'external_line_id' = item.external_line_id
          AND result_item->>'product_id' = NEW.product_id
          AND result_item->>'from_location_code' = source_location.location_code
          AND (result_item->>'quantity')::numeric = NEW.quantity
          AND (result_item->>'movement_ref')::bigint = item.movement_ref
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Incomplete controlled KIZ shipment';
    END IF;
    DELETE FROM wms.kiz_shipment_authorizations
    WHERE transaction_id = NEW.transaction_id AND kiz_id = NEW.kiz_id;
    RETURN NULL;
END;
$function$
```

### wms.require_complete_kiz_location_transfer()

```sql
CREATE OR REPLACE FUNCTION wms.require_complete_kiz_location_transfer()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM wms.kiz_operation_items oi
        JOIN wms.kiz_operations o USING (operation_id)
        JOIN wms.movement_registry r ON r.movement_ref = oi.movement_ref
        JOIN wms.movements m
          ON m.movement_id = r.movement_id AND m.created_at = r.movement_created_at
        JOIN wms.kiz_movement_links l
          ON l.kiz_id = NEW.kiz_id AND l.movement_ref = oi.movement_ref
        JOIN wms.kiz k ON k.kiz_id = NEW.kiz_id
        WHERE oi.operation_item_id = NEW.operation_item_id
          AND o.operation_type = 'transfer'
          AND o.result_payload IS NOT NULL
          AND m.movement_type = 'transfer'
          AND m.source_type = 'kiz_operation'
          AND m.source_id = o.operation_id
          AND m.source_item_id = oi.operation_item_id
          AND m.product_id = NEW.product_id
          AND m.from_location_id = NEW.from_location_id
          AND m.to_location_id = NEW.to_location_id
          AND k.product_id = NEW.product_id
          AND k.location_id = NEW.to_location_id
          AND k.lifecycle_status = 'active'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'Incomplete controlled KIZ location transfer';
    END IF;
    DELETE FROM wms.kiz_location_update_authorizations
    WHERE transaction_id = NEW.transaction_id AND kiz_id = NEW.kiz_id;
    RETURN NULL;
END;
$function$
```

### wms.require_complete_kiz_container_holder()

```sql
CREATE OR REPLACE FUNCTION wms.require_complete_kiz_container_holder()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
 IF NOT EXISTS(
   SELECT 1 FROM wms.container_operation_items oi
   JOIN wms.container_operations o USING(operation_id)
   JOIN wms.kiz k ON k.kiz_id=NEW.kiz_id
   JOIN wms.kiz_movement_links lo ON lo.kiz_id=NEW.kiz_id AND lo.movement_ref=oi.outgoing_movement_ref
   JOIN wms.kiz_movement_links li ON li.kiz_id=NEW.kiz_id AND li.movement_ref=oi.incoming_movement_ref
   WHERE oi.operation_item_id=NEW.operation_item_id AND o.result_payload IS NOT NULL
     AND oi.batch_number IS NULL AND oi.product_id=NEW.product_id
     AND ((NEW.direction='fill' AND o.operation_type='fill' AND k.location_id IS NULL AND k.container_id=NEW.container_id)
       OR (NEW.direction='extract' AND o.operation_type IN ('extract','unpack_all') AND k.location_id=NEW.location_id AND k.container_id IS NULL))
 ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Incomplete controlled container KIZ transition'; END IF;
 DELETE FROM wms.kiz_container_holder_authorizations
 WHERE transaction_id=NEW.transaction_id AND kiz_id=NEW.kiz_id;
 RETURN NULL;
END $function$
```

### wms.require_kiz_operation_result_at_commit()

```sql
CREATE OR REPLACE FUNCTION wms.require_kiz_operation_result_at_commit()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM wms.kiz_operations
        WHERE operation_id = NEW.operation_id
          AND result_payload IS NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'KIZ operation must store its result before commit';
    END IF;
    RETURN NULL;
END;
$function$
```

### wms.ship_kiz(bigint,bigint,character varying,bigint,numeric)

```sql
CREATE OR REPLACE FUNCTION wms.ship_kiz(p_operation_item_id bigint, p_kiz_id bigint, p_expected_product_id character varying, p_expected_source_location_id bigint, p_quantity numeric)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE
    operation_is_valid boolean;
    current_kiz record;
BEGIN
    IF p_quantity IS NULL OR p_quantity <= 0 OR p_quantity <> trunc(p_quantity) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Ship quantity must be a positive integer';
    END IF;

    SELECT true INTO operation_is_valid
    FROM wms.kiz_operation_items item
    JOIN wms.kiz_operations operation USING (operation_id)
    WHERE item.operation_item_id = p_operation_item_id
      AND operation.operation_type = 'ship'
      AND operation.result_payload IS NULL
      AND item.movement_ref IS NULL;
    IF operation_is_valid IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимый KIZ ship protocol';
    END IF;

    SELECT kiz_id, product_id, location_id, container_id, lifecycle_status, closed_at
    INTO current_kiz
    FROM wms.kiz
    WHERE kiz_id = p_kiz_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_kiz.lifecycle_status NOT IN ('registered', 'active')
       OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id <> p_expected_product_id
       OR current_kiz.container_id IS NOT NULL
       OR current_kiz.lifecycle_status = 'registered' AND current_kiz.location_id IS NOT NULL
       OR current_kiz.lifecycle_status = 'active'
          AND current_kiz.location_id IS DISTINCT FROM p_expected_source_location_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'КИЗ изменился до shipment';
    END IF;

    INSERT INTO wms.kiz_shipment_authorizations(
        transaction_id, operation_item_id, kiz_id, product_id, from_location_id, quantity
    ) VALUES (
        txid_current(), p_operation_item_id, p_kiz_id, p_expected_product_id,
        p_expected_source_location_id, p_quantity
    );

    UPDATE wms.kiz
    SET lifecycle_status = 'shipped',
        location_id = NULL,
        container_id = NULL,
        closed_at = now()
    WHERE kiz_id = p_kiz_id;
END;
$function$
```

### wms.transfer_kiz_location(bigint,bigint,character varying,bigint,bigint)

```sql
CREATE OR REPLACE FUNCTION wms.transfer_kiz_location(p_operation_item_id bigint, p_kiz_id bigint, p_expected_product_id character varying, p_expected_source_location_id bigint, p_destination_location_id bigint)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE
    operation_is_valid boolean;
    current_kiz record;
BEGIN
    IF p_expected_source_location_id = p_destination_location_id THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source and destination must differ';
    END IF;

    SELECT true INTO operation_is_valid
    FROM wms.kiz_operation_items oi
    JOIN wms.kiz_operations o USING (operation_id)
    WHERE oi.operation_item_id = p_operation_item_id
      AND o.operation_type = 'transfer'
      AND o.result_payload IS NULL
      AND oi.movement_ref IS NULL;
    IF operation_is_valid IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'Недопустимый KIZ transfer protocol';
    END IF;

    SELECT kiz_id, product_id, location_id, lifecycle_status, closed_at
    INTO current_kiz
    FROM wms.kiz WHERE kiz_id = p_kiz_id FOR UPDATE;
    IF NOT FOUND
       OR current_kiz.lifecycle_status <> 'active'
       OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id <> p_expected_product_id
       OR current_kiz.location_id <> p_expected_source_location_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P7501', MESSAGE = 'КИЗ изменился до transfer';
    END IF;

    INSERT INTO wms.kiz_location_update_authorizations(
        transaction_id, operation_item_id, kiz_id, product_id,
        from_location_id, to_location_id
    ) VALUES (
        txid_current(), p_operation_item_id, p_kiz_id, p_expected_product_id,
        p_expected_source_location_id, p_destination_location_id
    );

    UPDATE wms.kiz SET location_id = p_destination_location_id WHERE kiz_id = p_kiz_id;
END;
$function$
```

### wms.transition_kiz_container_holder(bigint,bigint,character varying)

```sql
CREATE OR REPLACE FUNCTION wms.transition_kiz_container_holder(p_operation_item_id bigint, p_kiz_id bigint, p_direction character varying)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE target record; current_kiz record;
BEGIN
    SELECT oi.operation_item_id,oi.product_id,oi.batch_number,oi.container_id,
           o.operation_type,o.result_payload,c.location_id
    INTO target FROM wms.container_operation_items oi
    JOIN wms.container_operations o USING(operation_id)
    JOIN wms.containers c ON c.container_id=oi.container_id
    WHERE oi.operation_item_id=p_operation_item_id FOR UPDATE OF oi;
    IF NOT FOUND OR target.result_payload IS NOT NULL OR target.batch_number IS NOT NULL
       OR (p_direction='fill' AND target.operation_type<>'fill')
       OR (p_direction='extract' AND target.operation_type NOT IN ('extract','unpack_all')) THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='Недопустимый container KIZ transition protocol';
    END IF;
    SELECT kiz_id,product_id,location_id,container_id,lifecycle_status,closed_at
    INTO current_kiz FROM wms.kiz WHERE kiz_id=p_kiz_id FOR UPDATE;
    IF NOT FOUND OR current_kiz.lifecycle_status<>'active' OR current_kiz.closed_at IS NOT NULL
       OR current_kiz.product_id<>target.product_id
       OR (p_direction='fill' AND (current_kiz.location_id<>target.location_id OR current_kiz.container_id IS NOT NULL))
       OR (p_direction='extract' AND (current_kiz.location_id IS NOT NULL OR current_kiz.container_id<>target.container_id)) THEN
        RAISE EXCEPTION USING ERRCODE='P7501',MESSAGE='КИЗ holder изменился до container operation';
    END IF;
    INSERT INTO wms.kiz_container_holder_authorizations
      VALUES(txid_current(),p_operation_item_id,p_kiz_id,p_direction,target.product_id,target.location_id,target.container_id);
    IF p_direction='fill' THEN
      UPDATE wms.kiz SET location_id=NULL,container_id=target.container_id WHERE kiz_id=p_kiz_id;
    ELSE
      UPDATE wms.kiz SET location_id=target.location_id,container_id=NULL WHERE kiz_id=p_kiz_id;
    END IF;
END $function$
```

### wms.sync_container_to_inventory()

```sql
CREATE OR REPLACE FUNCTION wms.sync_container_to_inventory()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE
    location bigint;
    code varchar(100);
    fill_item_id bigint;
BEGIN
    IF NEW.status<>'active' THEN
        RETURN NEW;
    END IF;

    fill_item_id:=NULLIF(
        current_setting('wms.container_fill_item_id',true),''
    )::bigint;
    IF fill_item_id IS NOT NULL AND EXISTS (
        SELECT 1
        FROM wms.container_operation_items oi
        JOIN wms.container_operations o ON o.operation_id=oi.operation_id
        WHERE oi.operation_item_id=fill_item_id
          AND oi.container_id=NEW.container_id
          AND oi.product_id=NEW.product_id
          AND oi.batch_number IS NOT DISTINCT FROM NEW.batch_number
          AND oi.quantity=NEW.quantity
          AND o.operation_type='fill'
          AND o.result_payload IS NULL
    ) THEN
        RETURN NEW;
    END IF;

    SELECT c.location_id,c.qr_code INTO location,code
    FROM wms.containers c WHERE c.container_id=NEW.container_id;
    IF location IS NULL THEN
        RAISE EXCEPTION 'Container % has no location assigned',NEW.container_id;
    END IF;

    INSERT INTO wms.movements(
        movement_type,product_id,from_location_id,to_location_id,quantity,
        batch_number,container_code,reason
    ) VALUES (
        'receive',NEW.product_id,NULL,location,NEW.quantity,
        NEW.batch_number,code,'Container registered'
    );
    RETURN NEW;
END;
$function$
```

### wms.register_container(character varying,character varying,character varying,jsonb)

```sql
CREATE OR REPLACE FUNCTION wms.register_container(p_qr_code character varying, p_container_type character varying, p_location_code character varying, p_contents jsonb)
 RETURNS TABLE(container_id bigint, qr_code character varying, items_registered integer)
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_container_id bigint;
    v_location_id bigint;
BEGIN
    IF p_contents IS NULL
       OR jsonb_typeof(p_contents) <> 'array'
       OR jsonb_array_length(p_contents) <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Containers must be registered empty; use container fill operation';
    END IF;
    IF EXISTS (SELECT 1 FROM wms.containers c WHERE c.qr_code = p_qr_code) THEN
        RAISE EXCEPTION 'Container with QR code % already exists. Cannot register twice.',
            p_qr_code;
    END IF;
    SELECT l.location_id INTO v_location_id
    FROM wms.locations l WHERE l.location_code = p_location_code;
    IF v_location_id IS NULL THEN
        RAISE EXCEPTION 'Location % not found', p_location_code;
    END IF;

    INSERT INTO wms.containers(qr_code, container_type, location_id, status)
    VALUES (p_qr_code, p_container_type, v_location_id, 'empty')
    RETURNING wms.containers.container_id INTO v_container_id;

    RETURN QUERY SELECT v_container_id, p_qr_code, 0;
END;
$function$
```

### wms.apply_container_extract_content(bigint)

```sql
CREATE OR REPLACE FUNCTION wms.apply_container_extract_content(p_operation_item_id bigint)
 RETURNS numeric
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE
    target record;
    current_quantity numeric(10,2);
    contained_quantity numeric(10,2);
    remaining_quantity numeric(10,2);
BEGIN
    SELECT oi.container_id,oi.product_id,oi.batch_number,oi.quantity,
           c.location_id,c.qr_code
    INTO target
    FROM wms.container_operation_items oi
    JOIN wms.container_operations o ON o.operation_id=oi.operation_id
    JOIN wms.containers c ON c.container_id=oi.container_id
    WHERE oi.operation_item_id=p_operation_item_id
      AND o.operation_type IN ('extract','unpack_all')
      AND o.result_payload IS NULL
      AND oi.movement_ref IS NULL
      AND oi.outgoing_movement_ref IS NOT NULL
      AND oi.incoming_movement_ref IS NOT NULL
    FOR UPDATE OF oi;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='55000',
            MESSAGE='Container extract item is not ready for contents mutation';
    END IF;
    SELECT cc.quantity INTO current_quantity
    FROM wms.container_contents cc
    WHERE cc.container_id=target.container_id
      AND cc.product_id=target.product_id
      AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
      AND cc.status='active'
    FOR UPDATE;
    IF current_quantity IS NULL OR current_quantity<target.quantity THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container extract content scope changed or is insufficient';
    END IF;
    SELECT COALESCE(i.quantity,0) INTO contained_quantity
    FROM (SELECT 1) marker
    LEFT JOIN wms.inventory i
      ON i.product_id=target.product_id
     AND i.location_id=target.location_id
     AND i.status='available'
     AND i.batch_number IS NOT DISTINCT FROM target.batch_number
     AND i.container_code=target.qr_code;
    IF current_quantity-contained_quantity IS DISTINCT FROM target.quantity THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container extract movements/content delta is invalid';
    END IF;
    remaining_quantity:=current_quantity-target.quantity;
    IF remaining_quantity=0 THEN
        DELETE FROM wms.container_contents cc
        WHERE cc.container_id=target.container_id
          AND cc.product_id=target.product_id
          AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
          AND cc.status='active';
    ELSE
        UPDATE wms.container_contents cc
        SET quantity=remaining_quantity,updated_at=now()
        WHERE cc.container_id=target.container_id
          AND cc.product_id=target.product_id
          AND cc.batch_number IS NOT DISTINCT FROM target.batch_number
          AND cc.status='active';
    END IF;
    RETURN remaining_quantity;
END;
$function$
```

### wms.require_complete_container_operation()

```sql
CREATE OR REPLACE FUNCTION wms.require_complete_container_operation()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'wms'
AS $function$
DECLARE
    target_operation_id bigint;
BEGIN
    target_operation_id:=COALESCE(NEW.operation_id,OLD.operation_id);
    IF EXISTS (
        SELECT 1
        FROM wms.container_operations o
        JOIN wms.containers c ON c.container_id=o.container_id
        WHERE o.operation_id=target_operation_id
          AND (
              o.result_payload IS NULL
              OR (o.result_payload->>'operation_id')::bigint IS DISTINCT FROM o.operation_id
              OR (o.result_payload->>'container_id')::bigint IS DISTINCT FROM o.container_id
              OR EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id AND oi.container_id<>o.container_id
              )
              OR (o.operation_type IN ('fill','extract','unpack_all') AND NOT EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
              ))
              OR EXISTS (
                  SELECT 1 FROM wms.container_operation_items oi
                  WHERE oi.operation_id=o.operation_id
                    AND (
                        (o.operation_type='move' AND (
                            oi.movement_ref IS NULL OR oi.outgoing_movement_ref IS NOT NULL
                            OR oi.incoming_movement_ref IS NOT NULL
                        ))
                        OR (o.operation_type<>'move' AND (
                            oi.movement_ref IS NOT NULL OR oi.outgoing_movement_ref IS NULL
                            OR oi.incoming_movement_ref IS NULL
                        ))
                    )
              )
              OR EXISTS (
                  SELECT 1
                  FROM wms.container_operation_items oi
                  LEFT JOIN wms.movement_registry single_registry
                    ON single_registry.movement_ref=oi.movement_ref
                  LEFT JOIN wms.movements single_movement
                    ON single_movement.movement_id=single_registry.movement_id
                   AND single_movement.created_at=single_registry.movement_created_at
                  LEFT JOIN wms.movement_registry outgoing_registry
                    ON outgoing_registry.movement_ref=oi.outgoing_movement_ref
                  LEFT JOIN wms.movements outgoing
                    ON outgoing.movement_id=outgoing_registry.movement_id
                   AND outgoing.created_at=outgoing_registry.movement_created_at
                  LEFT JOIN wms.movement_registry incoming_registry
                    ON incoming_registry.movement_ref=oi.incoming_movement_ref
                  LEFT JOIN wms.movements incoming
                    ON incoming.movement_id=incoming_registry.movement_id
                   AND incoming.created_at=incoming_registry.movement_created_at
                  WHERE oi.operation_id=o.operation_id
                    AND (
                        (o.operation_type='move' AND (
                            single_movement.movement_type IS DISTINCT FROM 'transfer'
                            OR single_movement.product_id IS DISTINCT FROM oi.product_id
                            OR single_movement.batch_number IS DISTINCT FROM oi.batch_number
                            OR single_movement.quantity IS DISTINCT FROM oi.quantity
                            OR single_movement.container_code IS DISTINCT FROM c.qr_code
                            OR single_movement.source_type IS DISTINCT FROM 'container_operation'
                            OR single_movement.source_id IS DISTINCT FROM oi.operation_id
                            OR single_movement.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR single_movement.from_location_id IS DISTINCT FROM (
                                SELECT location_id FROM wms.locations
                                WHERE location_code=o.result_payload->>'from_location_code'
                            )
                            OR single_movement.to_location_id IS DISTINCT FROM c.location_id
                        ))
                        OR (o.operation_type<>'move' AND (
                            outgoing.movement_type IS DISTINCT FROM 'transfer'
                            OR outgoing.product_id IS DISTINCT FROM oi.product_id
                            OR outgoing.batch_number IS DISTINCT FROM oi.batch_number
                            OR outgoing.quantity IS DISTINCT FROM oi.quantity
                            OR outgoing.source_type IS DISTINCT FROM 'container_operation'
                            OR outgoing.source_id IS DISTINCT FROM oi.operation_id
                            OR outgoing.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR incoming.movement_type IS DISTINCT FROM 'transfer'
                            OR incoming.product_id IS DISTINCT FROM oi.product_id
                            OR incoming.batch_number IS DISTINCT FROM oi.batch_number
                            OR incoming.quantity IS DISTINCT FROM oi.quantity
                            OR incoming.source_type IS DISTINCT FROM 'container_operation'
                            OR incoming.source_id IS DISTINCT FROM oi.operation_id
                            OR incoming.source_item_id IS DISTINCT FROM oi.operation_item_id
                            OR (o.operation_type='fill' AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS NOT NULL
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS DISTINCT FROM c.qr_code
                            ))
                            OR (o.operation_type IN ('extract','unpack_all') AND (
                                outgoing.from_location_id IS DISTINCT FROM c.location_id
                                OR outgoing.to_location_id IS NOT NULL
                                OR outgoing.container_code IS DISTINCT FROM c.qr_code
                                OR incoming.from_location_id IS NOT NULL
                                OR incoming.to_location_id IS DISTINCT FROM c.location_id
                                OR incoming.container_code IS NOT NULL
                            ))
                        ))
                    )
              )
              OR (o.operation_type='move' AND (
                  c.location_id IS DISTINCT FROM (
                      SELECT location_id FROM wms.locations
                      WHERE location_code=o.result_payload->>'to_location_code'
                  )
                  OR c.status IS DISTINCT FROM o.result_payload->>'container_status'
              ))
              OR (o.operation_type='unpack_all' AND (
                  c.status<>'empty'
                  OR EXISTS (SELECT 1 FROM wms.container_contents cc
                             WHERE cc.container_id=c.container_id AND cc.status='active')
                  OR EXISTS (SELECT 1 FROM wms.inventory i
                             WHERE i.container_code=c.qr_code)
              ))
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Container operation graph must be complete at commit';
    END IF;
    RETURN NULL;
END;
$function$
```

### wms.generate_location_path()

```sql
CREATE OR REPLACE FUNCTION wms.generate_location_path()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    parent_path wms.ltree;
BEGIN
    IF NEW.parent_location_id IS NULL THEN
        NEW.path := NEW.location_id::TEXT::wms.ltree;
    ELSE
        SELECT path INTO parent_path
        FROM wms.locations
        WHERE location_id = NEW.parent_location_id;

        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'Parent location % not found', NEW.parent_location_id;
        END IF;

        -- явно добавляем точку
        NEW.path := (parent_path::TEXT || '.' || NEW.location_id::TEXT)::wms.ltree;
    END IF;

    RETURN NEW;
END;
$function$
```

### wms.get_child_locations(bigint)

```sql
CREATE OR REPLACE FUNCTION wms.get_child_locations(p_location_id bigint)
 RETURNS TABLE(location_id bigint, location_code character varying, level integer, path wms.ltree)
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_path wms.ltree;
BEGIN
    -- Получаем путь родительской локации
    SELECT l.path INTO v_path
    FROM wms.locations l
    WHERE l.location_id = p_location_id;

    IF v_path IS NULL THEN
        RAISE EXCEPTION 'Location % not found', p_location_id;
    END IF;

    -- Возвращаем все дочерние локации через LTREE
    RETURN QUERY
    SELECT l.location_id, l.location_code, l.level, l.path
    FROM wms.locations l
    WHERE l.path <@ v_path  -- Оператор LTREE: является потомком
      AND l.location_id != p_location_id  -- Исключаем саму локацию
    ORDER BY l.path;
END;
$function$
```
