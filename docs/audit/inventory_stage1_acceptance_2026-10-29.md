# Приёмочное тестирование инвентаризации Stage 1

Дата: 29.10.2026. Ветка: `feature/inventory-stage1`.

## 1. Итоговый вердикт

**Техническая приёмка Stage 1 пройдена для проверенного профиля: 5 338 адресов, 1 105 physical rows, 1 101 пара SKU/локация и 44 026 исходных movements.** Нагрузочные сценарии действительно выполнены через HTTP, включая полный пересчёт всех строк, параллельных writers и отказ на evidence limit. Это не заключение, основанное только на прежних unit/integration tests.

**Stage 1 готов к контролируемому развёртыванию документов/пересчётов с `INVENTORY_APPLY_ENABLED=false`**, при выполнении эксплуатационных условий из раздела 10. Безусловный production GO для любого объёма ledger, любого memory limit или любой нагрузки не выдаётся.

**К разработке Stage 2 переходить можно.** Разрешения включать физические корректировки эта приёмка не даёт: reconciliation остаётся предварительным, а безопасный атомарный apply требует отдельного протокола и отдельной приёмки.

Ключевые результаты заключительного нагрузочного прогона:

- Full start вместе с snapshot — **2.801 с**; full reconciliation — медиана **2.637 с**, максимум **2.859 с**.
- Автоматизированный пересчёт **1 102 строк** с begin/progress/complete — **60.688 с** без имитации времени ходьбы и физического подсчёта.
- Все **1 153 assertions** заключительного стенда прошли; число включает повторяемые проверки прогресса каждой строки и не означает 1 153 независимых нагрузочных сценария.
- Под обычными transfer, КИЗ assignment/transfer/deactivation и FBS-списаниями ошибок writers не получено. Добавлено ровно **1 567 movements внешних writers**, не новым модулем.
- При snapshot обнаружено реальное ожидание КИЗ-transfer на локации: около **2.005 с** по PostgreSQL, HTTP максимум **2.057 с**. Утверждение «модуль вообще не блокирует движения» неверно.
- При **100 003 movements** в области start вернул `409 INVENTORY_EVIDENCE_LIMIT` за **1.583 с**, без частичных claims/scope.
- Дополнительный полный workflow с сервером в отдельном процессе прошёл; peak RSS одного API-worker — **250 424 КиБ = 244.6 МиБ**. Физическое состояние после обоих quiescent workflows осталось неизменным.

Код, миграции, существующие тесты и конфигурационные файлы в рамках приёмки **не изменялись**. Создан только этот отчёт. Production не использовался; commit/deployment не выполнялись.

## 2. Что именно проверено

Проверен HEAD `8aa94e40e868f41084537fd68016dc988cd59a26` **с уже существовавшими незакоммиченными исправлениями B.1/B.2**. Поэтому одного указания HEAD недостаточно для воспроизведения версии.

SHA-256 вывода `git diff --binary` для этих восьми tracked-файлов:

```text
9bf477e08115ed25fa2fddf8d5ad7df00baf3d33a487f21c259584a76ccf832e
```

Исходное рабочее дерево и посторонние untracked-файлы сохранены. Ветку не переключали. Прежние результаты 73 inventory tests / 858 full-suite tests относятся к предыдущему этапу; в этой задаче они не выдаются за вновь выполненный pytest run. Общий Code Review не повторялся.

### Стенд и методика

| Параметр | Фактическое значение |
|---|---|
| PostgreSQL | 17.4, image `postgres:17` |
| Изоляция | Новый disposable container `wms-inventory-acceptance-20261029`, только `127.0.0.1:55449` |
| HTTP | Настоящий Uvicorn/TCP на `127.0.0.1:18549`; отдельный memory probe — `:18550` |
| API | Текущий `api_router`, действующие DTO/services/repositories и error handlers; не прямые вызовы inventory service |
| Pool HTTP-приложения | min=5, max=20, command_timeout=60 с — как в текущем get_db_pool |
| Module flags | `INVENTORY_MODULE_ENABLED=true`, `INVENTORY_APPLY_ENABLED=false` |
| Timeout inventory write-команд | lock=5 с, statement=15 с; не увеличивались |
| Consumers | Отключены только в тестовом процессе; подключения к действующим очередям отсутствовали |
| FBS | Обычный legacy write-off без КИЗ, `FBS_VALIDATE_ASSEMBLY_TASKS=true`; реальные test assembly_task и stock |
| Диагностика | pg_stat_activity, pg_blocking_pids, pg_stat_statements, pg_stat_database, Docker resource samples |
| Хост | AMD Ryzen 7 4800H, 16 logical CPUs; Docker видит 14.99 ГиБ памяти; отдельная CPU/memory quota стенду не задавалась |

