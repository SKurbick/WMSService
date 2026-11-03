# Независимый Code Review Inventory Stage 2A Physical Apply

Дата: 30.10.2026. Ветка `feature/inventory-stage1`, HEAD `8aa94e40e868f41084537fd68016dc988cd59a26`. Проверено фактическое рабочее дерево, включая незакоммиченные и untracked файлы реализации. Это не review только commit HEAD.

## A. Вердикт и границы проверки

**Не готов к staging с включённым physical apply до устранения B.1 и проверки deployment permissions из B.2.** Развёртывание в изолированном staging для воспроизведения и исправления замечаний допустимо; это не разрешение производственного пилота.

Подтверждены **P0: 0, P1: 1, P2: 1, P3: 0**. B.1 относится к небезопасной адаптации установленного тела общей функции, B.2 — к совместимости миграции с не-owner writers. Это не утверждение, что обычный `adjust` неисправен или текущая production-функция содержит обнаруженный вариант комментария.

На штатной тестовой схеме подтверждены реальное изменение количества, атомарность нескольких строк, откат при ошибках и commit failure, повторы и основные freeze/FBS гонки. Отдельный эксперимент показал, что миграция может успешно установить неработающую строгую source-ветку; штатные тесты этот случай не покрывают.

**Источники доказательств:**

