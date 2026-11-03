# Code Review: инвентаризация WMS, Stage 1

Дата: 29.10.2026. Проверенная ветка: `feature/inventory-stage1`.
HEAD: `8aa94e40e868f41084537fd68016dc988cd59a26` — `[+] добавить модуль инвентаризации без корректировки остатков`.
База сравнения: `404c55e`, без переключения рабочего дерева.

Режим: review, без исправлений реализации. Создан только этот отчёт. Production не использовался; все записи, миграции и тесты выполнялись исключительно в специально созданной disposable PostgreSQL.

## A. Итоговая оценка

**Вердикт: не готов к включённому развёртыванию Stage 1 из-за блокирующей проблемы формирования частичной области (B.1).** Развернуть код с `INVENTORY_MODULE_ENABLED=false` и `INVENTORY_APPLY_ENABLED=false` можно отдельно от включения функциональности, после обычной проверки deployment.

Подтверждены **2 новых дефекта: P0 — 0, P1 — 1, P2 — 0, P3 — 1**. Прежний КИЗ-deadlock разобран отдельно и в это количество не включён.

Основная реализация соответствует границе Stage 1:

- Самостоятельные документы, 19 API-операций, 11 additive-таблиц; `tasks` и системные snapshots не используются как документ инвентаризации.
- Запуск, claims, snapshot, строки и начальная ревизия атомарны. Активные claims защищены PK по location_id, а не предварительным SELECT.
- Есть сохранение прогресса, история попыток, optimistic version и идемпотентность команд. Исходный snapshot не переписывается.
- Физические остатки, движения, registry и lifecycle КИЗ новый модуль только читает. Публичный movement API и общие физические DB functions не изменены.
- Reconciliation предварительный; каждая строка имеет `safe_for_apply=false`. Отсутствие алгоритма физического применения — правильная граница этапа, не дефект.

Подтверждение: [service:157](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:157), [start:351](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:351), [SQL:1](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/inventory_documents.py:1), [migration:7](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261022_add_inventory_documents.sql:7), [response:168](/home/skurbick/PROJECTS/WMSService/app/core/schemas/inventory_documents.py:168).

### Достоверность и границы проверки

- **Код:** изучены текущие service/repository/queries, DTO, endpoints, DI, flags, ошибки, миграция, тесты и изменения относительно `404c55e`.
- **Локальная PostgreSQL:** 17.4, отдельный контейнер `wms-inventory-review-20261029`, опубликованный только на `127.0.0.1:55449`. Проверена его Docker identity до тестов. Production DSN не использовался.
- **Результаты:** 43/43 теста модуля; полный suite — **827 passed, 1 failed**, 66 существующих Pydantic warnings, 152.36 секунды. Неуспешен прежний КИЗ-deadlock.
- **Независимые проверки:** воспроизведены B.1 и B.2; отдельно проверены commit КИЗ между SQL-чтениями snapshot и конкурентные progress-команды с разными ключами.
- **Не проверено:** актуальное production-состояние на 29 октября, deployment backend, production grants и performance на больших складах. Результаты аудита БД от 19 октября не выдаются за повторную проверку production.

В начале работы tracked-изменений не было. Untracked-файлы `docs/audit/wild188901_corrections_retry_20261025.md` и `fbs2_missing_kiz_2026-10-21_2026-10-22.txt` — посторонние материалы, не часть реализации Stage 1; не изменялись. Учтено, что реализация теперь закоммичена, в отличие от состояния в предыдущем отчёте.

Прочитаны три предыдущих аудита и [описание Stage 1](/home/skurbick/PROJECTS/WMSService/docs/flows/inventory_stage1.md:1). В текущей ветке базовые КИЗ/registry migrations уже присутствуют, container API работает через controlled operations; утверждения старых аудитов об отсутствии этих миграций и legacy container SQL не перенесены как текущие дефекты. Например, [ContainerService:35](/home/skurbick/PROJECTS/WMSService/app/core/services/container_service.py:35) запрещает регистрацию с содержимым. Исходное `docs/context/` отсутствует; контекст находится в `docs/current/` и `docs/decisions/`.

## B. Обнаруженные проблемы

### B.1. P1 — проверка дерева пропускает внутреннее рассогласование parent/path и фиксирует неверную частичную область

**Подтверждено текущим кодом и независимым воспроизведением на PostgreSQL.**

Место: [inventory_document_service.py:270](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:270), строки 270–285; [inventory_documents.py:58](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/inventory_documents.py:58), строки 58–73.

`resolve_scope()` сравнивает parent-tree и LTREE только для корня всего склада. Затем выбранные поддеревья разворачиваются исключительно по path. Равенство двух множеств всего склада не доказывает согласованность вложенных ветвей.

