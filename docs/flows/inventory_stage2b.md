# Inventory Stage 2B — КИЗ, QR и controlled reconciliation

Основа: интегрированный `9f3202c`, разработка в `feature/inventory-stage2b`.
Продолжает существующие документы, revisions, attempts, claims, freeze и applications.
Нового типа движения, независимого registry, production deployment и автоматического
commit нет. Существующие MODULE/FREEZE/APPLY flags по умолчанию остаются false.

## Единицы учёта и границы

- Россыпная строка `product_id + location_id` считает **только unidentified quantity**.
  Каждый active КИЗ считается отдельным сканом; он не прибавляется к actual россыпи.
- ContainerCount.quantity — полное собственное количество товара в конкретном QR,
  **включая** active КИЗ. Содержимое дочерних контейнеров в это число не суммируется.
- КИЗ сканируется с фактическим location_id и optional holder_qr. Используется общий
  KizCode с буквальными первыми 24 символами; QR контейнера не нормализуется как КИЗ.
- Registered без holder не считается размещённым в адресе. Unknown, registered,
  deactivated, shipped и error не становятся автоматически active.
- Поддерживаемая текущая container-схема имеет CHECK `chk_container_flat` и требует
  direct location. Вложенный контейнер создать нельзя. Это общее ограничение не снимается.
  При чтении legacy hierarchy каждому контейнеру соответствует собственный contents и
  effective ancestor location, без суммирования дочерних contents; nested/неоднозначные
  projections блокируют physical apply. Включение nesting требует отдельного развития
  controlled container contract, не скрытого удаления CHECK.
- ФБС-зона, большие/full physical pilots, batches, NULL/empty aliases и mixed quantity
  correction одного SKU с active КИЗ остаются ограниченными. Stage 1 full/preliminary
  workflow сохраняется; нельзя включать global auto-apply ради поддержки нового scan API.

## HTTP workflow

1. Создать обычный draft документа. Пока draft, один раз выполнить
   `identity-accounting` с `containers=false` для КИЗ либо `true` для КИЗ+контейнеров.
   Повтор с тем же UUID идемпотентен; изменение режима после настройки запрещено.
2. Обычный start одной REPEATABLE READ транзакцией фиксирует локации/claims, snapshot
   россыпи и новый immutable identity snapshot: КИЗ, containers, own contents, projections.
   Snapshot не изменяется при последующих сканах и перемещениях.
3. Для preliminary scan freeze не обязателен. Для применения нужны прежние begin freeze,
   операторское подтверждение отсутствия неотражённых операций и новый frozen epoch.
   Подтверждение остаётся операционным условием, не техническим доказательством времени.
4. Начать `identity-counts`. Сохраняются revision, автор, время, полный start evidence и
   freeze token. Новая попытка переводит открытую старую в superseded с сохранением сканов.
5. Добавлять по одному `scans`. Тот же UUID не дублирует скан; новый UUID того же code
   сохраняет отдельное событие и создаёт duplicate conflict при завершении.
   Container scan содержит полный собственный список product/quantity; отсутствие SKU
   означает фактический ноль. Каждый фактически найденный КИЗ сканируется отдельно.
6. `complete` фиксирует end evidence, результат сравнения и proposed controlled actions.
   Факт изменения во время count сохраняется как конфликт, а не стирает попытку.
   Завершённая попытка и её сканы неизменяемы; продолжение — новой попыткой.
7. Завершить обычные loose count attempts. Если controlled extract создаёт unidentified
   пару, которой не было в стартовых items, добавить её прежней командой found item и
   пересчитать. Expected на старте остаётся 0, proposed expected учитывает controlled plan.
8. Submit требует completed loose и identity counts. После return-to-count нужен новый
   identity count, даже если прежнее evidence внешне не изменилось. Затем используются
   прежние application-preview → freeze/ready → apply. План identity actions и подробные
   причины конфликтов доступны в identities; quantity preview отражает их влияние на loose.
9. После apply обычный application endpoint возвращает также identity_actions с results,
   operation IDs и registry refs. Все физические эффекты/история фиксируются целиком.

