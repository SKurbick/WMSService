# Единая история операций и редактируемые поступления

> **Статус: PROPOSAL.** Read-only часть реализована частично; модель
> редактируемых поступлений и transactional revision write flow остаются
> предложением, а не текущим контрактом.

| Область | Статус |
|---|---|
| `GET /api/operations-history` и detail endpoint | Реализовано для kit, re-sorting, FBS и standalone movements |
| `GET /api/receipts/history` и `GET /api/receipts/{guid}/history` | Реализовано отдельно от unified operations list |
| Receipt headers в unified operations list | Не реализовано |
| Новая receipt revision schema и transactional write flow | Proposal |

Дата анализа: 2026-07-22.

## 1. Цель и границы

Цель — спроектировать read-only API единой истории и будущую модель
редактируемых поступлений, сохранив `wms.movements` append-only ledger.

В этом документе разделены четыре понятия:

1. `wms.inventory` — текущее материализованное состояние остатков.
2. `wms.movements` — состоявшиеся физические эффекты над остатками.
3. Доменная операция — поступление, revision поступления, kit operation,
   re-sorting, FBS shipment или task, объединяющая несколько effects.
4. Операционное событие — факт приема, ошибки, retry, смены состояния или
   резерва, который мог не изменить физический остаток.

Код, endpoint, миграции и write flow в рамках анализа не изменялись.

## 2. Источники и степень подтверждения

Проверены:

- endpoints, Pydantic schemas, services, repositories и SQL текущего `main`;
- `docs/`, сохраненный `wms_schema.sql` и все SQL-миграции репозитория;
- Git history и все доступные ветки (`main` и `origin/main`);
- read-only системные каталоги и агрегаты фактической целевой PostgreSQL.

Важно: в текущем репозитории нет endpoint, schema, service, repository,
consumer или SQL-команды, которые создают либо обновляют `wms.receipt_items`.
Поиск по всей Git history также не обнаружил такого прикладного flow.
Следовательно, фактический writer поступлений находится вне этого checkout
(другой сервис, скрипт или интеграция). Его входной контракт и алгоритм нельзя
достоверно восстановить из WMS Service.

## 3. Подтвержденное состояние целевой БД

### 3.1 `wms.movements`

Фактическая таблица partitioned по `created_at` и содержит:

`movement_id`, `movement_type`, `product_id`, `from_location_id`,
`to_location_id`, `quantity`, `batch_number`, `container_code`,
`from_container_id`, `to_container_id`, `user_name`, `reason`, `metadata`,
`created_at`, `source_type`, `source_id`, `source_item_id`.

Фактический `chk_movement_type` разрешает:

`receive`, `putaway`, `transfer`, `pick`, `ship`, `unpack`, `adjust`,
`kit_assembly`, `kit_disassembly`, `re_sorting`.

В отличие от сохраненного dump, целевая БД уже содержит `re_sorting`, а также
два новых `NOT VALID` check constraint:

- `quantity > 0`;
- хотя бы одна сторона `from_location_id/to_location_id` не `NULL`.

`NOT VALID` означает: новые и изменяемые строки проверяются, но наличие старых
нарушений еще не было исключено командой `VALIDATE CONSTRAINT`.

У parent `wms.movements` нет PK/unique. Поэтому ссылка только по
`movement_id` не обеспечивается FK. Для устойчивой идентичности уже применяется
пара `(movement_id, created_at)`.

Триггер `trg_update_inventory_from_movement` выполняется только на `INSERT` и:

- добавляет `quantity` в `to_location_id`;
- вычитает `ABS(quantity)` из `from_location_id`;
- не ветвится по типу для обычного расчета delta;
- при отсутствии расходной inventory-строки явно бросает ошибку только для
  `ship`, `transfer`, `kit_assembly`, `kit_disassembly`;
- для исходящего `adjust` или `re_sorting` отсутствие строки может привести к
  записанному movement без фактического уменьшения inventory;
- отрицательное значение существующей строки останавливает DB check
  `inventory.quantity >= 0`.

Пересчет inventory также использует направления, а не смысл
`movement_type`: `to_location_id = +quantity`, `from_location_id = -quantity`.

На дату проверки в БД были типы и связи:

| Тип | `source_type` | Количество | Надежность группировки |
|---|---|---:|---|
| `kit_assembly` | `kit_operation` | 30 | надежная |
| `kit_disassembly` | `kit_operation` | 18 | надежная |
| `re_sorting` | `re_sorting_operation` | 4 | надежная |
| `receive` | `NULL` | 1 397 | только эвристика |
| `ship` | `NULL` | 1 643 | FBS связывается обратной ссылкой из items |
| `transfer` | `NULL` | 236 | task/container — эвристика или контекст |
| `adjust` | `NULL` | 32 | receipt/task/manual — эвристика |