Воспроизведение, только в disposable БД:

1. Дерево `Warehouse → Zone A → Bin → Leaf`, рядом `Warehouse → Zone B`.
2. На Leaf есть положительный available loose stock.
3. Выполнить `UPDATE wms.locations SET parent_location_id=<Zone B> WHERE location_id=<Bin>`.
4. Действующий в тестовой схеме location trigger меняет path Bin, но не path Leaf. Весь набор location_id под Warehouse остаётся тем же по обеим моделям.
5. Создать и запустить partial inventory по Zone B с descendants.

Реальный результат API: start успешен; parent-tree содержит **[4, 5, 8]**, frozen scope — **[4, 5]**; Leaf **8** с остатком отсутствует. Это не предположение из архивного SQL: сценарий выполнен через текущий inventory API на восстановленной тестовой схеме с актуальными миграциями. Сам перенос parent выполнялся SQL в disposable БД, не HTTP endpoint смены родителя.

Обратная ошибка тоже следует из запроса: выбор Zone A может включить Leaf по устаревшему path, хотя parent-chain уже ведёт в Zone B. Существование такого production-рассогласования на 29 октября не проверялось.

**Нарушение:** выбранный родитель должен включать всех своих потомков либо запуск должен явно отклоняться при конфликте дерева. Сейчас документ молча получает неправильные строки, snapshot и claims. PK claims корректно защищает именно записанные ID, но не может защитить пропущенный Leaf.

**Последствия:** неполная инвентаризация выбранной зоны; адрес с остатком пропущен без конфликта; возможен отдельный активный документ на пропущенном адресе, вопреки намерению включить всё поддерево.

**Минимальное безопасное исправление:** внутри нового модуля проверять согласованность внутренних parent/path edges либо сравнивать parent-based и LTREE-based descendants каждой выбранной зоны в том же snapshot. При несовпадении возвращать `INVENTORY_HIERARCHY_CONFLICT` до фиксации scope. Не ремонтировать дерево и не менять shared location functions в рамках этого исправления.

**Тест исправления:** приведённый перенос внутри одного root, с Leaf и остатком. Проверить отказ create/preview/start там, где вызывается resolver; для уже существующего draft — rollback start без locations/components/claims/revision. Добавить симметричный выбор старой зоны. Существующий [test_scope_is_frozen_after_new_location_and_tree_move:213](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:213) проверяет изменения после старта, а не это рассогласование перед ним.

### B.2. P3 — API принимает ID вне bigint и отвечает HTTP 500 вместо ошибки валидации

**Подтверждено текущим кодом и независимым API-воспроизведением.**

Место: [endpoints/inventory_documents.py:39](/home/skurbick/PROJECTS/WMSService/app/api/v1/endpoints/inventory_documents.py:39), строки 39–41, 57; [schemas/inventory_documents.py:34](/home/skurbick/PROJECTS/WMSService/app/core/schemas/inventory_documents.py:34), строки 34–35, 42, 88–90; [repository:17](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/inventory_document_repository.py:17).

Положительные Python int не ограничены диапазоном SQL bigint. Включить модуль и запросить:

```http
GET /api/inventory-documents/9223372036854775808
```

Результат: **HTTP 500**, `asyncpg.exceptions.DataError: value out of int64 range`, а не 422/404. Ошибка воспроизведена через ASGI client. Аналогичная граница отсутствует для body location IDs и offset, передаваемых в bigint SQL parameters.

**Нарушение и последствия:** некорректный клиентский ввод достигает БД и классифицируется как внутренняя ошибка; лишние traceback и 5xx. Физические остатки не затрагиваются. Это не блокирует корректные запросы.

**Минимальное исправление:** ограничить соответствующие SQL-bound integer inputs значением `9223372036854775807` в новых DTO/Path/Query. Не менять контракты старых endpoints и не подменять все DB DataError общей ошибкой валидации.

**Тест исправления:** path document/item/attempt, query/body warehouse/location и offset выше bigint должны давать 422 до SQL; верхняя допустимая граница должна проходить DTO и обрабатываться обычной логикой not-found/пустой страницы.

## C. Проверка бизнес-правил

Обозначения: «да» в колонке тестов означает выполненный существующий PostgreSQL/API или unit-тест, не production-проверку. Указаны начала конкретных тестов.