Набор WMS schema/functions/triggers восстановлен существующим `tests/conftest.py` из schema dumps 08.08.2026 и актуальных КИЗ/container/FBS migrations, затем применены normalisation 21.10 и inventory migration 22.10. Это не свежий production backup. Как и в существующей fixture, исключены несвязанный public.article FK и два public product triggers, definitions которых отсутствуют в dump. Ни одна миграция не применялась к production.

Миграции и seed выполнялись только при подготовке disposable БД. В measurement-фазе inventory использовал обычные транзакции реализации, без monkeypatch её SQL, искусственных задержек внутри service и изменения timeout. Для concurrency-count интервал между begin и finish намеренно оставлен открытым на 250 мс клиентом, чтобы в него гарантированно попадали обычные операции.

Основной прогон использовал HTTP-сервер и driver в одном Python process с реальным TCP. Поэтому дополнительно выполнен полный quiescent workflow с **отдельным процессом API**, чтобы отделить его память от нагрузки клиента. Production middleware, внешняя сеть, RabbitMQ delivery/ACK и фоновые consumers не моделировались как deployment environment.

## 3. Набор данных

В пределах одного тестового склада:

| Уровень | Количество |
|---|---:|
| Склад/root | 1 |
| Зоны | 10 |
| Стеллажи | 100 |
| Секции | 500 |
| Ярусы | 1 000 |
| Ячейки | 3 727 |
| **Всего** | **5 338** |

Остатки распределены по разным ветвям, а не собраны в одной зоне. Использованы 201 SKU, из них один предназначен для добавления найденного товара. Есть stock на root и на одном неактивном адресе; большинство адресов пустые, но входят в frozen scope.

Исходный stock: **1 105 положительных available loose rows**, **1 101 уникальная пара product/location**, **6 rows с непустыми batch**, **3 multi-row пары**. Active loose КИЗ — 2 в mixed pair, registered без holder — 4; контейнерные остатки не добавлялись, поскольку они исключены из Stage 1. Это синтетическая модель масштаба снимка production от 19 октября, а не копия его товаров/количеств.

История **44 026 movements** создана обычными receive/ship, без adjust. Исторические receive/ship распределены по парам с сохранением положительной capacity. FBS source получил запас для многократных реальных списаний; test assembly tasks созданы заранее.

## 4. Полный HTTP workflow

Выполнена последовательность:

1. Создание draft и PATCH его заголовка/области.
2. Preview 5 338 адресов; проверка положительных строк.
3. Start: snapshot, components, frozen locations, claims, начальная revision.
4. Получение scope и страниц items, в том числе offset=1000.
5. Добавление найденного SKU в пустой адрес: initial expected=0, actual=1.
6. Begin → progress → complete каждой из **1 102** строк; одна строка сначала пересчитана в 0.
7. Три предварительные reconciliation revisions; actual не преобразуется в automatic target.
8. Submit документа целиком, возврат одной строки на пересчёт, новая попытка, повторный submit.
9. Close without adjustment; проверка освобождения claims и окончательности статуса.
10. Чтение истории попыток, history и большой исходной revision через API.

Итог первого полного документа: `closed_without_adjustment`, version **3322**, current_revision **6**, **1 103 attempts** с сохранением старого counted-zero, **3 320 сохранённых команд**. Количество попыток больше числа строк из-за повторного пересчёта одной позиции.

### Измеренные HTTP-задержки заключительного основного прогона

Время включает HTTP response/body. p95 ниже — эмпирический порядок статистики данной небольшой серии, а не утверждённый production SLO. Для единичных операций указано одно измерение.