### 3.2 `wms.receipt_items`

Фактические поля:

- `receipt_item_id bigint PK`;
- `guid varchar NOT NULL`;
- `product_id varchar NOT NULL FK -> public.products(id)`;
- `quantity numeric NOT NULL CHECK quantity >= 0`;
- `document_number`, `supplier_name`, `supplier_code`;
- `created_at`, `updated_at`, оба default `now()`.

Unique: `(guid, product_id)`. Триггер только обновляет `updated_at` на любой
`UPDATE`. Нет header-таблицы, revision/event table, raw payload, location,
batch/container, document date, source revision, source event id или внешнего
`updated_at`.

Read-only проверка БД показала:

- 1 393 строки, 660 разных `guid`, дублей `(guid, product_id)` нет;
- одна строка с `quantity=0`;
- девять строк обновлялись заметно позже создания;
- все строки можно эвристически сопоставить хотя бы с одним `receive` по
  `product_id` и вхождению `document_number` в `movements.reason`;
- для 1 380 строк найден ровно один такой movement;
- 13 строк имеют несколько movements;
- только для 1 375 строк направленная сумма найденных movements равна текущему
  snapshot quantity.

Три тестовые строки имеют последовательности `receive` + входящие/исходящие
`adjust` с причинами вида «Корректировка поставки: old → new». Это подтверждает
использовавшийся алгоритм delta для этих тестовых данных.

Несколько реальных строк были обновлены значительно позднее первоначального
`receive`, но новых `adjust` рядом с `updated_at` нет. Например, snapshot
уменьшен со 102 до 51, с 183 до 14 и со 118 до 0, а ledger содержит только
первоначальный `receive`. Это подтверждает, что существующий writer способен
изменять snapshot без физического movement. Нельзя считать
`receipt_items.quantity` источником истории или автоматически считать, что
inventory уже отражает эти revisions.

## 4. Фактический flow поступлений: что известно и неизвестно

### 4.1 Первое поступление

По данным БД первоначальный flow практически одновременно создает:

1. входящий `movement_type='receive'` в некоторую WMS location;
2. snapshot `wms.receipt_items` с `guid + product_id + quantity`.

У `receive` заполнены `reason` вида «Поставка {document_number} от
{supplier_name}» и `user_name`. `metadata` и `source_*` не заполнены.
Movement обычно создан на миллисекунды раньше snapshot.

Transaction boundary, locks и точный порядок внешнего writer-а из данного
репозитория не видны. Близость timestamps не доказывает атомарность.

### 4.2 Последующее обновление

Подтверждены два наблюдаемых поведения:

- тестовые обновления: `delta = new_quantity - old_quantity`; положительная
  delta создает входящий `adjust`, отрицательная — исходящий `adjust` с
  `quantity=abs(delta)`, затем snapshot получает новое quantity;
- реальные обновления: snapshot менялся, но соответствующий movement не был
  создан.

Следовательно, единого надежного текущего flow нет либо разные writers/версии
работают по-разному.

### 4.3 Добавление, удаление и замена SKU

Схема позволяет:

- добавить новый SKU через новую строку `(guid, product_id)`;
- оставить удаленный SKU как строку с `quantity=0`;
- физически удалить строку: запрета, audit trigger и FK на нее нет;
- представить изменение `product_id` как UPDATE строки, если новая unique-пара
  свободна, либо как DELETE old + INSERT new.

По текущей БД невозможно установить, что именно разрешает внешний writer.
Нет immutable line id из источника, поэтому изменение product A → B
неотличимо от удаления A и добавления B. Для складских effects это в любом
случае должно давать `-old_qty` по A и `+new_qty` по B.

### 4.4 Даты внешнего документа

В `receipt_items` и связанных `receive` movements нет даты документа и даты
редактирования источника. `created_at/updated_at` — серверные timestamps WMS,
не доказанные внешние даты. `document_number` — номер, не дата.

Исходное внешнее сообщение не хранится, а его schema/consumer отсутствуют в
репозитории. Поэтому нельзя подтвердить, какие поля сообщения содержат:

- исходную дату документа;
- дату изменения в 1С;
- номер revision или стабильный id строки.

До проектирования write-контракта нужно получить примеры первоначального и
отредактированного сообщения и зафиксировать mapping. Предпочтительные
семантические поля: `document_date -> effective_at`, `modified_at ->
source_updated_at`, `guid -> external_document_id`, `line_guid ->
external_line_id`, `version -> source_revision`.

## 5. Текущий movements API