| Требование | Реализовано | Проверено тестом | Замечания |
|---|---|---|---|
| Отдельный документ, не tasks/snapshot | Да | [workflow:289](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:289) | Собственный lifecycle, без applied/approved |
| Full: root и descendants | Да для согласованного дерева | [full:171](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:171) | Некорректное внутреннее дерево: B.1 |
| Partial по локациям, SKU, пересечению | Да для согласованного дерева | [filters:195](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:195) | Есть exact location и include_descendants |
| SKU-only охватывает весь склад, включая пустые адреса | Да | filters:195 | Scope не строится только из наличия inventory |
| Пустые и неактивные адреса не исключаются | Да | full:171, filters:195 | Inactive root отвергается; inactive stock получает conflict |
| После start scope неизменяем | Да | [freeze:213](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:213) | Хранятся location ID/code/path/is_active |
| Атомарный scope/claims/snapshot | Да | [overlap:235](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:235), [rollback:651](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:651) | Ограничение B.1 относится к правильности исходного множества |
| Пересечение запрещено независимо от SKU | Да для frozen IDs | overlap:235 | PK claims(location_id); disjoint start:272 |
| available, loose, без active КИЗ | Да | [mixed:389](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:389), [container:724](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:724) | Active loose вычитаются из exact null-batch capacity, а не просто container=NULL |
| Holderless registered не вычитать из произвольной location | Да | [registered:637](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:637) | Сохраняются как контекст/неоднозначность |
| Immutable snapshot физических batch components | Да | mixed:389, [immutable:460](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:460) | Недостача между партиями не распределяется |
| Snapshot inventory/КИЗ согласован | Да на уровне DB snapshot | [writer:834](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:834) + независимая mid-capture проверка | Не равен доказанному физическому моменту count |
| Только положительный initial expected автоматически | Да | full:171, filters:195; [service:395](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:395) | SQL читает и zero rows, но строки создаются только при expected>0 |
| Не пересчитано отдельно от actual=0 | Да | workflow:289 | NULL, latest_attempt и attempt status используются отдельно |
| Прогресс не завершает попытку | Да | [progress:331](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:331) | Есть история каждого progress |
| Повторный count сохраняет прошлые попытки | Да | workflow:289 | Одна открытая попытка на item, completed immutable |
| Запрет редактирования completed attempt | Да | immutable:460; [service:539](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:539) | Защита API и DB trigger |
| Найденный товар: expected=0, frozen scope/SKU, без дубля | Да | [found:355](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:355) | Поздний приход показан отдельно от initial expected |
| Неполный документ нельзя submit | Да | workflow:289 | Recount_required тоже блокирует повторный submit |
| Возврат на пересчёт с историей | Да | workflow:289 | Вариант selective affected item_ids прочитан; отдельного теста выборочного возврата нет |
| Close without adjustment окончателен и освобождает claims | Да | workflow:289, overlap:235 | Claims и статус изменяются вместе |
| Идемпотентность и optimistic version | Да | progress:331, [draft:479](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:479), [create race:520](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:520) | Replay возвращает первоначальный ответ, не последнее состояние документа |
| Decimal без потери точности | Да | [decimal:818](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:818), [unit:24](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_unit.py:24) | Float/bool для quantity запрещены; numeric(12,2) |
| Предварительный reconciliation, без physical apply | Да | [after count:556](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:556) | actual не переносится автоматически на новый момент |
| Late commit со старым created_at обнаруживается | Да | [late commit:580](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:580) | Сравниваются identity sets, не timestamp watermark |
| КИЗ eligibility без movement | Да | [assignment:607](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:607), [activation:679](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:679) | Учитываются state и events |
| Container extraction влияет на loose | Да | container:724 | Контейнерный расход не взаимозачитывается с loose приходом |
| FBS timestamps не считаются физическим порядком | Да | [FBS:763](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:763), [during count:433](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:433) | Настроенная FBS location ambiguous даже до появления ship |
| Flags fail closed | Да | [flags:77](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_unit.py:77) | apply=true даёт 503, не создаёт движения |
| Новый модуль не меняет physical state | Да в проверенном workflow и по write-set | workflow:289, migration fixture:32 | Сравниваются inventory/movements/КИЗ/registry до и после |
| Evidence cap 100000 | Реализован | Только имитация capture failure:651 | Реальные границы 100000/100001 и нагрузка отдельно не проверялись |

### Архитектура, авторизация и ошибки

Слои FastAPI → service → repository/SQL сохранены; новой ORM, очередей, альтернативного registry или КИЗ-нормализатора нет. Service использует переданное соединение во всех командах; частные SQL-вызовы из service не приводят к отдельным autocommit-транзакциям. Стиль не полностью скрывает SQL за repository, но самостоятельного дефекта транзакций или необходимости рефакторинга только ради стиля здесь нет.