| Операция | N | Медиана, мс | p95 / максимум, мс |
|---|---:|---:|---:|
| Создание документа, разные scopes | 13 | 57.35 | 116.15 / 116.15 |
| Full preview | 1 | 846.34 | 846.34 |
| **Full start + snapshot** | 1 | **2 800.76** | **2 800.76** |
| GET frozen scope, 5 338 адресов | 1 | 26.19 | 26.19 |
| GET items, до 1000 строк/страница, смешанные scopes | 8 | 13.23 | 470.57 / 470.57 |
| Begin count, включая concurrency-count | 1 106 | 15.98 | 30.34 / 97.05 |
| Save progress | 1 106 | 12.69 | 24.19 / 36.04 |
| Complete count | 1 106 | 19.71 | 37.45 / 78.01 |
| **Full reconciliation** | 3 | **2 637.50** | **2 859.09 / 2 859.09** |
| Submit с новой reconciliation | 1 | 2 856.79 | 2 856.79 |
| Return to count | 1 | 212.35 | 212.35 |
| Повторный submit | 1 | 2 602.43 | 2 602.43 |
| Close, разные scopes | 13 | 13.40 | 31.24 / 31.24 |
| History, первая страница 1000 событий | 1 | 33.70 | 33.70 |
| GET revisions, **limit=1** | 1 | 486.40 | 486.40 |
| Full start при работающих writers | 1 | 2 294.17 | 2 294.17 |
| Reconciliation при работающих writers | 1 | 2 875.27 | 2 875.27 |

Автоматизированные 1 102 строки заняли **60.688 с**. В отдельном server-process прогоне — **67.226 с**. Эти числа характеризуют backend/API throughput, не длительность реальной инвентаризации сотрудником.

В предыдущем прогоне того же масштаба full start занял 2.615 с, reconciliation — максимум 2.742 с; его основной workflow тоже завершился, но дополнительный seed evidence-limit не завершился. Для итогового вывода используется заключительный полностью завершённый прогон, а не замалчивается поздняя ошибка раннего стенда.

## 5. Частичные инвентаризации и ошибки API

| Проверка | Фактический результат |
|---|---|
| Несколько exact адресов | 3 locations, 3 items; start/close успешны |
| Зона с descendants | 534 locations, 101 items, включая саму зону и пустые адреса |
| Один SKU по всему складу | 5 338 locations/claims, 6 items; не только адреса с этим SKU |
| Два SKU в двух зонах | 1 068 locations, 2 items; пересечение фильтров |
| Partial start, 4 варианта | Медиана 262.09 мс, максимум 514.24 мс |
| Два непересекающихся документа | Оба concurrent start — 200; максимум 388.40 мс |
| Пересекающиеся зоны с разными SKU | Ровно один 200 и один 409 `INVENTORY_SCOPE_OVERLAP`; у проигравшего нет frozen locations |
| Close после start | Claims освобождены; последующие документы запускаются |
| B.1, корректное дерево | Два preview успешны |
| B.1, перенос родителя после preview | Обе стороны — missing child / wrongly included child — дают 409 `INVENTORY_HIERARCHY_CONFLICT` при start; scope не сохранён |
| B.2, ID/offset > bigint | По одному реальному HTTP запросу для path/query/body: 422, не 500 |
| Точная повторная команда с тем же UUID/body | Возвращён тот же сохранённый результат begin, без второй попытки |
| Тот же UUID с другим body | 409 idempotency conflict |
| Устаревшая expected_version | 409 `INVENTORY_VERSION_CONFLICT` |
| Submit неполного документа | 409, pending_approval не установлен |
| Return не из pending_approval | 409 |
| Изменение закрытого документа | 409 |
| POST предполагаемого apply URL | 404: такого endpoint нет |
| OpenAPI/Swagger | `/openapi.json` и `/docs` доступны; **19 inventory operations** |

JSON OpenAPI — 507 594 байта для всего router. Эта проверка подтверждает регистрацию операций, но не заменяет браузерный тест UI.

## 6. Параллельные складские операции и блокировки

