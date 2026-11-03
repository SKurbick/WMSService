# Inventory Stage 2A — production preflight и deployment rehearsal

Дата: 02.11.2026. Это диагностический отчёт, не выполненный production deployment.

## 1. Вердикт

**NO-GO для production pilot на текущем этапе.** Сам SQL deployment path совместим с
проверенной действующей схемой; четыре миграции и основные physical workflows прошли
на свежей schema-only копии production. Остались конкретные условия допуска:

1. Утвердить immutable release artifact и разобраться с тремя отсутствующими в inventory
   ветке commits базовой КИЗ-ветки. Проверенный рабочий каталог не равен одному HEAD.
2. Подтвердить версии и конфигурации **всех** фактически развёрнутых API/FBS/retry процессов.
   Старый binary при active freeze блокирует ФБС: это воспроизведено на disposable.
3. Утвердить полный roster runtime roles и политику новых ACL с учётом production default
   privileges. Scoped helper проверяет наличие прав, но не удаляет избыточные default grants.
4. Определить назначение `manager51`: эта роль имеет INSERT на movements, но не имеет прав
   nextval на старую sequence. Если это настоящий runtime writer, prerequisite нужно закрыть
   отдельным согласованным изменением. Если не writer — явно классифицировать/исключить из
   operational roster; существующие default grants всё равно требуют рассмотрения.
5. Принять операционный протокол малого partial-пилота: завершение физических действий,
   текущий final count под freeze, отсутствие delayed FBS источников в выбранных адресах,
   ответственный за release/recovery и наблюдение первого apply.

Отсутствие partitions 2027 — подтверждённый эксплуатационный риск. Само по себе оно не
блокирует ноябрьский пилот, но блокирует запись с timestamps после текущей границы года.
До перехода через эту границу нужен отдельный утверждённый план partitions; здесь DDL в
production не выполнялся.

## 2. Проверенная версия

- Ветка: `feature/inventory-stage1`.
- HEAD: `8aa94e40e868f41084537fd68016dc988cd59a26`.
- Текущий tip базовой `feature/kiz-fbs-integration`: `fc0662a6e2bd2213123d291c434a5db6fdddefe3`.
- Общий предок: `404c55edec0d2da1dbdf3bb30e9745fe5ec8ec83` (`404c55e`).
- В рабочем дереве **39** исходных modified/untracked paths относительно HEAD; полный
  список с SHA-256 приведён в разделе 10. Исходные изменения не перезаписывались.
- Source manifest охватывает **370** tracked/untracked файлов, включая dirty/untracked
  реализации и тесты. SHA-256 канонического JSON `{path: file_sha256}`:
  `752dd14373a190e2aec0200ab3ae7cdeb81e7dda4e42c7be1b482ec457405cbe`.
- Этот создаваемый отчёт исключён из manifest, чтобы избежать самоссылки. Ignored runtime
  `.env`/секреты не входят в исходный snapshot и не выводились. Конфигурация rehearsal
  задавалась отдельно, с localhost DB/RabbitMQ и тестовыми credentials.

В inventory ветке отсутствуют три commits текущей базовой ветки:

| Commit | Содержание |
|---|---|
| `5380629` | Автоматическая регистрация КИЗ независимо от поступления |
| `07daa45` | Частичный импорт КИЗ и аудит точечных коррекций товаров |
| `fc0662a` | Оборот склада и сводка операций/history |

Это установлено `git log HEAD..feature/kiz-fbs-integration`, не догадка о production binary.
Обратный список содержит Stage 1 commit `8aa94e4`. Изменения базовой ветки относительно
общего предка затрагивают 41 файл, в том числе consumer, KIZ import и history. Какая версия
реально deployed, по подключению к БД установить нельзя. Автоматический merge/rebase,
checkout, reset и commit не выполнялись. Deploy только `8aa94e4` без untracked Stage 2A
файлов не соответствует проверенному составу.

### Контрольные суммы миграций

| Файл | SHA-256 |
|---|---|
| `20261022_add_inventory_documents.sql` | `f2218f389a5958a283856aef6cbd28097910b252ab328e2d04db4c627c30367c` |
| `20261029_add_inventory_application_preparations.sql` | `f61776feded4d68c7751ef8f55e4782ffb03d6bbc2f08d0ff7949826fa69353d` |
| `20261030_add_inventory_location_freeze.sql` | `6e518e31e6a587cd1a0465a1c23ab192a0f9428f2bc1dad174527c6bfe68613a` |
| `20261030_add_inventory_physical_apply.sql` | `3f7fdd327199739edf36d39bc2c0ac6b6b528a4b57a6b95b7c14f27edf4a4ac1` |

Полный manifest и сырой status сохранены в `/tmp/wms-stage2a-readiness/version.json`.
Это локальный evidence artifact, не коммит и не утверждённый release package.

## 3. Production: что действительно проверено

**Подтверждено действующей БД.** Подключение к `vector_db`, PostgreSQL **17.4**, роль
`vector_admin` (superuser). Отдельная read-only login role не была предоставлена.
Ограничение обеспечивалось `default_transaction_read_only=on` при подключении и явными
read-only транзакциями, `statement_timeout=10s`, `lock_timeout=1s`. Контрольная отметка
основной metadata-сессии: **2026-11-02 09:54:01.501988 UTC**; `transaction_read_only=on`.
Последующие диагностические statements имеют свои snapshots, склад не останавливался.

Не выполнялись production DML/DDL/GRANT, миграции, физические тесты, maintenance, refresh
views, изменения RabbitMQ/flags. Не вызывались неизвестные прикладные функции. Исполнялся
проверенный анонимный metadata block из projection preflight; только системные каталоги и
built-ins. LTREE extension обнаружена по metadata, прикладные tree functions не вызывались.