Новые endpoints подключены к тому же [api_router:30](/home/skurbick/PROJECTS/WMSService/app/api/v1/router.py:30), что остальные WMS endpoints, не обходят общий путь приложения. Отдельной auth dependency на новых routes нет; этот review не доказывает защищённость внешнего периметра и не предполагает существование не показанной в коде авторизации. Имя автора поступает от клиента, не является доказанной authenticated identity; это текущий контракт Stage 1 и известный долг ролей, а не новое разграничение прав.

[InventoryDocumentError:6](/home/skurbick/PROJECTS/WMSService/app/core/inventory_document_errors.py:6) имеет отдельный [HTTP handler:63](/home/skurbick/PROJECTS/WMSService/app/middleware/error_handler.py:63). Domain-конфликты отображаются в 404/409/422/503. Изменённый handler не меняет обработку старых exception types. Произвольные DB statement timeout/DataError не превращаются в domain error; пример B.2 подтверждён, нагрузочные timeout-сценарии не выполнялись.

## D. Транзакции и конкурентность

### Реальные гарантии

| Сценарий | Фактический механизм | Проверка/граница |
|---|---|---|
| Две команды одного документа | `FOR UPDATE` заголовка + expected_version | [command:182](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:182); при RR конфликт версии строки приводит к fresh transaction retry |
| Повтор одной команды | Transaction advisory lock глобального UUID, immutable command result, request SHA-256 | [command:157](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:157), [SQL:3](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/inventory_documents.py:3); тот же key с другим action/URL/body — 409 |
| Два concurrent create с одинаковым key | Advisory lock + unique command PK; повтор после UniqueViolation с новым RR snapshot | [service:227](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:227), create race:520 |
| Два overlapping start | Sorted claims insert + PK(location_id) | [migration:43](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261022_add_inventory_documents.sql:43); проигравший start целиком откатывается |
| Close и competing start | Claims DELETE и окончательный статус в одной transaction | [close:704](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:704); direct race close/start отдельно не воспроизводился |
| Concurrent progress с разными keys | Doc lock + optimistic version, не только open-attempt index | Независимый probe: ответы **409/200**, в history ровно **1** save_progress |
| Submit/return/close | Тот же doc lock и проверка status/version внутри transaction | [submit:671](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:671), [return:687](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:687); всех парных гонок отдельными тестами нет |
| Ошибка в любой части start | Context transaction rollback | rollback:651; locations, claims, snapshot, ревизия и state не коммитятся частично |
| Serialization/deadlock | До 3 полных попыток; затем domain 409 | [service:166](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:166), 221–243; принудительное исчерпание всех retry отдельно не проверялось |
| История после команд | Audit и saved response в той же transaction | [service:193](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:193); rollback не оставляет history успешной команды |

Write transaction — REPEATABLE READ, `lock_timeout=5s`, `statement_timeout=15s`. Чтение inventory, КИЗ, movements и events через несколько SELECT видит один MVCC snapshot, а не независимые состояния между запросами. Это не физическая сериализация склада.

Независимый probe остановил capture после GET_STOCK и выполнил обычный КИЗ assignment на другом connection до GET_KIZ. Результат: **initial_expected=10.00, snapshot_kiz=0, live_active=1**. То есть snapshot остался согласованным с состоянием до assignment, а writer успешно завершился. Это дополнительная проверка реального PostgreSQL, не mock доказательства.

Advisory lock команды может ждать уже после установления RR snapshot. Код учитывает невидимый committed command через unique-conflict retry, а обновившийся документ — через serialization retry. Нельзя убрать эти retries как «лишние» без потери concurrent replay.

### Блокировки и оставшиеся ограничения

Нет явных row locks inventory/КИЗ в новом snapshot. Но FK к products/locations берут обычные KEY SHARE locks; поэтому обещание «совсем не блокирует writers» было бы неверным. Например, старый explicit KИЗ transfer берёт `FOR UPDATE` locations ([SQL:8](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_transfer.py:8)) и может ждать FK lock незавершённого start. Длительность этих ожиданий на большом scope не измерялась. Claims не являются запретом движений, однако обычные PG lock conflicts остаются.

Большой документ сериализует даже записи разных строк на одном header. Это безопасно, но клиентам потребуется обработка 409 и повтор с новой версией/новым ключом; линейное масштабирование количества одновременно считающих сотрудников не гарантируется.

Snapshot устанавливается раньше `observed_at`: transaction сначала выполняет SELECT advisory lock/документа, а clock_timestamp вызывается позже в capture. Поэтому observed_at — отметка наблюдения, не точный момент snapshot, commit или физического действия. Текущий код не использует её как watermark; это допустимо для Stage 1, но непригодно как доказательство порядка Stage 2.

### Предварительная reconciliation