Три независимых HTTP writer loops работали до запуска инвентаризации и затем во время start/snapshot, count, reconciliation и close. Периоды — baseline 6.02 с и четыре периода примерно по 5 с. Это порядка 61 writer HTTP request/с в среднем за всю серию, с максимумом четырёх одновременно активных HTTP flows вместе с inventory foreground.

| Период | Normal transfer cycles | КИЗ cycles | FBS shipments |
|---|---:|---:|---:|
| Baseline | 96 | 56 | 87 |
| Snapshot | 75 | 28 | 67 |
| Count | 81 | 48 | 72 |
| Reconciliation | 62 | 35 | 54 |
| Close | 80 | 47 | 71 |
| **Всего** | **394** | **214** | **351** |

Один normal cycle — атомарный batch transfer туда/обратно (2 movements). Один КИЗ-cycle — assignment, identified transfer туда/обратно (2 movements), deactivation. FBS — обычное ship с проверкой и изменением `assembly_task.is_shipped` и success/movement_id links. FBS входил через HTTP adapter (`source=http_api`) в действующий общий legacy processing flow; RabbitMQ consumer/ACK/retry как транспорт не запускался. Поэтому результат нельзя называть тестом доставки сообщений из реальной очереди.

В каждом периоде writers завершались успешно, без 4xx/5xx и без потерянных частичных cycles. Всего создано **788 + 428 + 351 = 1 567** ожидаемых movements внешних операций. Установлено точное соответствие этого числа приросту журнала. Все 214 новых КИЗ в конце deactivated; исходные 2 active сохранены.

### Задержки writers: baseline и работа inventory

| Writer | Baseline p95 / max, мс | Snapshot p95 / max, мс | Reconciliation p95 / max, мс |
|---|---:|---:|---:|
| Normal movements | 19.08 / 37.65 | 31.77 / 123.51 | 22.91 / 470.21 |
| КИЗ assignment/deactivation | 17.57 / 32.48 | 19.76 / 22.29 | 19.39 / 140.84 |
| **КИЗ transfer** | **32.76 / 69.01** | **38.53 / 2 057.49** | **55.84 / 545.93** |
| FBS ship | 26.44 / 53.43 | 28.07 / 191.19 | 76.18 / 453.23 |

Во время count/close максимум КИЗ-transfer не превысил 56.92/29.75 мс соответственно. Увеличение HTTP latency при reconciliation нельзя целиком приписывать DB locks: основной стенд делит Python event loop между сервером и driver, и на этой фазе lock waits не наблюдались.

### Что реально блокировалось

При snapshot PostgreSQL зарегистрировал **34 lock-wait samples**; ожидающий запрос:

```sql
SELECT location_id FROM wms.locations
WHERE location_id = ANY($1::bigint[])
ORDER BY location_id FOR UPDATE;
```

В заключительном прогоне PID 166 ожидал transactionid с blocker PID 182; максимальная наблюдённая длительность ожидающего SQL — **2.005 с**, HTTP — **2.057 с**. В предыдущем прогоне — 2.368 с SQL / 2.397 с HTTP. Это согласуется с FK KEY SHARE на materialized locations, удерживаемым start transaction, и несовместимым `FOR UPDATE` старого КИЗ-transfer. Другие writers в нагрузке использовали другие source locations.

Физические inventory/КИЗ rows новый модуль явно не блокирует, однако FK locks на locations остаются настоящими блокировками. Claims не являются запретом движений, но во время большой транзакции start несколько секунд ожидания возможно. `lock_timeout=5s` нового модуля **не является глобальным пределом ожидания старых writers**.

В baseline, count, reconciliation и close lock waits монитором не обнаружены. Это наблюдение выборки, не доказательство отсутствия любого миллисекундного ожидания. `pg_stat_database.deadlocks=0` в заключительной case DB.

Старый assignment/transfer deadlock не исправлялся. КИЗ-cycle здесь выполняет assignment и transfer последовательно; тест **не доказывает** отсутствие гонки между двумя независимыми КИЗ writers на одной capacity. Прежнее воспроизведение на исходной версии остаётся актуальным; вывод о статистическом уменьшении/увеличении вероятности deadlock по этой серии не делается.

### Реакция на изменения пересчитываемой пары