| Проверка | Актуальный результат | Вывод |
|---|---|---|
| Projection body | Trimmed MD5 `59757ad997db9dd81274bd13dadc9e4f`; prepared B.1 preflight **passed** | Поддержан исправленной migration |
| Projection attributes | `plpgsql`, `RETURNS trigger`, 0 args, VOLATILE, PARALLEL UNSAFE, SECURITY INVOKER, не LEAKPROOF, proconfig NULL | Атрибуты соответствуют contract |
| Владение | Projection и WMS tables принадлежат `vector_admin` | В production применим именно его default ACL policy |
| Movements triggers | Enabled origin: `trg_guard_controlled_container_movement`, `trg_register_movement_identity`, `trg_update_inventory_from_movement` | Существующий guarded projection/registry path активен |
| Inventory triggers | Enabled: `trg_inventory_updated_at`, `trg_kiz_inventory_guard` | КИЗ-защита не отсутствует |
| КИЗ triggers | Enabled: final holder integrity, identity guard, organization immutable | Текущий lifecycle contract сохранён в свежем dump |
| Registry | Имеются table, coordinate uniqueness/FK и registration trigger; выборка 1 000 недавних movements: **0** missing registry rows | Это адресная проверка, не полный ledger audit |
| Inventory module objects | `inventory_documents`, preparations, freezes, applications, fences **отсутствуют** | Проверенная схема требует fresh installation четырёх migrations |
| Privilege helper | `wms.inventory_runtime_privileges(text)` отсутствует | Production privileges preflight файла после установки пока неприменим; неизвестная функция не вызывалась |
| Partitions | 12 monthly leaves января–декабря 2026; default leaf не обнаружена | Ноябрь/декабрь покрыты, 2027 не покрыт |
| 2027 | Ни одной attached partition с диапазоном 2027 | Это свежая проверка, не повтор утверждения аудита 19.10 |
| Механизм partitions | `pg_partman`/`pg_cron` отсутствуют среди extensions; catalog search прикладных partition-creator functions не нашёл | Внешний scheduler/DBA job не проверен, автоматическое создание не доказано |

Определения constraints/indexes/functions/trigger clones получены из системных каталогов.
127 WMS functions включают extension members; это не 127 отдельных бизнес-writers.

### Migration state и forward upgrade

В обнаруженной `public.schema_migrations` находятся три записи:

| filename | applied_at_db | checksum |
|---|---|---|
| `001_wb_stock_2.sql` | 2026-09-25 11:39:34.969310 UTC | NULL |
| `002_drop_api_only_columns.sql` | 2026-09-25 12:04:05.439677 UTC | NULL |
| `001_clean_slate.sql` | 2026-10-02 18:04:36.004964 UTC | `6d2a176d2ff1a5d5ca73d11ae28ff9aaf4062a968a6709f2406c9fa9ecf8a1f7` |

Эта таблица не подтверждает version/checksum WMS migrations. Найденные alembic/version
таблицы других схем не принимаются за WMS ledger. В репозитории WMS SQL применяются
вручную; полноценная история исполнения всех SQL из текущих каталогов не восстанавливается.

Для актуально установленной inventory schema **признаков старой Stage 2A нет**: новые
таблицы/helper отсутствуют, projection остаётся проверенным legacy body. Поэтому именно
этой БД не нужен forward upgrade существующих inventory applications; проверен fresh path.
Это не историческое доказательство, что SQL никогда не запускался и не откатывался.
Если на момент deployment объекты появятся либо target staging уже содержит старую Stage 2A,
остановиться: не повторять CREATE-файлы, сначала отдельный forward-upgrade review.

В rehearsal ledger **не заполнялся**: schema-only копия содержит пустую таблицу, после
четырёх SQL она также пустая. Результат исполнения фиксировался вне БД, не фиктивными
записями. Перед production нужен утверждённый deployment record с приведёнными checksums;
не записывать WMS filenames в чужой ledger без решения о его назначении.

### Evidence history и лимит Stage 1

Без тяжёлой полной исторической сверки выполнены bounded counts: `LIMIT 100001` внутри
подзапроса и COUNT снаружи. Поскольку все результаты меньше cap, это точные количества
для snapshot соответствующего statement, а не planner estimates.

| Источник | Число строк | Размер по metadata |
|---|---:|---:|
| `wms.inventory` | 1 156 | 614 400 bytes |
| `wms.movements` | 61 683 | 39 428 096 bytes по 12 leaves; parent size 0 |
| `wms.kiz` | 64 192 | 64 806 912 bytes |
| `wms.kiz_events` | 68 754 | 29 925 376 bytes |

Отдельно: **64 186** events с location_id NULL и **59 630** registered КИЗ без holder.
Эти последние два counts получены последующим statement, не объявляются общей атомарной
фотографией со всеми предыдущими числами. Количество registered не вычиталось из
произвольной локации и не интерпретировалось как физический остаток.