[Алгоритм:581](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:581) отдельно сохраняет initial expected, actual, current expected и новые видимые evidence. Начальные значения не изменяет. Для movements сохраняются from/to и положительное quantity; **signed movement delta или physical target не рассчитывается**. Направление эффекта видно из endpoints, а current expected берётся из фактической проекции inventory за вычетом active КИЗ. Таким образом, ошибки знака в автоматической корректировке здесь нет: самой автоматической корректировки нет.

Проекция evidence привязана к product/location; registry ref — идентификатор, не commit sequence. КИЗ state/events обнаруживают eligibility changes, в том числе без movement. Container fill/extract даёт релевантную loose-сторону, а контейнерные inventory rows исключены.

Проверенные сценарии:

- Initial 10, actual 8, receive +3 после count: current expected **13**, initial discrepancy **−2**, actual остаётся **8**, ambiguous/recount=true, safe_for_apply=false. Target 11 и adjustment −2 не объявляются доказанными.
- Initial 10, физическое изъятие 2 до count, actual 8, поздний FBS ship 2: DB не доказывает физическую границу. После записи ship current expected **8**, но совпадение с actual не снимает ambiguity/recount. В configured FBS location неоднозначность отмечается ещё до записи ship.
- Movement во время count вызывает `state_changed_during_count`; изменение после finish — `state_changed_after_count`. Изменение projection без нового evidence также отмечается, если изменилось наблюдаемое состояние.

Отсутствие изменений между наблюдениями не доказывает отсутствие любых промежуточных физических или maintenance-событий. Реестр holderless registered также не доказывает, где физически находятся эти единицы. Ни один из этих пробелов не закрыт общим writer protocol — по ТЗ Stage 1 это ограничение, а не обещание автоматической безопасности.

Evidence ограничен по каждой из четырёх выборок: LIMIT 100001 и явная ошибка при >100000 ([repository:25](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/inventory_document_repository.py:25)). Молчаливого усечения нет. Но читается вся видимая история scope, а holderless registered/null-location events могут относиться к другим локациям; лимит результата не ограничивает стоимость scan/sort. Документ, стартовавший около порога, может позднее потерять возможность capture/submit; close без применения остаётся доступным. Нужны нагрузочная проверка и понятная операторская политика; это известная граница, не подтверждённая текущая production-ошибка.

### Проверка прежнего deadlock

Исходный `404c55e` извлечён через git archive в `/tmp/wms-inventory-review-base-UP3IZi`, без checkout рабочей ветки. Тест и его fixtures/services/repositories сравнивались с HEAD: различий в соответствующих файлах нет. `kiz_pool` для этого теста **не применяет новую inventory migration** ([fixtures:183](/home/skurbick/PROJECTS/WMSService/tests/conftest.py:183)).

Контролируемая серия, отдельный pytest process на каждый запуск, один и тот же disposable PostgreSQL и явные локальные DB-настройки:

| Код | Запусков | Passed | DeadlockDetectedError |
|---|---:|---:|---:|
| `404c55e` | 8 | 4 | 4 |
| `8aa94e4` | 8 | 7 | 1 |

В обеих версиях ошибка возникает на source inventory lock в transfer. Цикл объясняется существующими locks:

1. Transfer сначала блокирует locations `FOR UPDATE`, затем inventory ([service:88](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_transfer_service.py:88)).
2. Assignment блокирует inventory, делает touch, затем INSERT КИЗ ([service:39](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_service.py:39)).
3. INSERT КИЗ проверяет FK location и ждёт KEY SHARE, конфликтующий с location lock transfer. Transfer уже ждёт inventory assignment.

**Вывод: существование ошибки до Stage 1 подтверждено экспериментально; это не новая регрессия inventory.** Частоты 4/8 и 1/8 не являются статистическим доказательством улучшения/ухудшения. Единичные successful runs не доказывают отсутствия гонки. Исправления не выполнялись; старый risk остаётся эксплуатационным долгом WMS и будущим ограничением выбора lock order Stage 2.

## E. Миграция и обратная совместимость

### Проверка схемы

Все 11 таблиц из [миграции:7](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261022_add_inventory_documents.sql:7) применены существующими тестами на PostgreSQL 17.4:

| Таблица | Проверенные ограничения и назначение |
|---|---|
| inventory_documents | PK, mode/status CHECK, положительная version, state/timestamps, FK корня и current/submitted revision |
| inventory_document_filters | PK/FK документа, JSON array, draft-only INSERT/UPDATE, запрет DELETE |
| inventory_document_locations | Composite PK(document,location), FK location; immutable frozen attributes |
| inventory_location_claims | PK(location), composite FK frozen scope, индекс document_id для release |
| inventory_document_revisions | Composite PK(document,revision), positive revision, kind CHECK, JSON object, preliminary_only=true; immutable UPDATE/DELETE |
| inventory_document_stock_components | PK(document,inventory_id), scope FK, product FK, available/null-container CHECK, quantity/active constraints; нет live FK inventory_id |
| inventory_document_kiz_snapshot | PK(document,kiz_id), product/location FK; historical kiz_id без live FK, чтобы не lock-ить identities |
| inventory_document_items | Unique(document,product,location), scope FK, discovered⇒expected=0, immutable initial fields |
| inventory_count_attempts | FK конкретного document/item/revision, unique attempt number и partial unique одной открытой попытки, completed quantity/timestamps, immutable completed/baseline |
| inventory_document_history | PK, FK document, индекс document/event; immutable UPDATE/DELETE |
| inventory_document_commands | Global UUID PK, FK document, saved request hash/result; immutable UPDATE/DELETE |

Lifecycle guard разрешает только предусмотренные переходы и увеличение version на 1 за UPDATE; закрытый документ не изменяется ([migration:180](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261022_add_inventory_documents.sql:180)). Snapshot/history guards объявлены только на новых таблицах. FK latest_attempt включает document/item/attempt, поэтому нельзя сослаться на попытку другой строки. Ревизии current/submitted имеют composite FK к своему документу.

Важное разграничение: схема не является полным DB-only state machine. Вставку новой строки items/attempts и полноту submit контролирует service под doc lock; одни constraints не запрещают все произвольные прямые SQL-записи в новых таблицах. Дефекта через текущий API из этого не установлено. Runtime role не должна предоставлять внешним клиентам обход service; перед Stage 2 отдельный DB application guard всё равно потребуется.

Индексы покрывают claims release, document/item/history pagination, физические компоненты пары и открытые attempts. Для фильтра списка по warehouse нет отдельного индекса, а revisions API возвращает крупный JSON evidence: необходимость дополнительных индексов/summary DTO определяется нагрузкой, не является доказанной ошибкой на текущем объёме.

### Безопасность deployment

- В миграции нет ALTER старых physical tables, replacement старых functions/triggers, backfill, новых movement types или физических DML. Единственные ALTER TABLE относятся к новым documents/items.
- Существующая fixture сравнивает definitions **всех существовавших wms functions** до/после миграции ([test fixture:32](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:32)); проверка прошла.
- DDL обёрнут в BEGIN/COMMIT, lock_timeout=5s, statement_timeout=30s. Ошибка не должна оставлять половину новой схемы. Скрипт не идемпотентен: повтор CREATE TABLE/FUNCTION/триггеров завершится ошибкой; это прямо указано в [runbook:193](/home/skurbick/PROJECTS/WMSService/docs/flows/inventory_stage1.md:193). Применять однократно по deployment journal, с остановкой runner на ошибке, не обходить её частичным ручным продолжением.
- Новые FK требуют locks на referenced products/locations при DDL; additive не означает «нулевая блокировка старого WMS при deployment». Использовать низкую нагрузку и bounded lock timeout. Реальное ожидание DDL под production write traffic не измерялось.
- Нет автоматического применения миграции при запуске сервиса. Code сначала можно развернуть с flags=false; затем отдельно применить проверенный SQL и grants, включить module только после исправления B.1. Apply остаётся false.
- Rollback приложения: выключить module, сохранить additive schema и audit; не удалять документы/историю для отката. Новых физических движений Stage 1 не создаёт, поэтому компенсировать их нечего.

### Матрица обратной совместимости

| Действующая операция/компонент | Изменение Stage 1 | Фактическая проверка и остаточный риск |
|---|---|---|
| POST/GET movements, обычный adjust | Нет изменения DTO/type/service/SQL | [legacy adjust:792](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_documents_postgres.py:792): расход/приход через HTTP после новой миграции, итог 11 |
| Общий inventory updater и guards | Не изменены | Сравнение всех старых function definitions прошло; Stage 1 их не вызывает для записи |
| FBS standard/external/HTTP, handlers/retry | Код не менялся | Full regression выполнен; реальное RabbitMQ/физическое время отгрузки не тестировались |
| Multi-source КИЗ FBS | Только чтение его effects | [current service:45](/home/skurbick/PROJECTS/WMSService/app/core/services/fbs_kiz_shipment_service.py:45) может списывать из разных holders; inventory не предполагает один movement на сообщение |
| КИЗ lifecycle/нормализация | Не менялись | Assignment/activation/deactivation отражены evidence; mid-capture writer завершился; прежний deadlock подтверждён на base |
| Controlled container fill/extract | Не менялись | Integration container extraction после новой migration прошёл, loose current expected корректен |
| Комплектация/разукомплектация/пересортица | Код и общие movement types неизменны | Full regression; concurrency этих writers именно с большим inventory start отдельно не измерялась |
| Tasks/recount/approval | Код/контракты не менялись | Полный suite; новый документ не делегирует применение старому tasks approval |
| Operations/inventory history, reports | SQL/read models не менялись | Full suite; Stage 1 не создаёт новые ledger records, которые readers должны классифицировать |
| Integrity/recalculate-inventory | Код/схема не менялись | Snapshot inventory_id не имеет live FK и не мешает удалению обнулённых/пересоздаваемых rows; maintenance с активным документом отдельно не запускался |
| Старый backend + новая схема | Additive объекты, известные старые types | Старый бинарник с новой migration отдельно не запускался; legacy adjust на новой схеме проверен. Full suite не означает, что каждая его fixture содержит inventory migration |
| Общие DI/config/error handler | Добавлены отдельные flags, dependency и exception type | Flags fail closed до pool dependency; existing error handlers не заменены |