`POST /api/movements` принимает 1–500 строк, открывает одну transaction и
последовательно вставляет movements. Service проверяет наличие хотя бы одной
стороны и существование location code. Он не блокирует расходную inventory,
не проверяет aggregate sufficiency всего batch и не позволяет заполнить
`metadata/source_*`.

`GET /api/movements` фильтрует по `product_id`, `container_code`, строковому
`movement_type`, датам и использует offset pagination. Сортировка только
`created_at DESC`, без tie-breaker.

Текущий ответ не возвращает:

- `from_location_id`, `to_location_id` (только codes);
- `from_container_id`, `to_container_id`;
- `metadata`;
- `source_type`, `source_id`, `source_item_id`;
- стабильную составную identity `(movement_id, created_at)` как отдельную
  ссылку;
- `warehouse_delta`, роль effect, статус/идентичность доменной операции;
- `effective_at`, `source_updated_at`;
- баланс до/после.

Есть несогласованность: Python `MovementType` содержит `write_off`, которого
нет в фактическом DB constraint, и не содержит `putaway/pick`, разрешенных БД.
Pydantic response может не провалидировать исторические `putaway/pick`.

## 6. Группировка существующих операций

### 6.1 Надежная структурная группировка

- Kit: `source_type='kit_operation'`, `source_id=operation_id`,
  `source_item_id=item_id`; item также хранит `(movement_id,
  movement_created_at)`. Одна операция содержит component consumption и kit
  result либо обратные roles.
- Re-sorting: `source_type='re_sorting_operation'` и две item roles
  `source_outgoing/target_incoming`, с той же составной ссылкой на movement.
- FBS: movement не имеет `source_*`, но все успешные items одной product group
  атомарно получают один `movement_id`. Группировка shipment → items надежна,
  связь item → movement прикладно гарантируется, хотя FK отсутствует. Из-за
  отсутствия `movement_created_at` формально ссылка слабее, чем у kit/re-sort.
- Stock reservation: каждый `stock_reservation_events.reservation_event_id` —
  стабильное append-only операционное событие. Оно не является movement.

### 6.2 Эвристическая или неполная группировка

- Receipt: `document_number` извлекается из `reason`, затем сопоставляется с
  `receipt_items.product_id`; `guid` и movement структурно не связаны.
- Tasks: movements содержат `reason='Task #id'` или `Task #id (approved)`.
  `source_*` не заполняются, а `tasks.related_movement_id` не моделирует набор
  effects.
- Container register/unpack/transfer: часть связей восстанавливается по
  `container_code`, времени и типу; общего operation id нет.
- Обычные manual receive/adjust/transfer/ship: одна строка movement может быть
  показана как отдельная операция, но batch из `POST /api/movements` не имеет
  batch/operation id и не может быть надежно восстановлен.
- История task statuses отсутствует: хранится текущее состояние и timestamps,
  а не append-only transitions.

## 7. Семантика времени

Для любой нормализованной операции нужны три независимых поля:

- `recorded_at` — когда revision/event и его effects зафиксированы и применены
  WMS. Основная лента сортируется по нему.
- `effective_at` (alias в UI: `original_operation_at`) — бизнес-дата исходной
  операции. Для correction это исходная дата поступления, а не время записи
  корректировки.
- `source_updated_at` — когда источник изменил документ/revision.

Для текущих movements доступно только `created_at`, которое отображается как
`recorded_at`. Нельзя без доказательств копировать его в external dates.

Correction, записанная 22 июля для документа от 10 июня, остается в основной
ленте 22 июля. В карточке отдельно показывается «исходное поступление: 10 июня»
и, если известно, «изменено в источнике: 20 июля». Backdating `created_at`
movement запрещено: это ломает аудит, cursor chronology и партиционирование.

## 8. `adjust` или `receipt_correction`

### 8.1 `adjust` + `source_type='receipt_revision'`

Плюсы:

- уже разрешен DB constraint и Python enum;
- текущий inventory trigger и ledger recalculation сразу понимают direction;
- не требует менять общую физическую taxonomy;
- бизнес-смысл однозначно задается структурной ссылкой на revision/item и role;
- manual adjustment остается отличимым, потому что имеет `source_type IS NULL`
  либо отдельный `manual_adjustment`.

Минусы:

- потребители, смотрящие только на `movement_type`, увидят общую
  «корректировку»;
- необходимо запретить receipt writer-у создавать `adjust` без `source_*`;
- существующий trigger не бросает explicit error, если строка для исходящего
  `adjust` отсутствует. Нужны предварительная проверка и row lock; в будущем
  полезно усилить trigger независимо от типа.

### 8.2 Новый `movement_type='receipt_correction'`

Плюсы:

- смысл виден даже старому плоскому movement reader;
- проще отдельная статистика только по типу.

Минусы:

- нужны миграция check constraint, Python enum, OpenAPI/schema/API docs и тесты;
- тип все равно не группирует несколько effects — `source_*` остается нужен;
- trigger/recalculation не получают новой возможности: они считают sides;
- текущая trigger-защита расхода также не включает новый тип, поэтому новый
  enum сам по себе не делает correction безопаснее;
- taxonomy смешивает физический механизм (`adjust`) с происхождением операции
  (`receipt revision`), что плохо масштабируется.

### 8.3 Решение

Рекомендуется `movement_type='adjust'` +
`source_type='receipt_revision'`. На уровне общей истории операция имеет
`operation_kind='receipt_revision'`, а effects — roles `quantity_increase`,
`quantity_decrease`, `sku_added`, `sku_removed`.

Отдельный `receipt_correction` оправдан только если downstream-интеграции не
могут фильтровать по `source_type`. Даже тогда он не отменяет revision tables,
locks и структурные ссылки.

## 9. Общий алгоритм correction

Входной документ должен быть явно объявлен полным snapshot. Для partial patch
нужен другой контракт; иначе отсутствующий SKU нельзя считать удаленным.

1. Нормализовать строки и агрегировать повторяющиеся ключи. Ключ минимум
   `(product_id, batch_number, container_code, target_location)`; если источник
   дает immutable `line_id`, использовать его для распознавания замены SKU.
2. Найти документ по `(source_system, external_document_id)` и сериализовать
   обработку.
3. Проверить idempotency key/source revision/payload hash и порядок.
4. Взять current items под `FOR UPDATE`.
5. Сравнить union ключей предыдущего и нового snapshots:
   `delta = new_quantity - old_quantity`.
6. `delta > 0`: входящий `adjust` на `delta`.
7. `delta < 0`: проверить и заблокировать exact available inventory key, затем
   исходящий `adjust` на `abs(delta)`.
8. Для изменения product по stable external line создать два effects в одной
   revision: расход old product и приход new product.
9. `delta = 0`: movement не создавать; metadata/header changes можно сохранить
   как operation event без physical effects.
10. В одной transaction создать revision, revision items, все movements,
    составные ссылки и обновить current snapshot.

## 10. Вариант A — минимальный

### 10.1 Таблицы

Сохранить `wms.receipt_items` как current snapshot и добавить:

`wms.receipt_revisions`:

- `revision_id bigint GENERATED ... PRIMARY KEY`;
- `source_system varchar NOT NULL`;
- `receipt_guid varchar NOT NULL`;
- `revision_no bigint NOT NULL` — внутренний последовательный номер;
- `source_revision varchar NULL`, `source_event_id varchar NULL`;
- `payload_hash bytea NOT NULL`;
- `status varchar NOT NULL`:
  `applied/duplicate/stale/rejected/failed`;
- `document_number`, `supplier_name`, `supplier_code` как audit snapshot;
- `effective_at timestamptz NULL`;
- `source_updated_at timestamptz NULL`;
- `recorded_at timestamptz NOT NULL DEFAULT now()`;
- `actor varchar NULL`, `error_message text NULL`, `raw_payload jsonb`;
- `previous_revision_id bigint NULL FK -> receipt_revisions`.

Constraints:

- unique `(source_system, receipt_guid, revision_no)`;
- unique `(source_system, source_event_id)` where not null;
- unique `(source_system, receipt_guid, payload_hash)` для identical replay;
- check допустимого status.

`wms.receipt_revision_items`:

- `revision_item_id bigint PRIMARY KEY`;
- `revision_id bigint NOT NULL FK ... ON DELETE RESTRICT`;
- `change_group_id uuid NULL` для пары effects замены SKU;
- `product_id varchar NOT NULL FK products`;
- `role varchar NOT NULL`;
- `old_quantity numeric NOT NULL`, `new_quantity numeric NOT NULL`;
- `delta numeric NOT NULL`;
- `location_id bigint NOT NULL FK locations`;
- `batch_number`, `container_code`;
- `movement_id bigint NULL`, `movement_created_at timestamptz NULL`;
- `created_at timestamptz NOT NULL DEFAULT now()`.

Unique `(revision_id, product_id, location_id, batch_number, container_code)`
должен учитывать `NULLS NOT DISTINCT`; если source допускает несколько строк
одного SKU, в ключ нужен `external_line_id`.

### 10.2 Immutable и mutable

Revision headers/items после commit immutable. Допустимо записывать конечный
status внутри той же transaction; последующие retry лучше оформлять новой
attempt/event записью. В `receipt_items` меняются quantity и повторяемые
document/supplier поля; `receipt_item_id` и пара `guid/product_id` не должны
меняться. Замена SKU моделируется remove + add.