Cap 100 000 применяется **отдельно к каждому evidence array**, не к их сумме:
[inventory_document_repository.py:25](../../app/infrastructure/database/repositories/inventory_document_repository.py#L25).
На дату проверки глобальные верхние границы ещё ниже cap. Но GET_EVENTS включает
`location_id IS NULL`, а GET_KIZ — holderless registered; уменьшение числа локаций без
SKU-фильтра не исключает глобальную часть history:
[GET_EVENTS:129](../../app/infrastructure/database/queries/inventory_documents.py#L129) и
[GET_KIZ:110](../../app/infrastructure/database/queries/inventory_documents.py#L110).
Когда global NULL events достигнут cap, даже маленький location-only документ может
получить `INVENTORY_EVIDENCE_LIMIT`. Для пилота проверять выбранные SKU/scope заранее;
мониторить NULL history и не обещать, что одного уменьшения location scope достаточно.
Timeout/cap не увеличивались и алгоритм не менялся. Нагрузочный тест 60–70 тысяч строк
на staging не выполнялся: production data в rehearsal не переносились.

422 374 estimated FBS item rows / 199 090 176 bytes — отдельная таблица; это не число
movements и не прямой размер одного Stage 1 evidence array. Полный scan FBS history не
выполнялся.

## 4. Runtime roles и реальные privileges

Catalog query нашёл login roles с schema USAGE и INSERT на movements:

| Роль | Production superuser | Назначение в disposable roster | Baseline receive/transfer/adjust/ship/location до и после migrations |
|---|---|---|---|
| chernitskiy_sn | нет | writer | прошли |
| ermolenko_i | нет | writer | прошли |
| hermes_kostya | нет | writer | прошли |
| manager17 | нет | writer | прошли |
| manager51 | нет | writer candidate, требует классификации | прежний отказ nextval sequence до и после |
| vector_admin | да | inventory; clone принудительно NOSUPERUSER | прошли, включая новые API и apply |
| ziev_me | нет | writer | прошли |

Это **кандидаты по effective permissions**, не утверждение, что все семь — service
accounts WMS. В observed sessions встречались `vector_admin` и `ziev_me`, значительная
часть application_name пустая. Имена других приложений под общей vector_admin также
наблюдаются; это не позволяет атрибутировать каждую сессию API, consumer или retry worker.
Production process/environment inventory и image digests не предоставлены.

`manager51`: has_table_privilege INSERT=true, но sequence rights для
`wms.movements_movement_id_seq` — USAGE=false, UPDATE=false, SELECT=false. Поэтому обычный
INSERT с default nextval не работает в mirrored baseline. Это не regression нового
freeze и не проверялось записью в production. Дополнительных старых sequence grants этой
роли в rehearsal не выдавали ради зелёного результата.

### Что означает permission rehearsal

В disposable созданы отдельные LOGIN роли с теми же именами, **все NOSUPERUSER**, без
CREATEDB/CREATEROLE; schema owner — изолированный postgres. Это проверка реальных профилей
на shadow accounts, не изменение production role attributes и не копирование паролей.
Права унаследованных production групп не копировались широким GRANT ALL: catalog effective
rights разложены в подтверждённый узкий набор DML/sequence/helper grants на конкретные
старые objects. Нет CREATE/TRIGGER/TRUNCATE/schema-wide grants для рабочих smoke tests.

Новые ACL выданы только checked-in механизмом scoped opt-in, с профилями writer/inventory;
check-only `20261102_inventory_runtime_privileges_preflight.sql` прошёл на всех семи
объявленных shadow roles. Это проверка **нового delta contract**, не сертификат всех старых
прав и не оправдание отсутствующей legacy sequence у manager51.

При детализации старого контракта дополнительно подтверждены необходимые prerequisites:

- Activation service непосредственно INSERT-ит `wms.kiz_activation_authorizations`:
  [kiz_activation.py:74](../../app/infrastructure/database/queries/kiz_activation.py#L74).
  Для inventory shadow role добавлен только этот INSERT, не DML на все private auth tables.
- Maintenance берёт SHARE lock на movements и EXCLUSIVE на inventory:
  [system_repository.py:58](../../app/infrastructure/database/repositories/system_repository.py#L58).
  Одних SELECT/INSERT на ledger для SHARE lock недостаточно. Для maintenance actor
  использован scoped UPDATE на parent movements, уже входящий в effective production
  права этого account. Обычным другим writer shadows его ради тестов не добавляли.
- Старые EXECUTE controlled helpers, включая ship_kiz, и старые identity sequences
  являются отдельным legacy contract; новый inventory helper не выдаёт их автоматически.

Точные новые table/sequence/helper/column locking privileges описаны в
[deployment contract](../flows/inventory_stage2a_physical_apply.md#явный-runtime-role-roster-и-минимальные-grants).
Наличие новых objects и прав в production сейчас **не проверяется положительным результатом**:
objects отсутствуют. После deployment обязателен повторный check-only preflight с реальным
roster и верифицированным установленным helper.

### Production default ACL шире минимального профиля

У vector_admin в wms действуют defaults:

- manager51 получает SELECT/INSERT/UPDATE/DELETE на новые tables, но не получает default
  sequence privileges;
- `vector_db_all_schemas_prod_manage` получает расширенный набор table privileges, включая
  TRUNCATE, TRIGGER, REFERENCES, MAINTAIN, и USAGE/SELECT/UPDATE новых sequences.

Это отдельно воспроизведено **только на disposable metadata probe**, с remapping owner
на postgres; рабочие workflow tests не делались успешными за счёт этих широких defaults.
Новый probe table автоматически получил, например, DELETE для manager51 и TRUNCATE для
manage group. Значит, указание profile writer не гарантирует итоговый SELECT-only доступ
к documents, если дополнительные права приходят из defaults. Helper добавляет/проверяет
минимум и не делает REVOKE ранее полученных прав.

Это существующая политика БД, а не тайный GRANT из новых migrations. До пилота owner должен
одобрить итоговую матрицу либо согласовать ограничение ACL **на новых objects**/отдельного
migration owner с подходящими defaults. Не менять глобальные defaults или роли других
приложений автоматически. Каталогами нельзя доказать least privilege только на основании
успешного positive privilege preflight.

## 5. Как выполнен disposable rehearsal

Свежий schema-only dump production снят клиентом **pg_dump 17.4** через отдельный контейнер,
с READ ONLY PGOPTIONS, timeout 10s/lock 1s. Установленный системный pg_dump 16.15 не
использовался против сервера 17.4. Экспортированы public+wms, **без данных, owner и ACL**.

- Размер raw dump: **634 254 bytes**.
- SHA-256: `eb58888f7c2d8a37f48b5275ebc94e2d57d1a561c99e25ed888c9992e6574ecd`.
- Восстановлено 48 WMS tables/partitioned relations; public dependencies также сохранены.
- Namespaces и extensions предварительно созданы по production metadata; исключены
  только повторные CREATE SCHEMA public/wms. Line endings нормализованы при чтении SQL.
  Triggers не отключались; constraints не удалялись ради тестов.
- ltree 1.3, btree_gist 1.7, pgcrypto 1.3, pg_stat_statements 1.11 — из source metadata;
  тестовая PostgreSQL 17.4. pg_stat_statements workload/настройка preload не тестировались.
- Для product trigger создана синтетическая reference warehouse row id=1. Она нужна
  пустой schema-only копии; исходный product trigger не удалялся. Production reference
  data/товары/остатки/messages не переносились.
- Сравнены **126** старых WMS function bodies кроме projection: после нормализации CRLF
  **ни одного изменения**. Projection изменена только предусмотренным strict wrapper.
- Реальный организационный staging cluster не предоставлен; использованы disposable
  PostgreSQL на `127.0.0.1:55453`, RabbitMQ на `127.0.0.1:5679`, backend на localhost:8094.
  Никаких staging connections к production RabbitMQ.

### Порядок миграций и measured time

| SQL | Результат на свежей production schema | Время |
|---|---|---:|
| 20261022_add_inventory_documents.sql | passed | 0.042 s |
| 20261029_add_inventory_application_preparations.sql | passed | 0.005 s |
| 20261030_add_inventory_location_freeze.sql | passed | 0.023 s |
| 20261030_add_inventory_physical_apply.sql | passed | 0.016 s |

Перед physical migration выполнен projection preflight, roster/grant opt-in был явным.
После — read-only privilege preflight. Это времена маленькой синтетической копии, а не
обещание production latency, объёма backfill fences или отсутствия DDL lock waits.
Production data/конкурентный production workload не копировались.

### Функциональные результаты

| Проверка | Что фактически получено |
|---|---|
| Legacy SQL, flags=false | Шесть shadow accounts проходят receive/transfer/adjust/ship/location до и после; manager51 сохраняет тот же baseline permission failure |
| КИЗ и containers, flags=false | Runtime HTTP activation/deactivation, register, fill 4, extract 2: россыпь 8, contained 2, активный content 2; реальная projection проверена |
| Maintenance, flags=false | Runtime recalculate выполняется на согласованном synthetic ledger |
| Ordinary writers при freeze | Receive/transfer отклоняются P7601; КИЗ assignment, container extract и maintenance возвращают domain conflict, физическое состояние не меняется |
| Shortage | 10 → actual 8 → applied stock 8; один source-specific adjust и registry link |
| Surplus | 10 → actual 12.25 → stock 12.25; точный Decimal, один adjust/link |
| Без расхождения | Actual 10 → applied, 0 movements, проверки документа/claims выполнены |
| Whole-document rollback | Две строки; injected deferred error при commit откатывает movements/registry/application/links/status/release. После удаления fault тот же UUID успешно применяет весь документ |
| Release | Явный audited release без физической корректировки; повтор команды безопасен |
| Новый legacy FBS processor | При active freeze обычный NULL-source ship проходит и invalidates область; stock 10→9 |
| HTTP FBS | Реальный endpoint, shadow runtime account, invalidation и фактический расход |
| Multi-source КИЗ + retry | Первая попытка pending_retry без эффекта; после отражённого прихода в свободный fallback адрес runtime retry пишет две ship-записи, zone 10→9, fallback 2→1; freeze invalidated; duplicate не списывает повторно |
| Live standard/external consumers | Три сообщения через две локальные очереди: standard 2, external_detected 1. Два успеха и один existing duplicate_only outcome, всего два ship, stock 10→8, freeze invalidated |
| Duplicate outcome | Повторное сообщение имеет item status failed с task_resolution_status duplicate_only и сообщением «Нового списания нет…». Это существующий terminal contract, не freeze failure; нового расхода/бесконечного retry нет |
| Реальный backend restart | Три разных процесса uvicorn: ready freeze пережил stop/start, apply успешен, после следующего restart тот же UUID/body вернул исходный результат; application одна, stock 8 |
| Старый 404c55e + новая DB | Без freeze проходят receive/transfer/adjust и legacy FBS; при active freeze старый FBS ожидаемо получает P7601 — mixed-version режим запрещён |
| 2027 partition, parent INSERT | Только в disposable создан январь 2027, row/deferred triggers cloned, receive работает; под freeze отклоняется |
| 2027 direct leaf INSERT | При scoped INSERT на synthetic leaf receive изменяет stock 10→11; под freeze прямой leaf INSERT тоже отклоняется P7601 |

Основной API проверялся через HTTP; restart — реальными отдельными backend processes,
а не только заменой service object. КИЗ/container/movement/maintenance HTTP в остальных
cases использовали ASGI transport с настоящим DB pool, без mock PostgreSQL.

Standard/external consumer functions запускались реально, с локальным broker и test
messages: [consumer.py:79](../../app/consumer.py#L79). Retry выполнялся реальным
`process_pending_retries(runtime_pool)`, а не подменённым handler. Долговременный scheduler
loop/production network/broker cluster и production image packaging не проверены.

Первые попытки harness обнаружили отсутствующие reference data schema-only копии,
недостаточно точный subset старых grants, повтор synthetic location name, глобальные
счётчики ship с baseline данными и inherited SOCKS proxy для localhost. Эти условия
устранены **только во временных scripts/test fixtures**, не кодом WMS и не отключением
DB guards. Существующий duplicate_only terminal status учтён в проверке вместо ошибочного
ожидания трёх success. Итоговые результаты приведены выше; промежуточные неуспехи не
замаскированы как production дефекты.

966 passed / 181 inventory tests / 23 deployment tests — результат предыдущей задачи,
**полный suite в этом readiness исследовании не перезапускался**. Здесь проведён отдельный
rehearsal на текущем production DDL. Известные КИЗ-deadlocks не исправлялись и отсутствие
их в этих сценариях не объявляется доказательством устранения.

## 6. Блокирующие и оставшиеся ограничения

| Вопрос | Подтверждение | Условие закрытия |
|---|---|---|
| Не утверждён точный release относительно базовой ветки | Три upstream commits отсутствуют, 39 dirty/untracked paths | Явный выбор состава/интеграции, review artifact digest; при изменении кода повторить соответствующий rehearsal |
| Не установлен deployment roster всех FBS processes | DB sessions не дают binary/config version | Получить image/commit/config manifest API, standard/external consumers, manual HTTP paths, retry jobs; все должны поддерживать context до первого freeze |
| Источник delayed physical FBS/физическая граница | Из SQL нельзя узнать время физического изъятия | Операционный допуск обычных адресов, единый FBS_LOCATION_CODE, final count после подтверждения окна; FBS-зона/active КИЗ и неоднозначные batches остаются исключены |
| Избыточные default ACL | Production catalog + isolated metadata reproduction | Owner approval конкретных effective grants новых objects или отдельный согласованный scoped ACL plan |
| manager51 | Старый sequence privilege отсутствует; отказ до/после | Установить назначение; если writer — отдельное согласованное закрытие baseline prerequisite, не маскировка scoped helper |
| Полнота migration history | WMS filenames/checksums не отражены в найденном ledger | Подтвердить installation checklist и вести утверждённый deploy record, не подделывать чужой ledger |
| Partitions 2027 | Attached leaves отсутствуют | Создать/проверить отдельной DBA процедурой до 2027 boundary; нет разрешения выполнять production DDL сейчас |
| Рост evidence | NULL events уже 64 186, holderless registered 59 630 | Monitoring и preview выбранного scope/SKU; cap не увеличивать в этом deployment |
| Реальная нагрузка | Копия schema-only, синтетические данные | Staging наблюдение DDL locks, длительности count/ready/apply и задержек FBS; текущие SQL timeout не поднимать ради приёмки |

Не выявлено необходимости менять movement_type, переписывать исторический ledger,
исправлять production stock или запускать recalculate для установки модуля.

## 7. Рекомендуемый production deployment — только после отдельного разрешения

1. Согласовать immutable исходную версию и судьбу трёх upstream commits. Не заменять более
   новую production функциональность непроверенной старой веткой. После любых merge/cherry-pick
   повторить изменившиеся проверки, сохранить новый manifest/migration hashes.
2. Получить process manifest всех API/consumers/retries/maintenance/external writers:
   image/commit, реальный DB account, FBS source config, queue identities, schedule.
   Сопоставить семь DB candidates с фактическими обязанностями. Секреты в отчёт не включать.
3. Утвердить owner/roles/ACL план. Учесть существующие defaults и старые prerequisites,
   включая sequences, activation authorization и maintenance locks. Никаких superuser
   требований к runtime; выбор production учётной записи — отдельное решение владельца.
4. В короткое согласованное migration window повторить read-only catalog preflight и
   B.1 projection preflight. Если body/attributes или installation state изменились — STOP.
   Сохранить pg_get_functiondef/ACL/constraints/partition metadata для сравнения/rollback.
5. Подтвердить MODULE/FREEZE/APPLY=false на всех replicas и отсутствие старых active
   inventory freezes. На проверенной текущей БД новых объектов ещё нет; не считать это
   вечной гарантией к моменту deployment.
6. В owner connection с ON_ERROR_STOP и явным полным roster выполнить только неприменённые
   migrations 22.10 → 29.10 → freeze 30.10 → physical 30.10, согласно
   [runbook](../flows/inventory_stage2a_physical_apply.md#порядок-ручного-deployment-rehearsal).
   Scoped grant opt-in — только явное действие утверждённого оператора. Каждая migration
   имеет собственный BEGIN/COMMIT; не создавать ложную общую внешнюю транзакцию и не
   продолжать файл после SQL failure. При ошибке ROLLBACK и анализ.
7. После согласованного ACL шага проверить **effective** grants, а не только отсутствие
   ошибок GRANT; выполнить check-only privileges preflight с grant mode off. Сверить
   installed wrapper, trigger clones/enabled state, constraints/indexes, registry links
   и актуальную месячную partition. Не считать проверку minimum rights аудитом excess ACL.
8. При flags=false выполнить ограниченный smoke существующих операций под runtime roles
   на согласованных тестовых сущностях staging. Production smoke с записями допускается
   только по отдельной явной процедуре/разрешению, не как часть этого read-only аудита.
9. Обновить **все** API/FBS/retry processes до совместимого artifact. Проверить, что старых
   instances и старых in-flight transactions не осталось. Без этого не включать FREEZE.
10. MODULE=true → затем FREEZE=true при APPLY=false: контролируемый partial dry run,
    check conflicts, confirm/final count, release. ФБС не останавливать и RabbitMQ не
    замораживать. Никаких автоматических freeze всего склада.
11. После отдельного pilot допуска выбрать небольшой scope обычных адресов, пройти
    count/ready, отдельно разрешить APPLY=true, применить документ целиком, сверить
    quantities/source/registry/application identity, claims/fences/history и retry behaviour.
    Физическое подтверждение оператора не объявлять техническим доказательством неизвестных
    действий. При invalidation урегулировать и пересчитать, не применять старый count.
12. Наблюдать application/FBS failures, lock timeouts, evidence growth и partitions;
    заранее реализовать утверждённый план 2027, не дожидаясь первой failed shipment.

Этот список — предложение оператору. Ни один production deployment/grant/flag шаг агент
не выполнял. Read-only каталогами нельзя выдать разрешение на физический pilot без
процессного и операционного допуска.

## 8. Безопасное отключение

1. Запретить новые apply (`APPLY=false`), дождаться/проверить уже начатые транзакции:
   изменение flag не отменяет текущий SQL commit.
2. Запретить новые freeze (`FREEZE=false`), сохранить новую версию API/FBS/retry и доступ
   MODULE=true к status/release. Persistent fences сами не снимаются по timer/flag.
3. Проверить active freezes/invalidation/conflicting operations; явно release с reason,
   author и актуальными version/UUID. Claims закрывать штатным workflow документа.
4. Убедиться в отсутствии active fences и незавершённых apply; только затем отключать
   MODULE или откатывать binary. Старые FBS workers нельзя возвращать с active fences.
5. Не удалять applications/components/registry links, history и movements. Rollback
   приложения не откатывает физический остаток. Обратные корректировки — отдельный
   согласованный документ/процесс, а не удаление исторических строк.
6. Если частичная установка/неизвестная версия schema — остановить новые freeze/apply,
   сохранить диагностику и определить forward upgrade. Не восстанавливать архивный body
   поверх runtime и не изменять ledger для видимости успешного deployment.

## 9. Воспроизводимые read-only SQL

На production использовать отдельную read-only роль, если доступна. Иначе как минимум
session default_transaction_read_only=on плюс отдельная транзакция для каждой группы:

```sql
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='1s';
-- SELECT группы ниже
COMMIT;
```

При ошибке — ROLLBACK. Не объединять дальнейшие запросы с aborted transaction. Запросы
catalog metadata лёгкие; bounded data counts ограничены cap, но всё равно могут читать
страницы соответствующих relations. Полная ledger reconciliation не предлагается.

### Function, установка и constraints

```sql
SELECT current_database(),current_user,current_setting('transaction_read_only'),version();
SELECT pg_get_functiondef(to_regprocedure('wms.update_inventory_from_movement()'));
SELECT md5(btrim(p.prosrc,E' \t\r\n')) AS trimmed_md5,
       l.lanname,p.prorettype::regtype,p.pronargs,p.provolatile,p.proparallel,
       p.prosecdef,p.proleakproof,p.proconfig,pg_get_userbyid(p.proowner) AS owner
FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
WHERE p.oid=to_regprocedure('wms.update_inventory_from_movement()');
SELECT name,to_regclass('wms.'||name) AS installed
FROM unnest(ARRAY['inventory_documents','inventory_application_preparations',
                 'inventory_freezes','inventory_applications','inventory_location_fences']) name;
SELECT to_regprocedure('wms.inventory_runtime_privileges(text)');
SELECT c.conrelid::regclass,c.conname,c.convalidated,pg_get_constraintdef(c.oid)
FROM pg_constraint c WHERE c.connamespace='wms'::regnamespace;
SELECT c.relname,t.tgname,t.tgenabled,t.tgparentid,pg_get_triggerdef(t.oid)
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
WHERE c.relnamespace='wms'::regnamespace AND NOT t.tgisinternal;
```

Checked-in projection preflight выполнен на production как проверенный anonymous metadata
block внутри read-only transaction. Весь файл также имеет READ ONLY. Privileges preflight
на текущей production не вызывался: function отсутствует. После установки сначала сравнить
его определение с checked-in helper; затем запускать prepared read-only файл с явным roster
и local grant mode off. Не выполнять произвольную установленную функцию «для проверки»,
не просмотрев её body. Проверка presence/helper name не заменяет B.1 contract.

### Roles, старые sequences, defaults

```sql
SELECT r.rolname,r.rolsuper,r.rolinherit,r.rolcanlogin,
       has_schema_privilege(r.oid,'wms','USAGE') AS schema_usage,
       has_table_privilege(r.oid,'wms.movements','INSERT') AS movement_insert,
       has_table_privilege(r.oid,'wms.inventory','UPDATE') AS inventory_update,
       has_sequence_privilege(r.oid,'wms.movements_movement_id_seq','USAGE') AS seq_usage,
       has_sequence_privilege(r.oid,'wms.movements_movement_id_seq','UPDATE') AS seq_update
FROM pg_roles r WHERE r.rolcanlogin ORDER BY r.rolname;
SELECT a.rolname AS granted_role,b.rolname AS member,m.admin_option
FROM pg_auth_members m JOIN pg_roles a ON a.oid=m.roleid JOIN pg_roles b ON b.oid=m.member;
SELECT pg_get_userbyid(d.defaclrole) AS owner,n.nspname,d.defaclobjtype,
       CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       a.privilege_type
FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) a;
SELECT usename,application_name,backend_type,state,count(*)
FROM pg_stat_activity WHERE datname=current_database()
GROUP BY usename,application_name,backend_type,state;
```

Никаких pg_authid/password queries. Для будущих новых objects сначала to_regclass /
to_regprocedure: отсутствие объекта нельзя подменить результатом has_*_privilege=true.

### Ledger, partitions, объём и адресная registry-проверка

```sql
SELECT table_schema,table_name FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog','information_schema')
AND (table_name ILIKE '%migrat%' OR table_name ILIKE '%alembic%' OR table_name ILIKE '%schema_version%');
-- Применимо после проверки columns именно найденного public.schema_migrations:
SELECT filename,applied_at_db,checksum FROM public.schema_migrations LIMIT 100;
SELECT p.relid::regclass,p.parentrelid::regclass,p.level,p.isleaf,
       pg_get_expr(c.relpartbound,c.oid) AS bounds,pg_get_userbyid(c.relowner) AS owner
FROM pg_partition_tree('wms.movements') p JOIN pg_class c ON c.oid=p.relid;
SELECT 'inventory' AS object,count(*) AS bounded FROM (SELECT inventory_id FROM wms.inventory LIMIT 100001) q
UNION ALL SELECT 'movements',count(*) FROM (SELECT movement_id FROM wms.movements LIMIT 100001) q
UNION ALL SELECT 'kiz',count(*) FROM (SELECT kiz_id FROM wms.kiz LIMIT 100001) q
UNION ALL SELECT 'kiz_events',count(*) FROM (SELECT kiz_event_id FROM wms.kiz_events LIMIT 100001) q;
SELECT count(*) AS locationless_events_bounded
FROM (SELECT kiz_event_id FROM wms.kiz_events WHERE location_id IS NULL LIMIT 100001) q;
SELECT count(*) AS holderless_registered_bounded
FROM (SELECT kiz_id FROM wms.kiz WHERE lifecycle_status='registered'
      AND location_id IS NULL AND container_id IS NULL LIMIT 100001) q;
WITH recent AS (
 SELECT movement_id,created_at FROM wms.movements WHERE created_at>=now()-interval '7 days'
 ORDER BY created_at DESC,movement_id DESC LIMIT 1000
)
SELECT count(*) AS sampled,count(*) FILTER(WHERE r.movement_ref IS NULL) AS missing_registry
FROM recent m LEFT JOIN wms.movement_registry r
ON r.movement_id=m.movement_id AND r.movement_created_at=m.created_at;
```

Результат bounded=100001 означает превышение лимита, не точный полный count. Не увеличивать
cap/timeout автоматически. Parent и leaf bounds проверять по metadata, а не одному имени
movements_2027_01; сейчас все фактические bounds заканчиваются на `2026-12-31 21:00 UTC`
(01.01.2027 00:00 Europe/Moscow). Нет покрытия правой границы и далее.

## 10. Dirty/untracked snapshot и локальные доказательства

`M` — изменённый tracked path, `??` — untracked на начало проверки. Список включает и
исходные unrelated audit/data files; они не выдаются за новые файлы этой задачи.

| Status | Path | SHA-256 содержимого |
|---|---|---|
| `M` | `app/api/v1/dependencies.py` | `b9df1da336c1afeaf6ac9791564d1ddd4840a1ec00297079d61c445f59e35974` |
| `M` | `app/api/v1/endpoints/inventory_documents.py` | `ba870de90edc3451f6e00d9b99c2d341a45f3dfaff9ff00191e8b46b36e913c3` |
| `M` | `app/core/schemas/inventory_documents.py` | `1f7c25caf1045801cc4ce20b6a20434d7ada9ae6b3f950c98e40d44e80afa4d4` |
| `M` | `app/core/services/inventory_document_service.py` | `c07e4b9e5c16f9987b94f86b14b162a2f86480853071c5ee3adc1f7a3f3c69ef` |
| `M` | `app/infrastructure/database/queries/inventory_documents.py` | `8396c237be4177d14c6676ec2b82638343f30b83c71d6f4ea6b6c03c23dfc9b0` |
| `M` | `app/infrastructure/database/repositories/fbs_kiz_repository.py` | `66d83d25d2717be8a89d519e66264cafd5e5d1de060e38fde11a181bb7cfecba` |
| `M` | `app/infrastructure/database/repositories/fbs_shipment_repository.py` | `b70ffa91e7af6e3c44993ef0f273f2573c33e7a7f976bc55d924921bcc7f8859` |
| `M` | `app/middleware/error_handler.py` | `d6866e1d7817d6cf77fd325011fef4f9c668737c64fcee0dcbe9466687013c29` |
| `M` | `app/shared/config.py` | `658e6ae9af7d2c74a7ee78f40800876377aadf11c63ab13a58c7469bf5620bed` |
| `M` | `docs/current/api_map.md` | `5497fe63d7f2c2fc5e752f96399a2850d021097142d5c6dc6af3372cd1919fdd` |
| `M` | `docs/current/business_rules.md` | `ba1f33111d0f5f838326a2f1d22823bad1375d4282a0ae17d161c921ca0551e7` |
| `M` | `docs/current/invariants.md` | `06598abab4c7de7d25bc32e5223ea96aeff2328289b81880d6574b3b020a0439` |
| `M` | `docs/current/open_questions.md` | `b559fb4ad6860ca2fc5b8cb0a3aa7c022ce28f96a34f7b02312159bf3e22cfaa` |
| `M` | `docs/decisions/decisions.md` | `3db37a91bfe7d7bacdcb72733208a0f2a96c184835d6222cc8958e0521cffbd7` |
| `M` | `docs/flows/inventory_stage1.md` | `5dc5d8bc7b3f0cd4caeea02f47be13eaf63fe1351d14aea3e10650f28a07fe78` |
| `M` | `scripts/migrations/README.md` | `32ff0a1f6ace27eaaf49eb62202a8c56670a8fad2442fb4ba5b62fd74c5a0911` |
| `M` | `tests/conftest.py` | `7f8c8955a5b24c31be6cd5c4b9fe6d7fdecfc2fe96c3f7cc6af0c1725a14f779` |
| `M` | `tests/test_inventory_documents_postgres.py` | `b9c8f1714bf1021ed91ef8b32e0a33aa991be460dc0d193c2844fce9ef803406` |
| `M` | `tests/test_inventory_documents_unit.py` | `f01619b4bff3b8c158d46e6f7def2b8da59fdf51b558d8f68fc91f0184a8d2fc` |
| `??` | `app/core/services/inventory_application_service.py` | `037f47d297897aec50aecc1d3d05c4f42589da4d08e1dcfc59af5cf975c7eae4` |
| `??` | `app/core/services/inventory_freeze_service.py` | `43083bcf939a26adee759fb7a3c181f8df09e2a3ca4f14ca814eb78ee25a718c` |
| `??` | `app/infrastructure/database/queries/inventory_applications.py` | `cc36b35853b245b06dd4158a44def34b43ee8f3555c8980a1d109646338d1558` |
| `??` | `app/infrastructure/database/queries/inventory_freeze.py` | `f722c1ab0fe4a5a08b9b58d3b3f472914bc6c2e61c7515698bfbf8906326c0ef` |
| `??` | `docs/audit/inventory_stage1_acceptance_2026-10-29.md` | `443a90baff098844d8b54af2982a3be382a7e852c9ae98d181703f8021bc5569` |
| `??` | `docs/audit/inventory_stage1_code_review_2026-10-29.md` | `e918e0902983590a97ff4e9235f8185f30e3345f62d65c43ffbeab20e6804057` |
| `??` | `docs/audit/inventory_stage2a_physical_apply_review_2026-10-30.md` | `80910e314cfa5045de91cae1b16046af1465ad9948dfd1971899d0a7fc27a760` |
| `??` | `docs/audit/wild188901_corrections_retry_20261025.md` | `1a5eb5faf120bb91ea9333ac72c42b440b99b09eac4b6fa7e0368f32b794c4d5` |
| `??` | `docs/flows/inventory_stage2a_physical_apply.md` | `e2a467181377435ea0312bfa9c8779ead2a8f7808035b4e4d542f8e67a962002` |
| `??` | `docs/flows/inventory_stage2a_preparation.md` | `6ad6a1085c7578d00f0df6e72685788bed52c6540b82048dd3c80cf61dfa69f8` |
| `??` | `fbs2_missing_kiz_2026-10-21_2026-10-22.txt` | `d6741246b2cc92285f819187e8168bba1f17392a275519acc162d566dd1a2da0` |
| `??` | `scripts/migrations/20261029_add_inventory_application_preparations.sql` | `f61776feded4d68c7751ef8f55e4782ffb03d6bbc2f08d0ff7949826fa69353d` |
| `??` | `scripts/migrations/20261030_add_inventory_location_freeze.sql` | `6e518e31e6a587cd1a0465a1c23ab192a0f9428f2bc1dad174527c6bfe68613a` |
| `??` | `scripts/migrations/20261030_add_inventory_physical_apply.sql` | `3f7fdd327199739edf36d39bc2c0ac6b6b528a4b57a6b95b7c14f27edf4a4ac1` |
| `??` | `scripts/migrations/20261102_inventory_projection_preflight.sql` | `b5d713bdf4b30f86719c5ab93a86da1cbaf6eadbc02b74c58d8fe2cc4603b2b3` |
| `??` | `scripts/migrations/20261102_inventory_runtime_privileges_preflight.sql` | `e0df54298f446d8fa12e8bdfdc05099bb2a3059494c4469c8acd395514f39261` |
| `??` | `tests/test_inventory_applications_postgres.py` | `c0df266b40b438b7d88ac6f2d2664e121efbb01f78d5a57bfd358c4a53b227fc` |
| `??` | `tests/test_inventory_deployment_postgres.py` | `558087ba35a62fc306e807d1b214c1a9479e092cf0c4cb4a8e357fe693b1d6dc` |
| `??` | `tests/test_inventory_freeze_postgres.py` | `17bad3c56702e863e5a5333c3906d0f9bfc43a7f09eeb8295e3944645d7d7529` |
| `??` | `tests/test_inventory_physical_apply_postgres.py` | `093707319409839479dc89d58ea9982fd62a80742fe954530a7e02b93433b0c3` |

Локальные артефакты находятся в `/tmp/wms-stage2a-readiness/`: version.json,
production.json, production-more.json, effective-privileges.json, recent-registry.json,
locationless-evidence.json, production-schema.sql, restore.json, rehearsal.json,
legacy-functions-comparison.json и rehearsal/uvicorn logs. Raw definitions/секреты в
отчёт не выгружались; schema export не содержит production rows. Каталог ограничен
правами владельца. Временные scripts вне рабочего дерева фиксируют реальные probes;
они не являются закоммиченным новым тестовым набором.

В рамках этого поручения создан **только этот отчёт**. Код/миграции/tests/config/flags и
исходные рабочие изменения не менялись, commit/deployment не выполнялись. Disposable
сервисы удалены после проверок; действующие сторонние контейнеры не затрагивались.