**Заключение:** нарушения физического write-set и изменения контрактов старого WMS не обнаружены. Это подтверждено diff, реальными tests и ограниченным post-migration workflow, но не является обещанием отсутствия любых production/performance-регрессий.

## F. Тестирование

### Выполнено

1. **Модуль отдельно:** `tests/test_inventory_documents_unit.py` и `tests/test_inventory_documents_postgres.py` — **43 passed**, 66 warnings, 18.04 секунды.
2. **Полный suite:** **827 passed, 1 failed**, 66 warnings, 152.36 секунды. Единственная ошибка — `tests/test_kiz_transfer_integration.py::test_concurrent_transfer_and_assignment_preserve_touch_protocol`, DeadlockDetectedError.
3. **Сравнение deadlock:** финальная контролируемая серия 8 base + 8 current запусков; результаты в D. Исходники извлекались в /tmp, а не через смену ветки.
4. **Независимый negative probe области:** успешный start с отсутствующим Leaf; B.1 подтверждён.
5. **Независимый negative probe API ID:** bigint overflow → HTTP 500; B.2 подтверждён.
6. **Независимый snapshot probe:** assignment commit между GET_STOCK/GET_KIZ не нарушил RR consistency и не ждал конца snapshot.
7. **Независимая гонка progress:** разные UUID, одна expected_version; 409/200, ровно одна запись progress в history.

Probe-код исполнялся из памяти через Python с существующими fixtures/API helpers. Тестовые файлы не редактировались и не создавались. Предварительная попытка baseline comparison не прошла collection из-за отсутствия обязательных DB settings в архиве без .env; повторена с явными disposable settings. Такие collection errors не включены в сравнительную серию deadlock.

### Изоляция

Контейнер имел identity `cc0f4fb329adc5608d886c8301e5dbfa2cd839ec1ef268bd68c285db920dc496`, image `postgres:17`; сервер сообщил PostgreSQL 17.4. Host port — только `127.0.0.1:55449`, не стандартный production host/port. Созданы disposable `kiz_test`, `kiz_b2_test`, `kiz_activation_test`; остальные case/template databases создавались и удалялись существующими fixtures.

База kiz_pool восстанавливается из архивных schema dumps 08.08.2026 с последовательным применением текущих КИЗ/container/FBS migrations; это не свежая копия production от 29 октября. Fixture явно исключает несвязанный external public FK и два public product triggers, определения которых отсутствуют в dump ([conftest:25](/home/skurbick/PROJECTS/WMSService/tests/conftest.py:25)). Эти ограничения не скрываются за формулировкой «полный suite».

После завершения проверок identity контейнера повторно сверена; остановлен только этот контейнер. Его disposable БД удалены механизмом `--rm`, восстановление тестовых данных не требуется. Другие контейнеры не затрагивались; архив исходного commit в /tmp не является изменением рабочего дерева.

Явно заданы все test DSN: `KIZ_TEST_DSN`, `KIZ_LOCK_TEST_DSN`, `KIZ_B2_TEST_DATABASE_URL`, `KIZ_ACTIVATION_TEST_DATABASE_URL`, а также DB_HOST/PORT/USER/PASSWORD/NAME для полного запуска и baseline comparison. Пароли и полные credentialed DSN здесь не приводятся. Использованы `PYTHONDONTWRITEBYTECODE=1` и `-p no:cacheprovider`, чтобы не создавать новые cache-файлы в репозитории.

Команда полного запуска после установки **только disposable** переменных:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 FBS_TASK_PROCESSING_MODE=legacy \
  .venv/bin/pytest -q -p no:cacheprovider