### 10.3 Transaction и locks

- `pg_advisory_xact_lock(hash(source_system + receipt_guid))` сериализует
  writers одного документа, включая первое сообщение до появления row.
- Все существующие `receipt_items` документа читаются `FOR UPDATE`.
- Расходные inventory rows exact key читаются `FOR UPDATE` в каноническом
  порядке product/location/batch/container во избежание deadlock.
- Revision, effects, movements и snapshot update — одна transaction.
- Нельзя вызывать публичный MovementService, который сам открывает transaction;
  нужен in-transaction path с поддержкой `source_*`.

### 10.4 Edge cases

- `quantity=0`: хранить snapshot tombstone с zero либо договориться, что
  отсутствие строки означает zero. Для минимальной миграции безопаснее оставить
  строку с zero; physical decrease все равно обязана иметь movement.
- Новый SKU: old=0, new>0, role `sku_added`, входящий adjust.
- Удаленный SKU полного snapshot: old>0, new=0, role `sku_removed`, исходящий
  adjust.
- Product change без external line id: remove old + add new, без утверждения,
  что это одна измененная строка; при наличии line id связать `change_group_id`.
- Идентичное сообщение: записать/вернуть duplicate без movements и snapshot
  UPDATE. Можно не создавать второй revision, но отдельный ingestion event
  полезнее для observability.
- Out-of-order: если есть source revision или `source_updated_at`, stale event
  записать со status `stale`, без effects. Без монотонного внешнего признака
  безопасно определить порядок нельзя; ingestion time не подходит.

### 10.5 Восстановление и миграция

Будущую историю можно восстановить из revisions; документ header по-прежнему
денормализован в item snapshot. Миграция простая: создать synthetic baseline
revision для каждого guid на основании текущего snapshot и попытаться связать
первоначальные receive movements. Но baseline не восстанавливает потерянные
промежуточные revisions и исходные external dates.

Плюс варианта — минимальное вмешательство. Минус — document metadata повторяется
по items, current snapshot не имеет revision FK, а модель хуже поддерживает
stable external lines и изменение состава.

## 11. Вариант B — полноценный

### 11.1 Таблицы

`wms.receipts` — header/current state:

- `receipt_id bigint PK`;
- `source_system varchar NOT NULL`;
- `external_document_id varchar NOT NULL`;
- `document_number`, supplier fields;
- `effective_at timestamptz NULL` — исходная дата документа;
- `latest_source_updated_at timestamptz NULL`;
- `current_revision_no bigint NOT NULL`;
- `status varchar NOT NULL`;
- `created_at`, `updated_at`;
- unique `(source_system, external_document_id)`.

Immutable: `receipt_id`, source system и external document id. Исправление
исходной даты или supplier — отдельная revision, после которой current header
может обновиться, но revision сохраняет before/after.

`wms.receipt_items` — актуальные строки:

- `receipt_item_id bigint PK`;
- `receipt_id bigint NOT NULL FK receipts ON DELETE RESTRICT`;
- `external_line_id varchar NULL`;
- `product_id FK products`, `quantity >= 0`;
- `location_id FK locations`, batch/container;
- `is_active boolean NOT NULL`;
- `current_revision_id bigint NOT NULL`;
- timestamps;
- unique `(receipt_id, external_line_id)` where line id not null;
- fallback unique NULLS NOT DISTINCT по receipt + product + stock dimensions.

`wms.receipt_revisions` — immutable received versions:

- `revision_id bigint PK`, `receipt_id FK`;
- `revision_no bigint`, `source_revision`, `source_event_id`, `payload_hash`;
- `status`, `recorded_at`, `effective_at`, `source_updated_at`, actor/error;
- header before/after JSON либо типизированные before/after поля;
- raw payload;
- `previous_revision_id FK`;
- unique `(receipt_id, revision_no)`, idempotency uniques как в A.

`wms.receipt_revision_items` — immutable effects/change lines:

- `revision_item_id bigint PK`, `revision_id FK`;
- `change_group_id uuid`, `external_line_id`;
- `role`: `quantity_increase`, `quantity_decrease`, `sku_added`,
  `sku_removed`, `product_replaced`, `metadata_only`;
- `old_product_id`, `new_product_id`;
- `effect_product_id NOT NULL FK products` — SKU конкретного warehouse effect;
- old/new quantity, positive `effect_quantity`, signed `warehouse_delta`;
- location, batch/container before/after при необходимости;
- `movement_id`, `movement_created_at`;
- check: physical role имеет nonzero delta и movement link;
- unique `(revision_id, deterministic_effect_key)`.

Для product replacement создаются две строки с одним `change_group_id`:
`sku_removed` old SKU и `sku_added` new SKU. Это напрямую отображается массивом
`effects` и не требует одного movement с двумя product ids.