Новые команды принимают прежние UUID idempotency_key, expected_version и user_name.
Область/filtered SKUs зафиксированы при start; scan за пределами scope возвращает 409.
Все новые SQL-bound IDs имеют предел PostgreSQL bigint; выход за него — 422.
GET identities имеет limit/offset по attempts; каждый attempt возвращает свои сканы.

## Что исправляется автоматически после approval

| Расхождение | Контролируемое исправление | Обязательные условия |
|---|---|---|
| Active loose КИЗ найден в другом адресе | KizTransferService, explicit codes, transfer quantity=числу КИЗ | Оба адреса в одном фиксированном frozen scope, неизменный lifecycle, batch-less stock |
| Flat QR найден в другом адресе | ContainerMoveService | Contents и каждый active КИЗ подтверждены, статус допускает move, тот же склад |
| Flat container содержит больше единиц, loose соответственно меньше | ContainerFillService | Тот же адрес, open/empty, точный SKU, достаточно eligible loose, explicit новые contained КИЗ |
| Flat container содержит меньше единиц, loose соответственно больше | ContainerExtractService | Тот же адрес, допустимый статус, missing contained КИЗ фактически просканированы в loose |
| Неразмеченная quantity отдельного SKU расходится | Прежний strict inventory-source adjust | Unambiguous component, нет active КИЗ/конкурирующей identity allocation в корректируемом scope |
| Все собственные количества/holders совпали | Application без движения | Полные актуальные final counts всех видов учёта |

Fill/extract перераспределяют имеющиеся единицы, не создают товар. Выполняется проверка
совместимости counted quantities и scanned identities, stock capacity, projection и
сохранения количества. Неизвестное происхождение surplus содержимого нельзя прикрыть fill.
Обмен identities между контейнерами при неизменном quantity и неясный источник/holder
возвращают конфликт вместо произвольного выбора направления или партии.

## Атомарность и конкурентность

Общий apply сохраняет документ/revision protocol Stage 2A, application identity и одну
outer PostgreSQL transaction. Сначала NOWAIT ledger lock, document/fences, products,
locations (сразу UPDATE для identity mode), затем containers, contents, inventory, КИЗ;
новые pre-locks — NOWAIT. Нет позднего повышения KEY SHARE → UPDATE за удерживаемым
ФБС location SHARE: на таком конфликте apply откатывается и повторяется.
Для legacy writers порядок не меняется. Под конфликтом команда откатывается и безопасно
повторяется с тем же UUID, не ждёт, удерживая физическую транзакцию во время подсчёта.

Контролируемые services получают TransactionPool, привязанный **к тому же соединению**.
Их внутренние transactions являются savepoints; deferred checks выполняются при outer
commit, а не превращаются в отдельные успешно записанные операции. Не выполняются HTTP
вызовы для отдельных строк и не добавляется приложение, напрямую меняющее inventory,
container_contents или lifecycle КИЗ.

Для каждой server-derived action сначала создаётся application/action identity; затем
ограниченный transaction-local context. Допуск guard требует реальную applying application
того же xid, active action, ready epoch и fixed location. Простого GUC недостаточно.
После controlled service context очищается, создаются FK links на movement_registry.
Deferred constraints проверяют полный action plan, успешные controlled operations,
application identity и полноту registry links; исходный Stage 2A constraint продолжает
проверять quantity-adjustments, document status, release claims/fences.

Перед apply повторно сравниваются immutable end evidence/count ID/plan и preparation.
После controlled операций отдельно сверяются фактические holders, own contents и итоговый
unidentified actual каждой строки. Ошибка последней quantity adjustment, неверный effect,
deferred error или commit failure откатывают также operation headers/items, КИЗ/contents,
registry, application links, history и снятие freeze.

Inventory projection `update_inventory_from_movement()` и строгий Stage 2A adjustment
helper не изменяются. Из shared DB paths меняется только admission function: старая ветка
сохраняется, добавлен ограниченный identity-action допуск. Миграция проверяет точное body
md5 и атрибуты поддержанного Stage 2A определения; при drift — полный rollback.