```

Legacy выбран только для baseline старых FBS mock tests; task-level tests задают собственный режим. Production configuration не изменялась. Часть существующих fixtures делает DROP SCHEMA/TRUNCATE, поэтому эту команду нельзя повторять с рабочими DSN ([B2 fixture:28](/home/skurbick/PROJECTS/WMSService/tests/test_kiz_import_b2_postgres.py:28), [activation fixture:32](/home/skurbick/PROJECTS/WMSService/tests/test_kiz_activation_postgres.py:32)).

### Недостатки покрытия и ещё необходимые проверки

- B.1: внутрискладской parent/path conflict **до** start, включая rollback claims; существующий post-start freeze test этого не покрывает.
- B.2: границы bigint всех SQL-bound ID и offset.
- Все парные гонки submit/close/return/reconcile, concurrent close/start, повторный конфликтующий payload с тем же key одновременно. Механизм прочитан и базовые races проверены, полного расписания interleavings нет.
- Принудительные SerializationError/DeadlockDetectedError на разных этапах, исчерпание retries и отсутствие дублированной history после retry. Прежний КИЗ-deadlock не тестирует retry нового service.
- Реальные thresholds 100000/100001 каждого evidence query, рост после start, время/память больших revisions и attempts. Нынешний rollback test имитирует capture error, а не достигает threshold.
- Concurrent projection maintenance, ABA state changes без ledger и physically delayed FBS из внешнего процесса; существующие tests лишь демонстрируют консервативные flags на наблюдаемых DB effects.
- Selective affected-item return и сохранение остальных completed attempts; close с открытой попыткой и неизменность его history при последующих rejected командах. Submit пустого документа уже проверяется существующим draft/preview test.
- Миграция под конкурентной нагрузкой, grants least-privilege runtime role, старый deployment artifact с новой схемой и rollback. Чтение diff и зелёные unit tests это не заменяют.
- Заявление о полном отсутствии блокировок обычных writers не сделано: отдельно нужен тест длинного snapshot против explicit КИЗ transfer/ship с location FOR UPDATE и workload latency.

## G. Рекомендации перед Stage 2

### Уже пригодно как основа

- Отдельный header с version и current/submitted revision; doc-level lock и saved command result.
- Frozen scope и PK location claims, после исправления B.1.
- Immutable initial batch components, КИЗ context и раздельные actual attempts.
- Append-only revisions/history и обнаружение late committed ledger identities.
- Fail-closed flags и отсутствие физического writer в модуле Stage 1.

Это база для последующего атомарного approval, но **не готовый протокол физического применения**.

### Необходимо спроектировать и проверить отдельно

1. Зафиксировать approved revision и application identity; не использовать один current revision как доказательство отсутствия новых physical/KИЗ effects. Reconcile разрешён и в pending_approval, поэтому current_revision может отличаться от submitted_revision ([service:664](/home/skurbick/PROJECTS/WMSService/app/core/services/inventory_document_service.py:664)).
2. Установить физическую count boundary и правила conservative invalidation. Не превращать initial discrepancy/current expected в adjust без доказательства влияния событий между count и apply. Delayed FBS не разрешается сортировкой created_at/movement_id.
3. Доказать TOCTOU-safe lock/recheck для всех допущенных writers, включая activation/deactivation, controlled containers и maintenance. RR Stage 1 и claims, которые старые writers не читают, сами по себе этого не обеспечивают.
4. Оставить существующий `adjust`; добавить inventory-specific source и устойчивые application/component links на существующий movement_registry. Не создавать второй registry и не менять семантику старых движений.
5. До physical apply решить exact component allocation для batch и физическое отделение unidentified stock от active/holderless КИЗ. Сохранённая детализация полезна, но не определяет сама, какая партия потеряна.
6. Реализовать строгую проверку отсутствующей/недостаточной physical row, alias NULL/empty и фактического эффекта source-specific adjust. Обычный adjust в штатных сценариях не объявляется неисправным. Inventory-only guard/ветку отдельно оценить на совместимость со всеми старыми triggers.
7. В одной transaction выполнить все movements, registry/application links, решение, статус applied и освобождение claims; проверить rollback deferred-trigger ошибки и повтор approval. Последовательные HTTP POST movements недопустимы.
8. Определить bounded size approval и deterministic lock order, учитывая подтверждённый прежний цикл location↔inventory. Retry нового модуля не исправляет legacy writer, который уже получил deadlock.
9. Включать physical apply отдельным flag только после отдельного review/tests; начинать с однозначных ordinary locations, не автоматически с FBS/mixed/batch.

**До включения Stage 1:** исправить B.1 и добавить PostgreSQL regression test; желательно закрыть B.2. Прежний deadlock вести отдельной задачей, не маскировать как новый inventory defect и не считать устранённым. Провести stage deployment с flags=false, однократной миграцией и smoke-test, затем разрешить только documents/counting. Stage 2 в рамках этого review не реализовывался.