### 11.2 Immutable и transaction model

Revisions и revision items immutable. `receipts` и `receipt_items` — current
projection, обновляемая только в transaction применения revision. Movement
append-only. Удаление receipt/current item физически запрещено; используются
status/is_active/zero quantity.

Transaction и locks те же, что в A, но `SELECT ... FOR UPDATE` по header дает
основной row lock после первого insert. Advisory lock все равно нужен для гонки
двух первых сообщений и единого порядка lock acquisition.

### 11.3 Idempotency и порядок

Рекомендуемый приоритет:

1. уникальный `source_event_id`;
2. монотонный `source_revision`;
3. `source_updated_at` + payload hash;
4. только payload hash — защищает identical replay, но не порядок разных
   revisions.

Stale/duplicate/rejected events не создают movements и не меняют projection,
но должны оставаться операционными событиями. Если два разных payload имеют
одинаковый source revision/time, событие переводится в conflict и требует
разбора, а не применяется по arrival order.

### 11.4 Восстановление и миграция

Current projection полностью восстанавливается из applied revisions, если
baseline содержит полный snapshot. Physical inventory по-прежнему
восстанавливается только из movements; эти два восстановления должны
сверяться, но не заменяют друг друга.

Миграция сложнее:

1. Создать header на каждый `guid`.
2. Перенести текущие items и создать baseline revision.
3. Сопоставить receive movements по product/document/reason/time с confidence.
4. Тестовые adjust sequences можно распознать, но связь пометить как backfilled.
5. Для production snapshot updates без movements создать anomaly records, а не
   выдумывать исторические effects.
6. Не создавать автоматически compensating movements: изменение текущего
   остатка спустя месяцы может быть неверным после transfers/shipments. Нужны
   инвентаризационная сверка, известная receipt location и бизнес-решение.

### 11.5 Сравнение вариантов

| Критерий | A: snapshot + events | B: header/current/revisions/effects |
|---|---|---|
| Изменение текущего writer | меньше | больше |
| Группировка документа | через guid в items | явный receipt header |
| Замена SKU/line identity | ограниченно | полноценно |
| Out-of-order/idempotency | поддерживается | поддерживается лучше |
| Восстановление current document | возможно, с оговорками | штатно |
| Миграция | проще | сложнее |
| Долгосрочная расширяемость | средняя | высокая |

## 12. Контракт общей истории

### 12.1 Выбор path

- `/api/product-history` слишком узок: kit/re-sorting и receipt revision
  затрагивают несколько SKU, а FBS/task/reservation имеют самостоятельную
  operation identity.
- `/api/history` наиболее короток, но слишком общий: со временем там ожидаются
  login/config/location audit и другие несвязанные журналы.
- `/api/operations-history` явно задает границу — складские бизнес-операции и
  их effects — и остается расширяемым.

Рекомендация: `GET /api/operations-history`.

Возможный detail path:
`GET /api/operations-history/{event_id}`. Для существующих сложных операций
можно сначала отдавать links на специализированные detail endpoints.

### 12.2 List item

```json
{
  "event_id": "receipt_revision:481",
  "event_kind": "physical_operation",
  "operation_kind": "receipt_revision",
  "title": "Корректировка поступления BB-001234",
  "recorded_at": "2026-07-22T09:14:31Z",
  "effective_at": "2026-06-10T00:00:00Z",
  "source_updated_at": "2026-07-20T15:43:02Z",
  "status": "applied",
  "actor": {"type": "integration", "id": "1c", "display_name": "1С"},
  "source": {
    "type": "receipt_revision",
    "id": "481",
    "parent_type": "receipt",
    "parent_id": "992",
    "external_id": "guid-from-1c",
    "revision": "17"
  },
  "effects": [
    {
      "product_id": "wild1980",
      "product_name": "Товар",
      "role": "quantity_decrease",
      "quantity": 51,
      "warehouse_delta": -51,
      "from_location": {"id": 10, "code": "RECEIVING-01"},
      "to_location": null,
      "batch_number": null,
      "container_code": null,
      "movement": {
        "movement_id": 44501,
        "created_at": "2026-07-22T09:14:31Z"
      }
    }
  ],
  "details_available": true
}
```

`event_kind` рекомендуется ограничить:

- `physical_operation` — один или несколько committed effects;
- `operation_state` — state/retry/error без физического эффекта;
- `reservation_event` — отдельная нефизическая модель спроса.

`warehouse_delta` — signed delta для SKU по всему складу. Для transfer он `0`,
а направление остается в from/to. При location filter дополнительно можно
отдавать `location_delta`, потому что transfer дает `-Q/+Q` относительно
конкретного адреса.