ФБС продолжает прежний pipeline и atomic invalidation. Его context не получает inventory
identity bypass. Все новые actions держат те же fences: ФБС либо инвалидирует до проверки,
либо ждёт завершения короткой SQL-транзакции. created_at не трактуется как физическое время.
Запоздалые физические ФБС-события по-прежнему требуют SOP и повторного счёта; расширения
ФБС auto-reconciliation этим этапом нет.

## Ручное разрешение

Missing КИЗ/QR, неизвестный code/QR, terminal lifecycle, SKU вне filters, неподтверждённый
holder, mixed quantity adjustment, batch/empty key и неконтролируемый projection drift
блокируют весь apply. GET identities сохраняет причины и рекомендуемые шаги.

Ответственный возвращает submitted документ на count, явно release freeze, устанавливает
физическую причину и использует существующие допустимые controlled КИЗ/container операции
с их собственными проверками. Никакой автоматической регистрации/удаления, замены identity
обычным adjust или фальшивого applied нет. Затем новый freeze confirmation/epoch и все
финальные пересчёты. Если безопасное исправление не определено — закрыть без применения.
Операционный ручной выбор не отменяет проверок БД и не считается физической корректировкой.

## Deployment / privileges / отключение

Новая ручная миграция: `20261105_add_inventory_identity_counts.sql` **после всех четырёх
Stage 1/2A migrations и актуальной КИЗ/container/registry базы**. Production не применялся.
Старые binaries работают со схемой при выключенном модуле; для opt-in Stage 2B весь API
должен использовать новую версию. Совместимость FBS требует ранее предусмотренного общего
обновления API/consumers/retries до включения freeze.

Новые таблицы: inventory_identity_config/snapshots/counts/scans/actions/movement_links.
Устанавливаются FK, immutable guards, one-open-count index, deferred completeness guards.
Новых columns/changes physical inventory, movements, КИЗ и containers нет.

Перед migration владелец задаёт полный `wms.inventory_runtime_roles` roster и решает,
разрешены ли scoped grants: `wms.inventory_grant_runtime_privileges=on` только в явно
авторизованной deployment-сессии. По умолчанию helper только проверяет, а не выдаёт права.
Финальный preflight внутри migration атомарен: при нехватке privilege нет частичной
установки новой guard. Новый helper не SECURITY DEFINER, EXECUTE для PUBLIC отозван.

- Каждый writer: EXECUTE inventory_identity_can_write(bigint,bigint); при отсутствии
  identity context функция возвращает false, не читая новые таблицы.
- Inventory profile: SELECT/INSERT config, snapshots, scans, movement_links;
  SELECT/INSERT/UPDATE counts и actions; USAGE ровно двух новых identity sequences.
- Inventory profile требует точечный EXECUTE transfer_kiz_location,
  transition_kiz_container_holder, apply_container_extract_content, check_kiz_holder_integrity.
  Это существующие controlled functions. Старые warehouse table privileges остаются
  проверяемыми prerequisite: SELECT КИЗ/events; locking UPDATE хотя бы одного КИЗ column;
  SELECT/INSERT/UPDATE operation headers/items и contents; SELECT/UPDATE containers;
  SELECT/INSERT KIZ links, USAGE соответствующих existing sequences. При нехватке helper
  прерывается даже в grant mode; эти старые writer права оператор утверждает отдельно.
  Helper не выдаёт ALL ON SCHEMA и не создаёт superuser.
- Схему helper проверяет по реальным объектам; non-owner integration tests используют
  отдельную runtime role, включая transfer, container move/fill/extract и apply.

После migration read-only privilege preflight выполняют с тем же явным roster и
`wms.inventory_grant_runtime_privileges=off`, в READ ONLY transaction с timeout.
При уже установленной иной версии миграцию повторять нельзя; нужен forward upgrade.

Флаги и отключение прежние: APPLY=false, дождаться активных transactions, прекратить новые
freeze, новой совместимой версией явно release всех fences, лишь затем MODULE=false и
rollback API. Не удалять movements/registry/history и не откатывать DB после первых
физических применений как способ отмены бизнес-операций.