В заключительном прогоне пересчитывались именно пары normal transfer, КИЗ и FBS, а не посторонние строки. Все три получили `requires_recount=true`, `state_changed_during_count` и `state_changed_after_count`.

| Пара | Initial expected | Actual сохранён | Current expected |
|---|---:|---:|---:|
| КИЗ mixed pair | 98 | 98 | 98 |
| Normal transfer туда/обратно | 100 | 100 | 100 |
| FBS source | 5 010 | 5 010 | 4 871 |

Совпадение начального и текущего агрегата не скрыло промежуточные transfer/КИЗ события. В FBS actual не был автоматически уменьшен. Всего ambiguous lines — 14, recount-required — 3. У всех `safe_for_apply=false`. Физическое время отгрузки не устанавливалось и не приравнивалось к created_at.

## 7. Транзакции, SQL и ресурсы

### Длительности транзакций

Из pg_stat_activity снимались возраст xact/query, wait event и blocking PIDs с номинальным интервалом 50 мс. Это **максимумы наблюдаемых возрастов**, не точный transaction trace и не p95 длительностей всех транзакций.

| Период | Максимальный наблюдённый возраст transaction |
|---|---:|
| Quiescent full workflow, включая start/reconciliation | 2.831 с |
| Частичные документы | 0.628 с |
| Writers baseline | 0.040 с |
| Snapshot с writers | 2.246 с |
| Count с writers | 0.054 с |
| Reconciliation с writers | 2.770 с |
| Close с writers | 0.166 с |

Дополнительный независимый observer работал в отдельном процессе: 716 samples, максимальный фактический интервал 33.6 мс; он наблюдал module transaction age до **2.779 с** и выполнил quantity oracle. Он подключился уже после snapshot-фазы и потому не используется для утверждения «snapshot lock waits отсутствуют».

Полный автоматизированный count длится около минуты, но это **не минутная transaction**: begin/progress/complete — отдельные короткие команды. Общая inventory transaction не имеет wall-clock deadline 15 с: statement_timeout ограничивает отдельный statement. Нельзя переносить полученные 2–3 с на произвольно большой ledger или медленный backend.

### SQL timings из pg_stat_statements

Все значения — серверное исполнение; сетевой обмен и Python JSON processing в них не входят. Статистика собрана для заключительной case DB; диагностический size query отделён от прикладных.

| Запрос/группа | Calls | Mean, мс | Max, мс |
|---|---:|---:|---:|
| Document + filters + counts | 10 103 | 0.736 | 3.716 |
| GET_MOVEMENTS evidence | 2 235 | 1.686 | **686.846** |
| ALL_ITEMS с последними attempts | 10 | 178.551 | 441.806 |
| LIST_ITEMS page | 8 | 106.612 | 423.346 |
| GET_STOCK | 2 235 | 0.132 | 40.401 |
| INSERT revision JSON | 15 | 47.759 | 95.548 |
| GET initial revision | 7 | 104.005 | 139.747 |
| INSERT saved command result | 3 362 | 0.286 | 3.516 |
| INSERT frozen location | 25 093 | 0.028 | 2.192 |
| Module document FOR UPDATE | 3 357 | 0.030 | 0.109 |

Измеренные inventory SQL не подошли к 15-секундному statement timeout. Для GET_MOVEMENTS отмечено **1 291 temp blocks written**: большая evidence-сортировка уже требует временных файлов при штатных settings. Диагностический подсчёт размеров JSON занял 919.5 мс и не является задержкой inventory endpoint.

pg_stat_database: 22 469 commits, 9 rollbacks, 0 deadlocks, 10 551 296 temp bytes. Девять rollbacks соответствуют ожидаемым негативным сценариям, а не частично применённым документам. Счётчики включают setup/diagnostics, поэтому не объявляются числом business-команд модуля. В измерениях преобладал тёплый cache; cold-disk нагрузка не проверялась.

### Размеры одного завершённого full документа