- Текущий код, четыре миграции и тесты; ссылки ниже указывают на файлы рабочего дерева и номера строк на дату review.
- Независимые проверки в disposable PostgreSQL **17.4**, Docker `wms-inventory-stage2a-review`, опубликованный только на `127.0.0.1:55451`. Рабочие приложения и RabbitMQ не запускались.
- Схема тестов собиралась существующим fixture из архивного DDL и актуальных миграций: [tests/conftest.py:16](../../tests/conftest.py#L16), [tests/conftest.py:158](../../tests/conftest.py#L158). Это **не повторная проверка действующей production-схемы**.
- Production не подключалась, её данные, grants, flags, triggers и migration state в этом review не менялись и заново не проверялись. Runtime-аудит от 19 октября — исторический контекст, не доказательство состояния на 30 октября.

Код, миграции и тесты не исправлялись. Единственное изменение репозитория в рамках review — этот отчёт. Диагностические скрипты и логи создавались в `/tmp`.

## B. Подтверждённые дефекты

### B.1. P1 — успешная миграция может оставить строгую inventory-source ветку внутри комментария

**Место:** [20261030_add_inventory_physical_apply.sql:135](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L135), особенно строки 141–145. Зависимые проверки физического эффекта находятся в [том же файле:98](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L98), а deferred completeness проверяет связи и величину movement, не итоговую projection: [строка 207](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L207).

**Причина.** `strpos(original,'BEGIN')` ищет первое текстовое вхождение, без различения PL/pgSQL-кода, комментариев и строк. Найденное слово считается началом исполняемого блока. Наличие имени helper в сохранённом body также не доказывает, что вызов исполняется.

**Воспроизведение — только в изолированной БД:**

1. Установить исходную схему и первые три inventory migrations.
2. Сохранить прежнее тело `update_inventory_from_movement()`, добавив перед ним валидный комментарий `/* BEGIN installed function documentation */`. Логика legacy функции при этом не меняется.
3. Применить четвёртую миграцию. Она **успешно коммитится**, а новая `IF NEW.source_type = 'inventory_document' ...` оказывается внутри этого комментария.
4. Подготовить через реальный HTTP workflow partial-документ с исходным количеством 10 и финальным фактом 8, довести до ready.
5. Установить в тестовой БД fault-injection BEFORE INSERT trigger, удаляющий physical row перед расходным inventory adjustment. Такой missing-row fault уже является частью штатной отрицательной матрицы: [test_inventory_physical_apply_postgres.py:210](../../tests/test_inventory_physical_apply_postgres.py#L210).
6. Выполнить HTTP apply. Получено **HTTP 200, документ `applied`, физический остаток 0 при target 8**. Срабатывает legacy fallback с известным отсутствием исключения для missing-row `adjust`; application/registry checks этого не обнаруживают.

Фактический вывод независимого скрипта:

```text
COMMENT migration committed, branch located inside block comment: True
COMMENT missing-row fault API: 200 doc_status: applied physical_quantity: 0 target:8
```

**Что доказано и что не утверждается.** Доказан пропуск строгого SQL-контракта при допустимом варианте установленного body. Для демонстрации неверного physical result использован явно указанный дополнительный fault trigger, а не естественная конкурентная гонка штатного pilot. На неизменённом тестовом body этот fault штатно отклоняется. Наличие такого комментария или fault trigger в production **не проверено и не предполагается**. Однако миграция заявляет адаптацию фактической установленной функции и обязана отклонять неподдержанный body, а не принимать его с отключённой защитой.

**Последствия.** После успешного deployment отсутствует гарантия source-specific exact effect, хотя таблицы application и endpoint доступны. Нельзя принимать тесты стандартного архивного body за доказательство корректности произвольной установленной функции.

**Минимальное безопасное исправление.** Заменить поиск первого текстового `BEGIN` проверяемой адаптацией поддержанного установленного определения; при неизвестном body завершать миграцию ошибкой. Допустим узкий контракт/проверенный fingerprint с явным preflight вместо универсального переписывания PL/pgSQL. Проверка после установки должна подтверждать исполняемую строгую ветку, а не только наличие подстроки. Legacy fallback необходимо сохранить.

**Тест исправления.** Миграционный integration test с ведущим block comment, содержащим `BEGIN`, и вариантом `BEGIN` внутри декларации/строки. Результат — корректная установка исполняемого guard либо полный rollback миграции. На поддержанном результате повторить missing-row, insufficient-row и after-projection fault tests: ни один не должен заканчиваться `applied` с неверным количеством. Дополнительно проверить обычный `adjust` без inventory source.

### B.2. P2 — новые invoker guards ломают прежние grants даже без активных freeze

**Место:** создание fences [20261030_add_inventory_location_freeze.sql:22](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L22); invoker-функция и locking read [строка 83](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L83); безусловная установка guards на старые таблицы [строка 199](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L199); создание fence при добавлении адреса [строка 207](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L207). В миграции нет выдачи прав или fail-fast проверки прав фактических runtime roles. Runbook упоминает проверку permissions, но не задаёт требуемый контракт: [inventory_stage2a_physical_apply.md:201](../flows/inventory_stage2a_physical_apply.md#L201).

**Воспроизведение.** В отдельной БД создать не-owner роль writer, выдать права на прежние WMS/public tables и sequences до установки freeze migration. Обычный `receive` проходит. Применить новые миграции owner-ролью без default privileges для writer, оставить все inventory flags выключенными, freeze не создавать. Тот же `receive` той же ролью падает:

```text
PERMISSIONS legacy receive BEFORE freeze migration: PASS
PERMISSIONS same receive AFTER migrations, no freezes, flags=false:
InsufficientPrivilegeError permission denied for table inventory_location_fences
```

**Последствия.** DB-first deployment может остановить существующие receive/transfer/adjust/FBS и другие writers до включения модуля. `FOR SHARE` требует также соответствующего UPDATE privilege, одного SELECT недостаточно. Location INSERT дополнительно нуждается в INSERT на fences. Для invalidating FBS понадобятся права на freeze header/events и identity sequence, а не только на прежние shipment tables.

**Ограничение вывода.** Результат подтверждён для описанного нормального набора legacy grants. Runtime-аудит 19 октября использовал superuser `vector_admin`; для такой роли этот конкретный permission failure не возникает. Текущие роли всех deployed workers и default privileges **не проверены**. Не утверждается, что production уже остановилась.

**Минимальное безопасное исправление.** До включения новых guards определить роли и явный минимальный grant contract, оформить deployment grant/preflight для каждой роли, включая workers и sequences. Не решать это требованием superuser или широким `SECURITY DEFINER` без отдельного security analysis. Проверка permissions должна происходить до изменения поведения старых таблиц, а не после первого production failure.

**Тест исправления.** Под не-owner ролью выполнить старые receive/transfer/adjust/ship, location INSERT, standard/task-level/multi-source FBS и retry: сначала без freeze и с flags=false, затем с активным freeze и разрешённым FBS invalidation. Ожидать корректные операции/409, без `42501`. Отдельно проверить новые inventory commands и sequence privileges. Нужен также старый binary с новой БД без freeze.

## C. Реальный SQL write path и целостность остатков

Ниже — подтверждённое поведение **при правильно установленной строгой ветке**. B.1 ограничивает применимость этого заключения к результату миграции.

1. Endpoint `POST /api/inventory-documents/{id}/apply` использует существующий document service и `ApplicationCommand`: [inventory_documents.py:257](../../app/api/v1/endpoints/inventory_documents.py#L257). Требуются MODULE/APPLY/FREEZE flags: [inventory_application_service.py:267](../../app/core/services/inventory_application_service.py#L267), defaults false: [config.py:55](../../app/shared/config.py#L55).
2. Командный протокол открывает явную транзакцию, берёт UUID advisory lock, проверяет сохранённый fingerprint/result, блокирует header и проверяет expected version: [inventory_document_service.py:169](../../app/core/services/inventory_document_service.py#L169). Apply выбирает READ COMMITTED: [inventory_application_service.py:386](../../app/core/services/inventory_application_service.py#L386). Возврат из transaction context выполняется с commit до возврата HTTP результата; deferred error не превращается в HTTP 200.
3. Повторно проверяются pending/submitted/current revision, ready freeze, epoch, preparation, final attempts и свежая evidence: [inventory_application_service.py:180](../../app/core/services/inventory_application_service.py#L180), [inventory_document_service.py:829](../../app/core/services/inventory_document_service.py#L829). Историческое `can_apply=true` не достаточно.
4. Создаётся application с UNIQUE document/preparation и текущим top-level transaction identity: [physical migration:17](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L17). Components остаются отдельными техническими сущностями; исторический inventory_id не становится FK на изменяемый остаток.
5. SQL INSERT создаёт только `adjust`, строго односторонний from/to, quantity=`abs(delta)`, NULL batch/container, source=`inventory_document`, source_id=document, source_item_id=component: [inventory_applications.py:29](../../app/infrastructure/database/queries/inventory_applications.py#L29). Числа Decimal, без float. Прямого UPDATE inventory в application service нет.
6. BEFORE movement freeze guard пропускает inventory source только при matching application текущей транзакции и freeze. Это не один предварительный SELECT: fences удерживаются до commit. Registry identity создаёт существующий механизм; новый registry не вводится.
7. AFTER projection function направляет inventory source в strict helper. Helper проверяет document/revision/epoch/latest count/provenance, единственность компонента, exact key и отсутствие КИЗ/партий: [physical migration:48](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L48).
8. Для недостачи exact row блокируется FOR UPDATE NOWAIT, сверяются inventory_id и previous quantity с подготовкой, проверяется достаточность, UPDATE выполняется по PK с RETURNING; missing row отклоняется. Для излишка INSERT ON CONFLICT изменяет exact NULL/NULL row либо создаёт её. Нулевой результат удаляет только собственный exact row. Проверяются RETURNING, итоговая сумма и число physical rows: [physical migration:98](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L98).
9. NULL/empty collision не объединяется: она отклоняется до корректировки. Non-NULL batch/container, даже одна партия без утверждённого распределения, также отклоняется. Общая legacy COALESCE-семантика остаётся в fallback и не переопределяется для обычного `adjust`.
10. Registry link, application `applied`, document `applied`, release fences/claims, event/history/UUID-result записываются в той же транзакции: [inventory_application_service.py:326](../../app/core/services/inventory_application_service.py#L326). Deferred checks требуют завершённую application, ожидаемое число links и matching movements: [physical migration:207](../../scripts/migrations/20261030_add_inventory_physical_apply.sql#L207).

**Атомарность доказана штатными integration tests:** ошибка последнего movement, disappearance/insufficiency row, изменение projection AFTER trigger и deferred exception откатывают все физические изменения. Application и links не остаются, document/freeze остаются в прежнем ready состоянии, тот же UUID после устранения fault может быть повторён: [test_inventory_physical_apply_postgres.py:210](../../tests/test_inventory_physical_apply_postgres.py#L210). Отдельный `command_rejected` audit после rollback является намеренной записью отказа, не частичным успешным apply: [inventory_document_service.py:269](../../app/core/services/inventory_document_service.py#L269).

**Идемпотентность:** UUID+hash возвращает исходный committed result до проверки version; другой payload с тем же UUID даёт конфликт. Дополнительная защита — UNIQUE application(document_id), preparation и component/ref links. Конкурентная команда с другим UUID и старой version может получить 409, что соответствует optimistic concurrency. После перечитывания applied header повтор с тем же submitted revision возвращает сохранённый результат. Сетевой timeout не означает rollback: клиент должен повторить исходные UUID/body либо получить `/application`, а не создавать новую корректировку.

## D. Freeze, lock order и конкурентность

### D.1. Барьер writers

Постоянная fence row существует для каждого адреса. Обычный writer в row trigger берёт `FOR SHARE NOWAIT`; freeze берёт `FOR UPDATE NOWAIT` в порядке location_id. Эти блокировки несовместимы и держатся до конца соответствующих транзакций. Поэтому writer либо прошёл первым и freeze получает конфликт, либо freeze установлена первой и writer отклоняется. Пустая inventory row не нужна для защиты адреса: [freeze migration:64](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L64), [inventory_freeze.py:3](../../app/infrastructure/database/queries/inventory_freeze.py#L3).

В старой REPEATABLE READ транзакции locking read изменённого fence не позволяет записать, используя старый свободный snapshot: PostgreSQL выдаёт serialization failure. Это проверено integration test: [test_inventory_freeze_postgres.py:315](../../tests/test_inventory_freeze_postgres.py#L315).

Freeze состояния и epoch хранятся в БД, SQL-транзакция **не держится между HTTP begin/confirm/count/ready/apply**. Confirm фиксирует author/text/time, увеличивает epoch, отменяет открытые попытки и требует новый count всех строк: [inventory_freeze_service.py:117](../../app/core/services/inventory_freeze_service.py#L117). Begin/complete count сохраняют token и start/finish evidence: [inventory_document_service.py:571](../../app/core/services/inventory_document_service.py#L571). Старый завершённый count не становится финальным автоматически.

### D.2. Порядок apply

Фактический порядок: command UUID → document FOR UPDATE → movements ROW EXCLUSIVE NOWAIT → sorted fences FOR UPDATE NOWAIT → freeze header FOR UPDATE NOWAIT → sorted products FOR UPDATE NOWAIT → locations FOR KEY SHARE NOWAIT → inventory rows FOR UPDATE NOWAIT → task admission exclusive try-lock → повторная evidence → adjustments/links/status/release. Источник: [inventory_application_service.py:288](../../app/core/services/inventory_application_service.py#L288), [inventory_freeze_service.py:15](../../app/core/services/inventory_freeze_service.py#L15).

ROW EXCLUSIVE не запрещает обычные INSERT movements; он конфликтует с existing maintenance SHARE. Products FOR UPDATE могут кратковременно задерживать FK-проверки writers того же SKU даже в других адресах. Это не долгоживущий warehouse freeze, но полностью «нулевого влияния вне scope» код не гарантирует. Максимальный размер pilot ограничен 20 адресами, а не числом SKU; длительность транзакции на адресе с очень большим количеством SKU ещё нужно измерить.

NOWAIT на physical rows важен из-за старых writers, которые уже держат inventory/KИЗ/locations до вызова guard. Если ждать их, удерживая fences, возможен обратный lock order. Здесь apply безопасно отклоняется вместо ожидания. Это не математическое доказательство отсутствия всех deadlocks: document/UUID/FK locks, глобальный task admission latch и внутренние старые writers остаются в системе. Команды retry serialization/deadlock до трёх попыток; lock conflict возвращает 409. Statement timeout 15 s и lock timeout 5 s — ограничения отдельных SQL, не общей длительности HTTP workflow: [inventory_document_service.py:180](../../app/core/services/inventory_document_service.py#L180).

Task metadata получает shared admission latch; confirm/ready/apply берут exclusive try-lock. Неопределённая активная task scope запрещает pilot, а не останавливает весь WMS. Прямая запись inventory/movements не подменяет этот task latch. Установленные tasks/parent/items guards и recursive scope проверены: [freeze migration:129](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L129), [inventory_freeze.py:23](../../app/infrastructure/database/queries/inventory_freeze.py#L23).

Maintenance сохраняет старый порядок SHARE movements → EXCLUSIVE inventory. Если его изменения затрагивают frozen address, inventory trigger откатывает maintenance целиком; запуск вне scope может работать. Сам maintenance способен брать широкие locks, поэтому на время pilot его расписание нужно контролировать операционно: [system_repository.py:58](../../app/infrastructure/database/repositories/system_repository.py#L58). Прямое административное DDL, отключение triggers и прямой TRUNCATE отдельной partition не считаются поддерживаемыми warehouse writers; parent TRUNCATE защищён отдельным statement guard.

### D.3. Освобождение и восстановление

`freeze/release` доступен и при FREEZE_ENABLED=false, пока включён модуль. Он сохраняет reason/event, отменяет open attempts, помечает строки для пересчёта и освобождает fences, **но не claims**. Close освобождает claims после явного release: [inventory_freeze_service.py:171](../../app/core/services/inventory_freeze_service.py#L171), [inventory_document_service.py:810](../../app/core/services/inventory_document_service.py#L810).

TTL нет. Зависший freeze требует просмотра status/conflicts и явного release с актуальной version/UUID либо урегулирования и нового epoch. Это соответствует заданию и сохраняет историю. Отключение flags не снимает уже существующую блокировку. До rollback binary нельзя выключать весь модуль и терять доступ к release.

## E. ФБС-исключение

### E.1. Что подтверждено

Standard и external consumer вызывают один `handle_write_off_fbs`; HTTP/manual paths также используют его, retry worker — тот же product-group processor: [consumer.py:32](../../app/consumer.py#L32), [consumer.py:94](../../app/consumer.py#L94), [fbs_shipments.py:486](../../app/api/v1/endpoints/fbs_shipments.py#L486), [retry_worker.py:86](../../app/retry_worker.py#L86). Проверка не ограничивалась одним HTTP endpoint.

После блокировки реальных shipment items repository устанавливает transaction-local shipment/item GUC: [fbs_shipment_repository.py:478](../../app/infrastructure/database/repositories/fbs_shipment_repository.py#L478). Поэтому старый NULL `source_type` поддерживается; публичные payloads и значения source не переписываются. Multi-source путь дополнительно берёт все source fences в порядке location_id и headers в порядке freeze_id до stock scopes: [fbs_kiz_repository.py:180](../../app/infrastructure/database/repositories/fbs_kiz_repository.py#L180), [freeze migration:111](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L111).

Guard разрешает исключение не любому `ship`, а контексту реальных items. На frozen адресе FBS получает fence, увеличивает generation, переводит freeze в invalidated и пишет event **в той же транзакции**. Deferred proof требует ship witness с exact movement coordinates, совпадающими freeze/location/product/shipment/top-level transaction, и success item либо written_off task result: [freeze migration:83](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L83), [freeze migration:226](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L226).

Один GUC без success witness не даёт committed обход. Это протокол доверенных серверных writers с правами БД, а не защита от злонамеренного SQL-клиента с правом изменять все связанные таблицы. Nested savepoints учитываются top-level transaction_id, а не xmin.

Независимо прошли тесты: real legacy FBS при freeze; real multi-source active-КИЗ + retry + duplicate; fake context rollback; две FBS транзакции при гонке free→frozen; разные SKU в свободном адресе; несколько source addresses с общими headers. Свободные адреса используют совместимый SHARE; savepoint снимает спекулятивный SHARE перед переходом к UPDATE, что предотвращает проверенный upgrade deadlock: [test_inventory_freeze_postgres.py:248](../../tests/test_inventory_freeze_postgres.py#L248), [строка 506](../../tests/test_inventory_freeze_postgres.py#L506), [строка 704](../../tests/test_inventory_freeze_postgres.py#L704), [строка 774](../../tests/test_inventory_freeze_postgres.py#L774).

### E.2. Одновременные ФБС и apply: точная гарантия

| Кто первым удерживает fence | Результат |
|---|---|
| FBS | Apply не может проскочить незакоммиченное списание: получает конфликт; после commit документ invalidated, прежний final count неприменим |
| Apply | FBS ждёт короткую apply-транзакцию, затем пишет после release. Apply может успешно завершиться; следующая FBS запись не инвалидирует уже applied документ |

Обе очередности прямо проверены: [test_inventory_physical_apply_postgres.py:424](../../tests/test_inventory_physical_apply_postgres.py#L424), [строка 475](../../tests/test_inventory_physical_apply_postgres.py#L475). Второй тест ожидает successful apply к 8, затем FBS -1 и остаток 7.

Это **сериализация DB effects**, а не гарантия «любой overlap HTTP-запросов FBS/apply всегда отменяет apply». Если физическое изъятие произошло до count, но запись ФБС поставлена в очередь позже, locks не восстанавливают физический порядок. Подтверждение сотрудника также не является техническим доказательством этого порядка. Такой источник нельзя допускать в pilot на основании одних этих тестов.

В разрешённой модели этот риск ограничен исключением configured FBS root/descendants и любых active КИЗ в адресе: [inventory_application_service.py:48](../../app/core/services/inventory_application_service.py#L48). Но исключение зависит от согласованной конфигурации всех deployed процессов и фактического физического процесса. В коде нет общей DB allowlist всех исторических/внешних FBS источников. До пилота необходимо доказать, что выбранные обычные адреса не являются альтернативными delayed FBS sources, в том числе у workers другой версии/конфигурации. Нарушение этой предпосылки нельзя исправить интерпретацией created_at как физического времени.

### E.3. Mixed-version deployment

**Обновить все FBS workers до первого freeze необходимо.** Старый binary не устанавливает transaction-local context. При активном fence его обычный NULL-source ship отклоняется guard как обычный writer. Выключенный apply этого не исправляет; freeze продолжает действовать. Без active freezes старое приложение может работать с новой схемой, если выполнен grant contract.

Runbook правильно запрещает mixed-old workers с active freeze: [inventory_stage2a_physical_apply.md:201](../flows/inventory_stage2a_physical_apply.md#L201). Однако capability/helper не является автоматическим согласованием версий всего deployment. Нужно проверить main/external consumers, HTTP replicas, ручные retries и scheduler/retry workers, а также старые живые транзакции до включения freeze.

## F. Матрица совместимости

| Старый компонент / writer | Новая DB boundary | Без active freeze | С active freeze | Проверка / ограничение |
|---|---|---|---|---|
| receive, transfer, putaway/pick, manual adjust, обычный ship | movement BEFORE guard, оба конца; затем inventory guard | Старый projection fallback; новые fence locks и grants обязательны | Запись затронутого адреса отклоняется до physical effect | Обе стороны transfer, missing-row adjust, unrelated location tests; B.2 |
| Kit/re-sort | Те же guards на создаваемых movements/inventory | Старые типы и service не меняются | Весь transaction откатывается | Real kit/resort HTTP: [freeze tests:388](../../tests/test_inventory_freeze_postgres.py#L388) |
| Standard/external/HTTP FBS, manual retry | Item context → ship/inventory guards → deferred effect proof | Старые source/payloads; context работает и на старой DB | Ship разрешён, freeze invalidated атомарно | Shared pipeline code + real legacy HTTP; живой broker staging ещё нужен |
| Task-level и multi-source КИЗ FBS/retry worker | Context + canonical source fences/headers, KИЗ/inventory guards | Capability check сохраняет old-DB режим | Списание продолжается; invalidation и task result коммитятся вместе | Real multi-source retry/duplicate integration test |
| Обычный КИЗ lifecycle / transfer / ship | BEFORE kiz old/new holder + inventory/movement fences | Сохраняются прежние lifecycle/guards; добавлен NOWAIT fence check | Запрещён, если это не доказанный FBS effect | [freeze tests:155](../../tests/test_inventory_freeze_postgres.py#L155), [486](../../tests/test_inventory_freeze_postgres.py#L486); legacy deadlocks не устранены |
| Controlled fill/extract/move/unpack | containers, container_contents, inventory, kiz, movements | Общие container functions не заменены | Атомарный отказ на frozen source/destination | Controlled fill + generic direct guards; все функции под freeze отдельно на staging |
| Tasks/task_items | Shared task admission + own/ancestor locations | Добавляется краткий metadata latch | Запрещённые изменения не проходят, safe pending/assigned cancel разрешена | [freeze tests:427](../../tests/test_inventory_freeze_postgres.py#L427) |
| Locations | old/new location; structural ancestor descendants; AFTER INSERT fence | Требуются новые fence grants | Изменение охваченного дерева отклоняется | [freeze tests:595](../../tests/test_inventory_freeze_postgres.py#L595), [625](../../tests/test_inventory_freeze_postgres.py#L625) |
| Maintenance/recalculate | Existing ledger/projection locks + inventory guard | Старый algorithm, `adjust` учитывается существующим ledger | При затрагивании freeze весь maintenance rollback | [freeze tests:347](../../tests/test_inventory_freeze_postgres.py#L347), [physical tests:645](../../tests/test_inventory_physical_apply_postgres.py#L645) |
| История movements/ledger/integrity/read models | Тип остаётся adjust; новые source и registry links | Старые DTO/types не менялись | Read не блокируется долговечным freeze | Existing history/integrity test; внешние ETL/source allowlists в repo не доказаны |
| Старый backend на новой DB | Новые invoker triggers уже активны | Условно совместим: grants, поддержанный body, без freeze | Старый FBS exception отсутствует — такой rollout запрещён | B.1/B.2; нельзя обещать совместимость только на основании false flags |

Inventory source — новое зарезервированное происхождение, но movement_type CHECK/Enum не расширяется. `wms.update_inventory_from_movement()` меняется, поэтому физически это не чисто additive deployment. `inventory_application_can_write()` и module lifecycle function также заменяются; КИЗ lifecycle functions и controlled container DB functions миграциями не заменяются. Freeze triggers устанавливаются безусловно на старые таблицы и работают независимо от flags.

## G. Миграции, partitions и безопасное развёртывание

### G.1. Последовательность и влияние

| Миграция | Объекты и влияние |
|---|---|
| `20261022_add_inventory_documents.sql` | Документы/scope/claims/snapshot/count/revision/history/commands; старые physical таблицы не меняются |
| `20261029_add_inventory_application_preparations.sql` | Preparations/components и guards; исходно can_apply=false; UNIQUE document/revision |
| `20261030_add_inventory_location_freeze.sql` | Три таблицы, backfill fence на каждый адрес, новые triggers старых tables/partitions, lifecycle count cancelled; начинает влиять на все writers уже при deployment |
| `20261030_add_inventory_physical_apply.sql` | Расширение preparations/components, две application/link таблицы, strict helper и модификация общей projection function; applied lifecycle и deferred constraints |

Все четыре транзакционные; latest migrations используют lock_timeout 5 s, statement_timeout 30 s. CREATE/ALTER не являются idempotent повторным применением; физическая миграция дополнительно отвергает повторную установку branch. Нужен проверенный migration ledger и полный rollback при неуспехе, а не продолжение с середины SQL-файла. Создание triggers на partitioned parent и FK/ALTER требует relation locks: его нельзя объявлять online zero-impact только из-за небольших новых таблиц.

Movement guards и deferred source-link trigger установлены на partitioned parent. PostgreSQL клонирует row triggers на существующие/будущие partitions; generic dispatch передаёт parent family через TG_ARGV, а не ошибочно сравнивает имя leaf: [freeze migration:141](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L141), [строка 199](../../scripts/migrations/20261030_add_inventory_location_freeze.sql#L199). Штатные tests проверяют реальный INSERT через partitions. Создание/attach новой будущей partition и административный retention в этом review отдельно не выполнялись: до rollout проверить клоны и состояние enabled на каждой leaf, в том числе direct-leaf INSERT, и провести attach/create rehearsal на staging. Наличие будущих production partitions не устанавливалось.

### G.2. Рекомендуемый порядок

1. Снять определения/owners/grants/triggers/partitions на staging-копии актуальной production schema. Сверить runtime body, dependency objects, movement registry coordinates и схему FBS/task-level. Исправить B.1 до успешного physical deployment.
2. Определить реальных runtime roles, default privileges, sequences; закрыть B.2 и выполнить legacy writer smoke под каждой ролью. Не выдавать лишние права автоматически.
3. Подготовить короткое migration window с оценкой locks и ручным rollback транзакции при timeout. MODULE/FREEZE/APPLY=false на всех репликах; active freezes отсутствуют. Применить четыре миграции в порядке выше с учётом уже применённых стадий, не запускать их повторно без проверки ledger.
4. Проверить фактическое исполняемое определение projection/source helper, trigger order/enabled/clones, permissions и legacy receive/transfer/adjust/FBS без freeze. Это обязательная проверка и для DB-first старого binary.
5. Обновить все процессы ФБС и API; подтвердить единую конфигурацию FBS source location и отсутствие старых экземпляров/in-flight старых transactions. App-first допускается при flags=false благодаря built-in GUC/capability, но новый модуль не должен обслуживаться на неполной схеме.
6. MODULE=true, затем FREEZE=true с APPLY=false. На маленьком обычном partial scope выполнить begin/confirm/final count/ready, проверить обычные отказы, FBS invalidation, release и восстановление после перезапуска приложения.
7. После закрытия всех замечаний и staging матрицы разрешить APPLY=true только для согласованного малого пилота. Проверить реальную projection и registry до/после первого документа, prepared/result revision и отсутствие лишних movements.

**Отключение:** запретить новый apply; дождаться завершения уже начатых apply transactions; запретить новый freeze; сохранить новую worker-версию для FBS и status/release; явно снять все active fences с reason и проверить claims/document state; только затем отключать модуль/откатывать binary. Flags не отменяют начатую SQL-транзакцию и не освобождают persistent fences. Старый FBS binary с активными fences возвращать нельзя.

После записи первых inventory-source movements нельзя откатывать DB простым удалением новых таблиц/функций: история и ссылки уже являются частью committed физических операций. Схему и links сохранять. Компенсация количества — отдельное согласованное действие, не rollback deployment и не удаление historical movements. Старый app binary может не понимать новый applied document status; его следует лишить нового inventory API, сохранив обслуживание legacy операций и новую схему до отдельной процедуры совместимости.

## H. Выполненные проверки и недостающее покрытие

### H.1. Независимый прогон

- PostgreSQL 17.4 подтверждена образом отдельного контейнера; порт ограничен localhost. Все destructive test fixtures были направлены только в disposable `kiz_test`/его клоны. Production DSN не использовался.
- Пять inventory test files: **158 passed**, 86.95 s. JUnit: `/tmp/wms-stage2a-independent-review.xml`.
- Две дополнительные изолированные пробы дали результаты B.1/B.2. Скрипт `/tmp/wms-stage2a-independent-probes.py`, лог `/tmp/wms-stage2a-independent-probes.log`. Вне этого review временные файлы не являются постоянными regression tests; доказательство и точный сценарий сохранены выше.
- Первый общий прогон: **881 passed, 11 failed, 51 skipped**, 218.47 s. Из них 10 failures связаны с унаследованным `FBS_TASK_PROCESSING_MODE=task_level`, тогда как старые fake-repository tests ожидают default legacy, и 1 — известный ship/assignment deadlock. Skips обусловлены отсутствовавшими отдельными opt-in DSN для B2/activation/lock tests. Этот прогон не выдан за успешную полную регрессию.
- Выполнен отдельный baseline comparison: исходный `404c55e` в `/tmp`, без переключения ветки, без inventory freeze migrations. `test_concurrent_ship_and_assignment_preserve_touch_protocol`: первые пять запусков passed, на шестом воспроизведён `asyncpg.DeadlockDetectedError`. Содержимое baseline `kiz_ship_service.py` сверено с `git show 404c55e`. Лог `/tmp/wms-stage2a-independent-baseline.log`. **Этот deadlock не включён в новые дефекты Stage 2A**; изменение вероятности его появления не измерялось. Дополнительно `test_concurrent_transfer_and_assignment_preserve_touch_protocol` воспроизвёл deadlock на первом запуске того же исходного `404c55e`; лог `/tmp/wms-stage2a-independent-baseline-transfer.log`. Поэтому оба failures повторного полного suite имеют независимое baseline-воспроизведение.
- Повторный общий прогон с явным `FBS_TASK_PROCESSING_MODE=legacy`, `KIZ_TEST_INVENTORY_SCHEMA=1` и всеми четырьмя opt-in DSN, направленными только на disposable PostgreSQL: **941 passed, 2 failed, 0 skipped**, 237.32 s. Failures — `test_concurrent_ship_and_assignment_preserve_touch_protocol` и `test_concurrent_transfer_and_assignment_preserve_touch_protocol`, оба с `asyncpg.DeadlockDetectedError`. JUnit: `/tmp/wms-stage2a-independent-full-explicit.xml`. Успешной полной регрессией этот результат не объявляется.

Ранее заявленные 943 passed не использовались как замена независимому запуску. Первичная ошибка запуска baseline из-за отсутствия обязательных Settings в отдельной `/tmp` копии была устранена явными disposable DB settings; она не является дефектом WMS.

### H.2. Обязательные дополнительные integration/staging tests

1. B.1: реальное migration adaptation с допустимыми отличиями установленного body, отказ неподдержанного определения и effect fault matrix после установки.
2. B.2: migration/legacy workers под настоящими не-owner ролями и последовательность выдачи grants; новые и старые binaries с flags=false, затем новый worker с freeze.
3. Live RabbitMQ: standard и external queues, HTTP ingress, manual retry и scheduled retry, legacy/task-level/multi-source режимы, повтор сообщения и delivery во время freeze/ready/apply. Проверить не только вызов handler, но ack/requeue, item outcomes и отсутствие потерянных/двойных эффектов после restart/network timeout.
4. Controlled extract, move и unpack под freeze через реальные DB services; имеющиеся generic guards и fill test не заменяют каждый lifecycle endpoint. Параллельные freeze/KИЗ assignment/container operation и maintenance с управляемым расписанием locks, не только последовательный отказ.
5. Migration window на текущей staging schema: порядок BEFORE/AFTER/deferred triggers, права/owners, новая 2027 partition и attach, enabled/cloned triggers и direct leaf INSERT. Проверить rollback неуспешной миграции, app-first и DB-first; текущая fixture не равна произвольной production schema.
6. Максимальная реалистичная плотность SKU в 20 адресах, длительность ready/apply, deferred проверки и задержки FBS при apply/maintenance. Настроенные timeouts не увеличивать ради прохождения. Долговременный freeze, restart API/worker и явный recovery/release с audit.
7. Operational acceptance: доказать источники delayed FBS, завершение физической работы на выбранных адресах и действия при её обнаружении после ready. Серверного timestamp для доказательства нет.

## I. Условия допуска к physical pilot

Допуск возможен после исправления B.1, определения и проверки grants по B.2, успешной актуальной regression matrix и staging deployment rehearsal. Нужны одновременно:

- Проверенный установленный SQL-контракт и реальный эффект surplus/shortage/zero/missing-row/NULL-empty/deferred rollback под runtime-role.
- Все FBS processes обновлены и проверены; альтернативные delayed sources не попадают в pilot. Глобальная очередь продолжает работать, незнакомые ship не получают исключение.
- Малый partial scope обычных активных адресов, без FBS root/descendants, active КИЗ, контейнерных остатков, batch ambiguity, неизвестных tasks и uncontrolled writers. Лимит 20 адресов сам по себе не является оценкой нагрузки.
- Сохранённое физическое подтверждение ответственного, затем новый final count в текущем freeze epoch; invalidation требует урегулирования и нового пересчёта, а не повторного использования старого count.
- Согласованная процедура остановки новых apply, освобождения зависшего freeze и расследования позднего physical события; не автоматическое удаление movements и не отключение DB guards.

Stage 1 и подготовительная модель application/components пригодны для продолжения. На штатном поддержанном SQL body механизм physical apply действительно работает, а не является только таблицами подготовки. Но успешные тесты этого body не закрывают B.1 и не заменяют проверку ролей, deployment всех workers и физической границы пилота. Production-ready заключение по текущему состоянию не выдаётся.

### Завершение review

Проверены 66 локальных ссылок отчёта: все файлы и номера строк существуют. Disposable review PostgreSQL остановлена и удалена после завершения проверок. Рабочая ветка и исходные незакоммиченные файлы сохранены; код, миграции и тесты не исправлялись, commit/deployment не выполнялись. Новые endpoints и миграции в рамках review не создавались.