### 12.3 Фильтры

`product_id`, `event_kind`, `operation_kind`, `status`, `actor`,
`location_id/location_code`, `warehouse_id`/subtree scope, `container_code`,
`batch_number`, `source_type`, `source_id`, `recorded_from`, `recorded_to`,
`effective_from`, `effective_to`, `include_non_physical`, `limit`, `cursor`.

По умолчанию сортировка:

```text
recorded_at DESC, event_id DESC
```

Cursor — opaque base64url от versioned JSON `{recorded_at, source_rank,
numeric_id}`. `event_id` строковый и гетерогенный, поэтому внутренний
`source_rank + numeric_id` дает детерминированный tie-breaker. Cursor должен
фиксировать filters hash/version и применяться keyset-условием, не offset.

Стабильные `event_id`:

- `movement:{created_at}:{movement_id}` для одиночного legacy movement;
- `kit_operation:{operation_id}`;
- `re_sorting_operation:{operation_id}`;
- `fbs_shipment:{shipment_id}` либо item/group event для частичных результатов;
- `receipt_revision:{revision_id}`;
- `reservation_event:{reservation_event_id}`;
- `task:{task_id}:current` только как неполный legacy state, не история.

## 13. Первая версия без новых таблиц

Возможна read-only V1 как application-level merge/`UNION ALL`:

- надежно группировать kit и re-sorting;
- группировать FBS shipment/items и подтягивать успешные movements;
- отображать reservation events отдельно от physical operations;
- показывать остальные movements как single-effect operations;
- эвристически помечать receipt и task по `reason`.

Ограничения V1:

- receipt revision не является надежной operation: отсутствуют revision id,
  source dates и структурные links;
- часть snapshot corrections вообще не имеет movements;
- composition changes и deleted SKU не восстановимы;
- tasks не имеют status transition history;
- batch movements публичного endpoint не группируются;
- container operations группируются неполно;
- для legacy данных `effective_at/source_updated_at` должны быть `null`, а не
  подменяться `created_at`;
- один общий SQL UNION с множеством joins может быть дорогим; допустим
  fan-out по источникам с bounded keyset fetch и merge в service, но cursor
  должен хранить положение каждого источника либо использовать materialized
  operation index позднее.

## 14. Индексы

Фактические movement indexes уже покрывают product, created time, from/to,
container, type и source, но для keyset и tie-breaker нужны составные варианты
на partitioned parent с наследованием в partitions:

- `(created_at DESC, movement_id DESC)`;
- `(product_id, created_at DESC, movement_id DESC)`;
- `(source_type, source_id, created_at DESC, movement_id DESC)`;
- `(from_location_id, created_at DESC, movement_id DESC)`;
- `(to_location_id, created_at DESC, movement_id DESC)`;
- при утвержденном container filter:
  `(container_code, created_at DESC, movement_id DESC)` where non-null;
- при частом type filter:
  `(movement_type, created_at DESC, movement_id DESC)`.

Не следует создавать все индексы заранее: подтвердить filters и выполнить
`EXPLAIN (ANALYZE, BUFFERS)` на объемах partitions.

Для новой receipt-модели:

- revision `(recorded_at DESC, revision_id DESC)`;
- revision `(receipt_id, revision_no DESC)` unique;
- idempotency unique indexes;
- revision items `(product_id, revision_id)`;
- current items unique business key и `(receipt_id, is_active)`;
- при source date filter `(source_updated_at DESC, revision_id DESC)`.

Для FBS желательно индексировать successful item movement lookup
`(movement_id) WHERE movement_id IS NOT NULL`; для объединенной ленты —
header `(received_at DESC, shipment_id DESC)`. Аналогичные `(created_at DESC,
id DESC)` нужны kit/re-sorting и `(event_received_at DESC,
reservation_event_id DESC)` reservations.

## 15. Конкурентность и failure semantics receipt revision

Канонический порядок:

1. advisory lock document;
2. receipt header/current rows `FOR UPDATE`;
3. consumption inventory rows `FOR UPDATE` в сортированном порядке;
4. revision header/items;
5. movements inserts (trigger меняет inventory);
6. current projection update;
7. commit.

При недостаточном остатке correction decrease не должна частично применяться.
Вся revision откатывается. Для сохранения failed attempt нужен audit вне
основной transaction либо отдельная короткая transaction после rollback;
такой event имеет `event_kind=operation_state`, effects без movement и status
`failed`.

Нельзя опираться только на текущий trigger для исходящего `adjust`: он не
ошибается при полном отсутствии inventory row. Service обязан проверить exact
stock под lock. Отдельным улучшением БД следует сделать единое правило расхода
для любого movement с `from_location_id`, а не whitelist типов.