| Данные | Количество | Измеренный объём |
|---|---:|---:|
| Revisions | 6 | sum(pg_column_size(evidence)) = **4 450 282 B** |
| Attempts | 1 103 | sum(pg_column_size(evidence)) = **2 157 092 B** |
| Saved command results | 3 320 | sum(pg_column_size(result)) = **6 227 883 B** |
| Максимальная логическая revision JSON | — | **15 257 699 B** |
| HTTP GET первой revision, limit=1 | — | **13 701 554 B**, без HTTP compression |
| Все 11 module tables с indexes/TOAST после одного full workflow | — | **20 307 968 B ≈ 19.37 МиБ** |
| Все module tables после заключительных сценариев | — | **26 722 304 B ≈ 25.48 МиБ** |

Stored/TOAST объём и логический JSON — разные величины. Пагинация revisions ограничивает количество revisions, а не размер evidence. Запрос большого limit способен вернуть десятки/сотни мегабайт; это не проверялось как безопасный предел. UI/pilot следует читать revisions с малым limit. Успешный limit=1 не доказывает безопасность максимального page size.

### Память и CPU

Общий process основного стенда достиг RSS **1 725 688 КиБ**. В нём одновременно находились server, driver, JSON fingerprints и decoded responses; выдавать это число за backend RSS было бы неверно.

Дополнительный probe разместил API в отдельном процессе и повторил весь quiescent workflow с теми же 5 338/1 105/44 026. Его **1 121 assertions прошли**, время count — 67.226 с, peak RSS server process — **250 424 КиБ (244.6 МиБ)**. Старые attempts и physical hashes сохранились. Это измерение одного worker без production consumers и не sizing нескольких workers или режима около evidence cap.

Docker PostgreSQL cgroup peak за все прогоны — **597 606 400 B ≈ 569.9 МиБ**, включая повторные setup. Во время основного count выборка Docker показывала CPU 44.4% одного логического CPU и 167 МиБ memory; во время bulk seed — около 104.7% CPU и 287.8 МиБ memory. Это отдельные samples, не средние за весь сценарий и не суммарная стоимость API+DB. Cold cache, WAL/checkpoint storm, длительная работа и memory quota production не моделировались.

## 8. Evidence limit и незавершившиеся подготовительные попытки

После основных сценариев история **того же склада** доведена до **100 003 movements**. Inventory start выполнялся обычным атомарным endpoint, не пакетами и не с повышенными timeout.

Фактический ответ за **1.583 с**:

```json
{"detail":"Evidence limit exceeded; select a smaller scope","error_code":"INVENTORY_EVIDENCE_LIMIT","diagnostics":null}
```

Статус 409; frozen scope нового документа пуст; глобальных claims нет. Draft можно закрыть без применения. Это штатный fail-closed, а не поддержка полной инвентаризации склада с любой глубиной истории.

**Эксплуатационное ограничение:** full inventory прекратит запускаться, когда релевантная история scope превысит cap, даже если locations и physical stock не выросли. SKU/location partial scope может помочь, но не заменяет full inventory. Дата достижения лимита на production не оценивалась; фактический runtime ledger 29 октября не проверялся.

Две подготовительные проблемы не скрываются:

1. Первый запуск стенда остановился **до HTTP workflow** из-за локального environment socks proxy, не поддержанного httpx. Только тестовый localhost client переведён на `trust_env=False`; WMS config не менялась.
2. Следующий прогон успешно выполнил full/partial/concurrent workflow, но **один seed SQL** для дополнительного cap-теста, многократно обновлявший одну hot-row, получил TimeoutError на штатных 60 с. Это подготовка истории, не inventory endpoint. Прогон целиком не объявлялся успешным. Seed распределён по парам и разбит на запросы по 5 000 событий; timeout не увеличен. Заключительный прогон заново выполнил все сценарии и сам cap-тест успешно. Наблюдаемые 9.49 с transaction age в фазе evidence limit включают этот пакетный setup transaction, а не 9.49 с обработки inventory start.

## 9. Подтверждение границ Stage 1

В quiescent full и partial workflows до/после сравнивались SHA-256 канонических JSON представлений **всех rows** inventory, movements, registry и kiz, включая timestamps. Хеши совпали; следовательно, успешные count/reconcile/submit/return/close не маскировали даже изменений updated_at physical stock.

При параллельных writers физические изменения, наоборот, ожидаемы и разрешены. Поэтому глобальное равенство хешей там было бы неправильным критерием. Проверено:

- прирост журнала точно равен 1 567 ожидаемым movements writers;
- `movement_type='adjust'` отсутствует во всём тестовом наборе;
- КИЗ lifecycle/count соответствуют внешним КИЗ cycles;
- независимый read-only oracle сопоставил current quantities каждой пары со stock components начального full snapshot минус **только внешние FBS quantities**. **Несоответствий — 0**;
- исходная revision первого full документа повторно получена через HTTP после writers; SHA-256 evidence совпал с первоначальным;
- первые две attempts повторно читались, counted-zero и последующий recount сохранились;
- все reconciliation lines имеют `safe_for_apply=false`, документы не получают applied/approved, apply URL отсутствует.

Обычные receive/ship/transfer для подготовки данных и имитации склада не являются inventory adjustments. Изменения физического состояния в этих фазах не приписываются новому модулю.

## 10. Достаточность производительности и условия внедрения

**Для проверенного текущего масштаба производительность достаточна:** старт/reconciliation занимают примерно 2–3 с, обычные count-команды — десятки миллисекунд, DB statements имеют большой запас до 15 с. Даже при работающих writers основной workflow не ломает целостность и не создаёт физических корректировок.

До включения Stage 1 на production необходимо:

1. Зафиксировать конкретный commit с исправлениями B.1/B.2: протестированная версия включает dirty diff, а не только HEAD.
2. Применить подготовленную additive migration владельцем БД по deployment journal; эта приёмка её production apply не выполняла. Проверить grants и сначала развернуть с flags=false.
3. Проверить реальное число evidence rows предполагаемого full scope: оно должно укладываться в cap. Числа production-снимка 19 октября не являются актуальным preflight 29 октября.
4. Сверить memory limit/число workers/фоновую нагрузку с измеренным server-only RSS, оставив запас. В частности, 256-МиБ quota почти не оставляет headroom даже для измеренного отдельного worker; её безопасность не подтверждена.
5. Согласовать наблюдённую задержку КИЗ-transfer до примерно 2–3 с при start; включать full starts сначала в контролируемом окне. Нет необходимости глобально останавливать RabbitMQ или склад.
6. Включить только `INVENTORY_MODULE_ENABLED`, оставить `INVENTORY_APPLY_ENABLED=false`, провести pilot документа и наблюдать реальные latency/locks/memory.

Не покрыты endurance на днях работы, сотни revisions одного документа, массовый multi-user count на одном header/version, максимальная пагинация evidence, production network/consumers, physical FBS timing и все расписания старого КИЗ-deadlock. Это границы приёмки, не скрытые гарантии.

Для Stage 2 пригодны документ, frozen scope, attempts/history и preliminary revisions. Но физический count boundary, TOCTOU-safe approval, точные source-specific adjust effects, batch allocation и FBS/КИЗ неоднозначности остаются задачами Stage 2. Успешные нагрузочные тесты не превращают preliminary reconciliation в безопасный physical target.

## 11. Артефакты и воспроизводимость

Временные скрипты и raw results размещены **вне рабочего дерева**:

- `/tmp/wms-inventory-acceptance-384wMa/run_acceptance.py` — основной HTTP workload;
- `/tmp/wms-inventory-acceptance-384wMa/results_final.json` — итоговые timings, checks, blockers, SQL stats и размеры;
- `/tmp/wms-inventory-acceptance-384wMa/memory_server.py`, `memory_probe.py`, `memory_results.json` — отдельный server-only probe;
- `results.json` — предыдущий прогон, остановленный на подготовке cap-истории; не смешан с финальным.

Скрипты используют существующие schema fixtures, но не меняют их. Перед воспроизведением требуются **явные disposable localhost** DB settings и KIZ_TEST_DSN; пароли/credentialed DSN в отчёт не включены. Loopback HTTP не использует environment proxy.

Все measurements выше сохранены в отчёте, поскольку /tmp не является долговременным хранилищем. Тестовые case/template БД удалялись fixtures. После приёмки identity контейнера повторно проверена; остановлен только созданный контейнер `83c031ec72a48553fe728f9d0f43dd9b599a474fc740b4ef5e6c3f7caab88b47`, disposable данные удалены через `--rm`. Production и другие контейнеры не затрагивались.