## 16. Тестовая стратегия

### 16.1 Unit/service

- initial full snapshot создает baseline revision и receive effects;
- identical replay не создает movement;
- увеличение 10→15 создает один входящий adjust 5;
- уменьшение 15→4 создает исходящий adjust 11;
- zero/removal создает расход полного old quantity и inactive/tombstone;
- add SKU, remove SKU, simultaneous multi-SKU composition revision;
- product replacement создает два effects в одной revision;
- metadata-only revision не создает movement;
- unknown product/location, malformed dates/revision conflict;
- stale and out-of-order messages не меняют projection/inventory;
- `recorded_at` correction остается текущим временем, effective date — старой.

### 16.2 Integration PostgreSQL

- revision/items/movements/snapshot атомарны при ошибке любого effect;
- trigger дает точный inventory delta и ledger recalculation совпадает;
- отсутствующая и недостаточная расходная inventory строка отклоняется;
- nullable batch/container сопоставляются через `IS NOT DISTINCT FROM`;
- проверка composite movement link `(movement_id, created_at)`;
- constraints/idempotency indexes и partition routing;
- recovery current receipt projection from baseline + revisions.

### 16.3 Конкурентность

- два одинаковых одновременных сообщения: один applied, один duplicate;
- две разные revisions одного guid: сериализация и правильный порядок;
- newer приходит первым, older позже: older stale, без movement;
- два decreases конкурируют за один stock: не возникает отрицательного остатка;
- receipt correction конкурирует с ship/transfer/kit/re-sort по тому же exact
  inventory key;
- deadlock test на multi-SKU revisions с обратным порядком входных строк;
- crash/retry после insert revision, после первого effect и перед projection
  update: transaction rollback и безопасный replay.

### 16.4 Read API

- одна operation возвращает все effects;
- стабильный порядок при одинаковом `recorded_at`;
- cursor без пропусков/дублей при вставке новых событий между страницами;
- filters по product/location находят operation целиком, но можно отметить
  matched effects;
- transfer имеет warehouse delta 0;
- non-physical event имеет пустой effects либо effects с
  `warehouse_delta=null`, но не маскируется под физическое изменение;
- legacy receipt/task heuristic помечается `link_confidence='heuristic'`.

## 17. Рекомендуемая целевая архитектура

Рекомендуется вариант B и три слоя чтения:

1. Append-only physical ledger `wms.movements`.
2. Доменные headers/revisions/items, где operation является aggregate, а
   movements — ее effects.
3. Read model/adapter `operations-history`, нормализующий разные домены в один
   контракт без смешивания physical и non-physical semantics.

Для receipt corrections использовать `adjust + receipt_revision`, хранить
original/effective date отдельно и сортировать по `recorded_at`. Current
`inventory` и current receipt projection никогда не заменяют историю.

## 18. Поэтапный план

1. Получить и документировать реальный receipt message contract и writer:
   full snapshot/patch, document date, source updated time, revision/event id,
   stable line id и target location.
2. Выпустить read-only V1 `/api/operations-history` без новых таблиц: надежные
   kit/re-sort/FBS groups, reservations и single legacy movements; receipt/task
   links явно heuristic.
3. Провести data audit: все измененные receipt snapshots, unmatched/multiple
   movements, exact receipt locations и ledger/inventory integrity.
4. Добавить миграции варианта B, idempotency constraints и необходимые indexes.
5. Реализовать transactional receipt revision write flow с advisory + row
   locks и `adjust/source_type='receipt_revision'`.
6. Backfill receipts/current items и synthetic baselines с полем confidence;
   не создавать автоматические stock corrections для исторических anomalies.
7. Переключить history adapter с receipt heuristics на revision aggregates.
8. Структурно связать будущие task/FBS/container/manual batch operations через
   operation id и `source_*`.
9. После нагрузочных тестов добавить materialized operation read model только
   если fan-out/UNION не выдерживает SLA.

## 19. Открытые вопросы, блокирующие write design

- Где расположен фактический writer `receipt_items` и может ли он быть изменен
  атомарно вместе с movements?
- Входящее сообщение — полный document snapshot или partial patch?
- Как называются и насколько надежны document date, source updated time,
  revision/event id и line id?
- Может ли один документ содержать одинаковый SKU несколькими строками,
  разными партиями, контейнерами или локациями?
- В какую exact location должен применяться decrease старого поступления, если
  товар уже был перемещен или отгружен?
- Разрешена ли correction ниже уже физически доступного количества и какой
  workflow расхождения нужен вместо нее?
- Нужно ли сохранять rejected/failed receipt attempts в основной history?
- Требуется ли исторически исправить шесть подтвержденных production snapshots
  без movements или только пометить их как anomalies?
