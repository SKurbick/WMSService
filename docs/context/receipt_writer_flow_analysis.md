# Фактический flow поступлений из 1CRoutingAPI в WMS

Дата анализа: 2026-07-22.

Репозитории:

- `/home/skurbick/PROJECTS/1CRoutingAPI`, HEAD `4b3a5fe`, ветка
  `new_sticker_service`;
- `/home/skurbick/PROJECTS/WMSService`, HEAD `3bdd1af`, ветка `main`.

Анализ read-only: код, конфигурация, SQL, Git history и системные каталоги
целевой PostgreSQL прочитаны; рабочий код, SQL, конфигурация и БД не менялись.

## 1. Executive summary

Фактический production-capable writer найден в `1CRoutingAPI`.

Точка входа — `POST /api/receipt_of_goods/update`. Это обычный синхронный HTTP
endpoint, а не RabbitMQ consumer, cron или Celery task. Router подключен в
[`main.py`](/home/skurbick/PROJECTS/1CRoutingAPI/main.py:108), handler находится
в [`receipt_of_goods.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/api/v1/endpoints/receipt_of_goods.py:23).

Один вызов последовательно выполняет два независимых контура:

1. Legacy warehouse в общей PostgreSQL:
   `supply_to_sellers_warehouse`, `incoming_documents`, `incoming_items` и,
   через DB trigger, `inventory_transactions`.
2. WMS bridge:
   HTTP `POST` в WMS `WMS_API_URL_MOVEMENTS`, затем прямой SQL в
   `wms.receipt_items`; для decrease перед HTTP выполняется прямой SELECT из
   `wms.inventory/wms.locations`.

Legacy commit происходит раньше WMS. WMS HTTP movement и receipt snapshot не
атомарны: movement фиксируется WMS Service в своей transaction, а
`wms.receipt_items` пишется после ответа отдельным autocommit-запросом через
pool 1CRoutingAPI. Distributed transaction, saga, outbox, retry и idempotency
key отсутствуют.

Основные подтвержденные дефекты текущей семантики:

- legacy flow трактует сообщение как полный replacement по `guid`, а WMS bridge
  — как набор upsert-строк и не удаляет отсутствующие SKU;
- при decrease и недостатке товара WMS списывает только доступную часть, но
  snapshot устанавливается в полное новое количество;
- при `available=0` movement вообще не вызывается, а snapshot все равно
  обновляется;
- после 25.03.2026 WMS endpoint принимает массив, но correction helper
  1CRoutingAPI продолжает отправлять один JSON object; такой запрос несовместим
  с текущим WMS контрактом и должен получить 422;
- два параллельных update одного `(guid, product_id)` не сериализованы;
- status filter отключен 16.06.2026: WMS bridge обрабатывает документы любого
  `event_status`, но не реализует отмену/распроведение как reversal;
- нет структурной связи receipt → movement: только `product_id`, время и
  `reason` с номером документа.

Данные целевой БД согласуются с Git history: test delta-adjust movements
датированы моментом появления bridge 16.03.2026; несколько production snapshot
updates 07–08.07.2026 не имеют correction movements. Последнее прямо
объясняется веткой `available=0`, которая обновляет snapshot без HTTP movement.

## 2. Точки входа

### 2.1 Активный входящий flow из 1С

| Entry point | Статус | Назначение | Доказательство |
|---|---|---|---|
| `POST /api/receipt_of_goods/update` | активен | входящий список документов 1С; legacy replacement, затем WMS bridge | handler [`create_data`](/home/skurbick/PROJECTS/1CRoutingAPI/app/api/v1/endpoints/receipt_of_goods.py:23), router registration [`main.py`](/home/skurbick/PROJECTS/1CRoutingAPI/main.py:108) |
| `GET /api/receipt_of_goods/get_receipt_of_goods?guid=...` | активен, read-only | возвращает valid legacy snapshot | [`get_receipt_of_goods`](/home/skurbick/PROJECTS/1CRoutingAPI/app/api/v1/endpoints/receipt_of_goods.py:13) |
| `POST /api/receipt_of_goods/add_incoming_receipt` | заглушка | задуман local acceptance → legacy → outgoing 1С | handler возвращает literal «стоит заглушка», service не вызывается: [`receipt_of_goods.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/api/v1/endpoints/receipt_of_goods.py:49) |

Auth dependency для receipt router закомментирован
([`receipt_of_goods.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/api/v1/endpoints/receipt_of_goods.py:8)).
В самом приложении route не защищен token dependency.

### 2.2 Другие механизмы

Полный поиск по current tree, всем Git branches и соседней копии
`PROJECTS/окружение/1CRoutingAPI` не нашел других writers
`wms.receipt_items`, receipt consumers, cron/Celery jobs или stored functions.
RabbitMQ в проекте относится к генерации документов/стикеров, не к receipt.

`ReceiptOfGoodsService.add_incoming_receipt()` и исходящий
`ONECRouting.receipt_of_goods_update()` существуют, но активный endpoint их не
вызывает из-за заглушки. Исходящий URL — `ONE_C_BASE_URL + 'inc_invoice/'`
([`routing.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/infrastructure/ONE_C/routing.py:55)).

### 2.3 Какая реализация используется сейчас

Dependency wiring без feature flag всегда создает `WMSIntegrationService` и
передает его в `ReceiptOfGoodsService`
([`dependencies/receipt_of_goods.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/dependencies/receipt_of_goods.py:20)).
Поэтому для current HEAD активен flow:

```text
POST /api/receipt_of_goods/update
  -> ReceiptOfGoodsService.create_data
  -> ReceiptOfGoodsRepository.update_data
  -> WMSIntegrationService.process_receipts
```

Точно определить deployed Git branch по репозиторию нельзя. Current checkout и
`origin/new_sticker_service` указывают на `4b3a5fe`; `origin/master` —
`ec00d71`. Receipt WMS flow присутствует в обеих ветках; заметное отличие HEAD —
отключенный status filter.

## 3. Входной контракт

### 3.1 HTTP

- Method/path: `POST /api/receipt_of_goods/update`.
- Body: JSON array `List[ReceiptOfGoodsUpdate]`.
- Успех: HTTP 201, `ReceiptOfGoodsResponse`.
- Pydantic validation: HTTP 422.
- Legacy PostgreSQL error: repository создает response status 422; endpoint
  преобразует его в HTTPException 422.
- WMS per-receipt ошибки обычно не меняют HTTP status: они собираются в stats,
  а endpoint возвращает legacy success 201.

Response:

```json
{"status": 201, "message": "Успешно", "details": "... или null"}
```

Schema: [`ReceiptOfGoodsResponse`](/home/skurbick/PROJECTS/1CRoutingAPI/app/models/receipt_of_goods.py:133).

### 3.2 Document fields

Фактическая schema
[`ReceiptOfGoodsUpdate`](/home/skurbick/PROJECTS/1CRoutingAPI/app/models/receipt_of_goods.py:100):

| Поле | Тип | Required | Использование |
|---|---|---:|---|
| `guid` | string | да | document identity в legacy и WMS snapshot |
| `document_number` | string | да | legacy header, receipt snapshot, movement reason |
| `document_created_at` | datetime | да | legacy `supply_to_sellers_warehouse`; legacy `incoming_documents.doc_date` |
| `update_document_datetime` | datetime | да | legacy history row; WMS bridge игнорирует |
| `supply_date` | datetime | да | legacy history row; WMS bridge игнорирует |
| `event_status` | string | да | legacy history; с 16.06.2026 WMS filter отключен |
| `author_of_the_change` | string | да | legacy history и `movements.user_name` |
| `our_organizations_name` | string | да | только legacy history |
| `supplier_name` | string | да | legacy, snapshot, movement reason |
| `supplier_code` | string/null | нет | legacy/snapshot; код `9714053621` исключает WMS и legacy incoming_items |
| `order_guid` | string/null | нет | legacy history, не WMS |
| `currency` | string/null | нет | legacy history, не WMS |
| `supply_data` | array | да | строки документа |

`document_created_at` — единственное поле, прямо названное датой создания
документа; `supply_date` — дата поставки; `update_document_datetime` — дата
изменения источника. Ни одно из них не сохраняется в `wms.receipt_items` или
`wms.movements`.

Нет document revision/version, event id, payload hash, cancellation boolean,
deleted flag или warehouse/location field.

### 3.3 Line fields

[`SupplyData`](/home/skurbick/PROJECTS/1CRoutingAPI/app/models/receipt_of_goods.py:85):

- required: `local_vendor_code: str`, `product_name: str`, `quantity: float`,
  `amount_with_vat: float`;
- optional: `amount_without_vat`, `planned_cost`, `pack_count`,
  `pack_multiplicity`.

Нет stable line id, batch, container, status, location/warehouse или line
updated timestamp. У `quantity` нет `gt/ge` validation: schema допускает zero и
отрицательное число. WMS API позднее преобразует значение `int(quantity)`, то
есть дробная часть теряется.

### 3.4 Единственный фактический payload example

В коде есть `example_receipt_of_goods_data`
([`models/receipt_of_goods.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/models/receipt_of_goods.py:20)).
Он содержит один document и две одинаковые строки `wild123`. В handler example
закомментирован, но данные доступны Swagger schema как module constant только
если вручную подключить Body example.

Других receipt fixtures/Postman collections/бизнес-тестов в репозитории нет.
Самостоятельные payload для отсутствующих сценариев в этом отчете не
выдумываются.

## 4. Call graph и способы интеграции

```text
main.app
└─ POST /api/receipt_of_goods/update
   └─ endpoint.create_data(data)
      └─ ReceiptOfGoodsService.create_data(data)
         ├─ ReceiptOfGoodsRepository.update_data(data)
         │  ├─ SELECT products
         │  ├─ TX1: UPDATE/INSERT supply_to_sellers_warehouse
         │  └─ TX2: UPDATE incoming_items
         │          INSERT incoming_documents
         │          INSERT incoming_items
         │          └─ DB trigger sync_incoming_items()
         │             └─ INSERT inventory_transactions
         └─ WMSIntegrationService.process_receipts(data)
            └─ for each document
               ├─ SELECT products
               └─ for each input line
                  ├─ SELECT wms.receipt_items by guid/product
                  ├─ new: HTTP POST WMS /api/movements [receive effects]
                  │       └─ WMS TX + INSERT wms.movements
                  │          └─ trigger updates wms.inventory
                  │     then direct INSERT wms.receipt_items
                  └─ existing: SELECT wms.inventory/location
                        HTTP POST WMS adjust when applicable
                        then direct UPDATE wms.receipt_items
```

Способ взаимодействия комбинированный:

- direct SQL через один configured asyncpg pool в shared PostgreSQL для
  `public.*`, `wms.receipt_items`, `wms.inventory`, `wms.locations`;
- HTTP к WMS Service для `wms.movements`;
- PostgreSQL trigger внутри WMS меняет `wms.inventory`;
- PostgreSQL trigger legacy `incoming_items` пишет `inventory_transactions`.

Нет RabbitMQ, PG function call или другого internal service в receipt path.

## 5. SQL и изменяемые таблицы

### 5.1 Legacy warehouse

SQL находится в
[`ReceiptOfGoodsRepository.update_data`](/home/skurbick/PROJECTS/1CRoutingAPI/app/database/repositories/receipt_of_goods.py:72).

TX1:

```sql
UPDATE supply_to_sellers_warehouse
SET is_valid = false
WHERE guid = ANY($1) AND is_valid = true;

INSERT INTO supply_to_sellers_warehouse (..., is_valid, ...)
VALUES (..., true, ...);
```

TX2:

```sql
UPDATE incoming_items
SET is_valid = false
WHERE guid = ANY($1) AND is_valid = true;

INSERT INTO incoming_documents (...)
VALUES (...)
ON CONFLICT (guid) DO NOTHING;

INSERT INTO incoming_items (guid, product_id, quantity, price, is_valid)
VALUES (..., true);
```

Фактический DB trigger `tr_incoming_sync AFTER INSERT OR UPDATE` вызывает
`public.sync_incoming_items()`:

- insert valid item → positive `inventory_transactions`, type `incoming`;
- true→false → negative transaction, type `adjustment`;
- warehouse всегда hard-coded `target_warehouse_id := 1`, status `1`.

Следовательно, identical replay legacy-документа создает reversal старых строк
и новые положительные transactions, хотя net balance может остаться прежним.

### 5.2 WMS snapshot writer

Единственный current writer —
[`WMSReceiptRepository`](/home/skurbick/PROJECTS/1CRoutingAPI/app/database/repositories/wms_receipt_repository.py:11):

| Writer | Файл/функция | Операция | Вызывает movement | Transaction | Используется сейчас |
|---|---|---|---|---|---|
| `create_receipt_item` | repository lines 49–85 | INSERT | нет; caller вызывает HTTP раньше | отдельный acquire/autocommit | да |
| `update_receipt_item_quantity` | lines 87–111 | UPDATE quantity | нет; caller может вызвать HTTP раньше или не вызвать | отдельный acquire/autocommit | да |
| DELETE writer | не найден | DELETE | — | — | нет |
| timestamp trigger | target DB `trg_receipt_items_updated_at` | переписывает `updated_at` | нет | transaction UPDATE | да |

В actual DB нет stored function/trigger, создающего receipt rows или movement.
Migration/script/test/admin writer в обоих репозиториях не найден. Соседняя
копия окружения WMS writer не содержит.

### 5.3 WMS movement

1CRoutingAPI не выполняет `INSERT wms.movements` напрямую. Он вызывает URL из
required setting `WMS_API_URL_MOVEMENTS`
([`config.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/dependencies/config.py:51)).

WMS endpoint в current WMS принимает только массив `List[MovementCreate]`, 1–500
строк. WMS создает весь HTTP batch в одной PostgreSQL transaction; AFTER INSERT
trigger изменяет `wms.inventory`.

## 6. Первоначальное поступление

Условие WMS-new: SELECT по `(guid, product_id)` не нашел snapshot
([`get_receipt_item`](/home/skurbick/PROJECTS/1CRoutingAPI/app/database/repositories/wms_receipt_repository.py:17)).

Последовательность current code:

1. До WMS legacy repository загружает все `products.id`; unknown product не
   попадает в legacy `incoming_items`, но остается в
   `supply_to_sellers_warehouse`.
2. Legacy TX1 и TX2 commit.
3. WMS bridge еще раз загружает все `products.id`.
4. Supplier `9714053621` пропускается. Status не фильтруется.
5. Каждая неизвестная строка пропускается с warning; весь документ не
   отклоняется.
6. Все новые input lines складываются в `new_items`, затем разбиваются по 500.
7. Для batch отправляется массив receive movements.
8. WMS transaction вставляет movements; inventory trigger создает/увеличивает
   available loose stock в `PUSHKINO-ПРИЁМКА`.
9. После успешного HTTP response 1CRoutingAPI последовательно делает отдельный
   INSERT snapshot для каждой строки.
10. Handler возвращает legacy `201` и, при наличии, статистику в `details`.

Заполняемый movement:

| Поле | Значение/источник |
|---|---|
| `movement_type` | literal `receive` |
| `product_id` | `supply_data.local_vendor_code` |
| `quantity` | `int(supply_data.quantity)` |
| `from_location` | null |
| `to_location_code` | constant `PUSHKINO-ПРИЁМКА` |
| `user_name` | `author_of_the_change` |
| `reason` | `Поставка {document_number} от {supplier_name}` |
| batch/container | null |
| metadata/source_* | не передаются, остаются null |
| created_at | WMS DB `now()`, не document date |

Заполняемый `wms.receipt_items`:

- guid/product/quantity — input;
- document number/supplier name/code — input header;
- created_at/updated_at — DB defaults `now()`;
- external dates, author, event status, location и movement id не сохраняются.

## 7. Повторное получение и корректировка

### 7.1 Increase 10 → 15

Code вычисляет `diff = 15 - 10 = 5`
([`_adjust_receipt_item`](/home/skurbick/PROJECTS/1CRoutingAPI/app/service/wms_integration_service.py:261)).
Затем отправляет `adjust`, `to_location_code=PUSHKINO-ПРИЁМКА`, quantity 5,
reason `Корректировка поставки {document_number}: 10.0 → 15.0`. После успешного
HTTP выполняется UPDATE snapshot до 15.

Новый `receive` на 15 не создается.

В current code correction helper отправляет JSON object, тогда как current WMS
ожидает array. Поэтому фактический current pair репозиториев должен вернуть WMS
422 до snapshot UPDATE. В период 16–25.03.2026 WMS endpoint принимал object, и
этот flow работал.

### 7.2 Decrease 15 → 4

`required_decrease=11`. Перед HTTP выполняется aggregate SELECT суммы всего
available stock SKU в `PUSHKINO-ПРИЁМКА`. Batch/container не фильтруются, lock
не берется.

- available ≥ 11: отправляется outgoing adjust 11;
- 0 < available < 11: отправляется outgoing adjust только на available,
  добавляется warning о shortage;
- available = 0: HTTP movement не вызывается, только warning.

Во всех трех ветках после успешного/пропущенного HTTP snapshot устанавливается
в `new_quantity=4`. Поэтому snapshot может отражать полную correction, а
inventory — только частичную или никакую.

Если товар уже перемещен/отгружен/скомплектован/пересортирован/контейнеризирован,
его может не быть loose в fixed receipt location. Код не ищет товар в других
locations и не связывает decrease с остатком конкретного receipt. Container и
batch dimensions игнорируются в precheck; movement посылается с null
batch/container и затрагивает только exact loose inventory key WMS trigger-а.

### 7.3 Без изменения quantity

При `diff == 0` method возвращается до HTTP и snapshot UPDATE:

- movement не создается;
- `wms.receipt_items.updated_at` не меняется;
- supplier/document metadata snapshot также не обновляется;
- caller все равно увеличивает счетчик `adjusted_movements` после возврата.

Это weak idempotency только для одной существующей пары quantity. Legacy flow
при этом уже создал новую version и reversal/reapply transactions.

### 7.4 Ошибки

`process_receipts` ловит исключение на уровне document, добавляет его в
`stats.errors` и продолжает следующий document. `ReceiptOfGoodsService` не
добавляет `errors` в response details. Поэтому caller может получить HTTP 201,
не увидев WMS failure в response.

## 8. Добавление, удаление и замена SKU

### Добавление SKU

Если `(guid, new_product)` отсутствует, создается новый receive и snapshot row.
Это работает как patch-add независимо от других строк документа.

### Удаление SKU / исчезновение строки

Legacy flow сначала инвалидирует все старые rows guid и вставляет только input,
поэтому исчезновение означает удаление из current legacy snapshot и создает
negative legacy transaction.

WMS bridge перебирает только входные строки. Он не читает все snapshot rows guid
и не делает set difference. Поэтому отсутствующий SKU остается в
`wms.receipt_items`, и WMS reversal не создается.

### Quantity zero

Pydantic разрешает zero. Для new SKU bridge формирует receive quantity 0, но
фактическая WMS БД проверяет новые movements `quantity > 0`, а current Pydantic
MovementCreate требует `ge=1`; HTTP отклоняется. Для existing SKU old>0 → 0
работает как decrease; при available=0 snapshot станет 0 без movement.

### Замена SKU A → B

Stable line id отсутствует, поэтому код видит только:

- B присутствует во входе и отсутствует в WMS snapshot → add/receive B;
- A отсутствует во входе → WMS ничего не делает с A.

То есть replacement не распознается. Legacy full replacement отменит A и
добавит B; WMS оставит A и добавит B.

### Повторяющиеся SKU

Schema и пример разрешают duplicates. Current WMS-new flow собирает обе строки
до создания snapshot, отправляет два receive movements, затем два INSERT
snapshot. Первый INSERT commit, второй нарушает actual unique
`(guid, product_id)`. Movements уже commit, поэтому возможны doubled inventory и
частичный snapshot.

Если snapshot уже существует, строки обрабатываются последовательно. Каждая
читает quantity после предыдущего autocommit UPDATE, поэтому фактически
последняя input-строка становится current quantity, а movements отражают цепочку
между значениями.

### Header changes

Legacy version сохраняет новый document number/supplier/dates. WMS existing
snapshot обновляет только quantity; document number/supplier fields остаются от
первого insert. При unchanged quantity WMS snapshot вообще не обновляется.

### Отмена/распроведение/удаление документа

Отдельного flow нет. До `75d2b7a` WMS принимал только `Проведен/Проведён`. После
этого commit status filter закомментирован, поэтому любой status обрабатывается
как обычный receipt update. Empty `supply_data` не удаляет WMS rows. DELETE
receipt writer отсутствует.

## 9. Location mapping

В input нет warehouse/location. Legacy trigger всегда использует
`warehouse_id=1`.

WMS bridge всегда использует class constant:

```python
RECEIPT_LOCATION = "PUSHKINO-ПРИЁМКА"
```

([`wms_integration_service.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/service/wms_integration_service.py:41)).

WMS Service разрешает code через `wms.locations` и сохраняет location id в
movement/inventory. `wms.receipt_items` location не хранит. Первоначальную
location можно восстановить только эвристически из receive movement; guid в
movement отсутствует.

Повторное сообщение не может сменить location через контракт. Изменить constant
можно только deployment/code change; existing snapshot не хранит, где был
первый receive.

## 10. Transaction boundary и частичный успех

### 10.1 Реальные границы

1. Product list read — отдельный connection/autocommit.
2. Legacy TX1 — supply history replacement.
3. Legacy TX2 — incoming replacement + trigger transactions.
4. WMS HTTP batch — отдельная transaction внутри WMS Service.
5. Каждый `wms.receipt_items` INSERT/UPDATE — новый pool acquire, implicit
   autocommit statement.
6. HTTP response 1CRoutingAPI — после best-effort WMS processing.

Даже TX1 и TX2 legacy являются двумя последовательными transactions на одном
connection, а не одной transaction. Если TX2 падает, TX1 уже commit.

### 10.2 Возможные асимметрии

- legacy commit, WMS unavailable → legacy обновлен, WMS нет, HTTP обычно 201;
- movement commit, timeout/response lost → snapshot не создан; retry создаст
  duplicate receive;
- movement commit, snapshot unique/DB failure → inventory изменен, marker нет;
- batch receive commit, snapshot INSERT падает на середине → все movements и
  часть snapshots;
- multi-batch document → ранние batches commit, поздний падает;
- partial decrease → snapshot full-new, inventory partial-new;
- available=0 decrease → snapshot изменен без movement;
- WMS movement failure → snapshot обычно не обновляется, но legacy уже commit;
- два legacy transactions могут расходиться между собой.

Rollback действует только внутри каждой локальной transaction. Retry receipt
HTTP/outbox/saga/compensation отсутствуют. HTTP client timeout 30 seconds; после
timeout нельзя определить, commit-нул ли WMS запрос.

## 11. Concurrency и idempotency

### 11.1 Защиты

- actual unique `(wms.receipt_items.guid, product_id)`;
- legacy `incoming_documents.guid` PK;
- нет `SELECT FOR UPDATE`, advisory lock, optimistic version, revision column,
  idempotency key, payload hash или lock по guid;
- inventory availability SELECT не блокирует строки;
- документы обрабатываются последовательно только внутри одного request/task,
  но разные HTTP requests выполняются параллельно.

### 11.2 Два concurrent updates: 10 → 15 и 10 → 20

Оба могут прочитать old=10. A создаст +5, B создаст +10. Затем snapshot UPDATE
last-writer-wins: 15 или 20. Inventory станет old stock +15, хотя корректный
результат относительно snapshot должен быть +5 либо +10. Если current
object-vs-array mismatch срабатывает, оба WMS calls упадут, snapshot останется
10, но legacy оба раза будет переверсионирован.

Для concurrent first receipt оба не увидят marker, оба могут commit receive
movements. Один snapshot INSERT победит, второй получит unique violation.

### 11.3 Повтор identical message

- legacy: old rows invalidated, negative transactions созданы, новые rows и
  positive transactions вставлены;
- WMS existing pairs: diff=0, movements/snapshot UPDATE нет;
- WMS отсутствующий marker после partial failure: receive повторяется.

Идемпотентность условна и зависит от сохраненного marker; request-level
idempotency отсутствует.

## 12. Проверка товара, остатков и старая складская схема

### 12.1 Product validation

Оба слоя выполняют `SELECT id FROM products` и membership check. Не проверяется
`is_active`. Unknown SKU:

- сохраняется в `supply_to_sellers_warehouse`;
- не попадает в `incoming_items`;
- пропускается WMS bridge;
- не делает весь request ошибочным;
- отображается только числом skipped products в response details.

### 12.2 Decrease stock validation

Availability query суммирует `status='available'` по product + fixed location,
без batch/container и без lock
([`wms_receipt_repository.py`](/home/skurbick/PROJECTS/1CRoutingAPI/app/database/repositories/wms_receipt_repository.py:113)).

Исходящий movement всегда null batch/container, поэтому фактически WMS trigger
ищет loose exact inventory row. Aggregate precheck может включить container или
batch rows, которые movement не сможет уменьшить. Для `adjust` WMS trigger при
полном отсутствии matching row не бросает explicit exception; movement может
остаться в ledger без inventory decrease. 1CRoutingAPI проверяет только HTTP
status, не проверяет получившийся stock.

### 12.3 Legacy warehouse effects

В actual DB:

- `supply_to_sellers_warehouse` — append versions с `is_valid`;
- `incoming_documents` — один header по guid; `ON CONFLICT DO NOTHING` означает,
  что number/date/supplier после первого insert не обновляются;
- `incoming_items` — versions с `is_valid/valid_from/valid_to`;
- trigger пишет `inventory_transactions`, fixed warehouse 1.

Итак, `old warehouse + wms.receipt_items + wms.movements` не являются одной
операцией. Legacy ledger и WMS ledger могут асимметрично изменяться.

## 13. Связи и аудит

Структурной связи receipt ↔ movement нет:

- `source_type/source_id/source_item_id` не передаются;
- movement id не сохраняется в receipt item;
- receipt item id/guid не сохраняются в movement;
- `metadata` не передается;
- link table отсутствует.

Эвристическая связь:

```text
receive reason:
  Поставка {document_number} от {supplier_name}

adjust reason:
  Корректировка поставки {document_number}: {old} → {new}

partial adjust reason:
  Частичная корректировка поставки {document_number}: -{actual} из -{required}
```

Raw входной payload полностью пишется в rotating application log
`app/logs/wms_integration.log` на DEBUG, но не в БД. Файл max 10 MB, пять
backups; это не устойчивый audit. Legacy current/history rows сохраняют часть
payload, включая external dates и author. WMS сохраняет author только в
movement; WMS snapshot — нет.

Нет revision/event id, retry count, durable processing status/error или audit
неуспешной WMS попытки. `stats.errors` живет только в памяти/log и не полностью
возвращается caller.

## 14. Git history и подтвержденные расхождения

### 14.1 Ключевые commits

| Commit | Дата | Изменение |
|---|---|---|
| `c560fa2` | 2026-03-16 02:47 +03 | добавлены WMSIntegrationService, direct receipt repository, per-item receive/adjust |
| `51ab160` | 2026-03-16 02:59 +03 | исправлено имя setting на `WMS_API_URL_MOVEMENTS` |
| `7352223` | 2026-03-19 | добавлен full payload rotating log |
| `1511afc` | 2026-03-25 10:11 +03 | new receives переведены на bulk array; corrections оставлены single object |
| WMS `403234a` | 2026-03-25 10:15 +03 | WMS POST `/movements` изменен с object на required array |
| `75d2b7a` | 2026-06-16 | отключен filter `Проведен/Проведён`, WMS получает все statuses |
| `4b3a5fe` | 2026-07-20 | receipt outgoing 1С изолирован в background task; incoming WMS algorithm не менялся |

До `c560fa2` writer `wms.receipt_items` в Git history отсутствует. Версии,
которая сначала UPDATE-ит WMS snapshot, а затем всегда вызывает movement, в Git
не найдено. Весь delta-adjust flow с момента появления делает movement раньше
snapshot, кроме shortage branch `available=0`, где movement отсутствует.

### 14.2 Связь с actual WMS data

На 2026-07-22 actual DB содержит 1 393 receipt rows и 1 397 receive movements.
Девять rows имеют заметный UPDATE после create.

- три test rows 16.03 имеют receive + adjust chains и reasons из
  `c560fa2`; это период до WMS bulk-only contract;
- шесть production rows 07–08.07 имеют changed snapshot и только original
  receive. Их new quantity меньше original; одна стала zero.

Наиболее прямое объяснение этих шести строк по current code: после перемещения
товара из `PUSHKINO-ПРИЁМКА` availability стала zero; shortage branch не вызвала
HTTP и UPDATE snapshot успешно выполнился. Это вывод из кода и timestamps, а не
сохраненный audit конкретного request, потому что durable attempt log отсутствует.

Production данные действительно могли быть записаны разными совместимостями:

- 16–25 марта: object correction совместим со старым WMS endpoint;
- после 25 марта: new receive bulk совместим, correction object несовместим;
- после 16 июня: обрабатываются все event statuses;
- branch `available=0` не зависит от WMS correction HTTP и продолжает менять
  snapshot.

## 15. Фактические примеры

### 15.1 Что найдено

Единственный входной example — document с двумя одинаковыми `wild123` в
[`example_receipt_of_goods_data`](/home/skurbick/PROJECTS/1CRoutingAPI/app/models/receipt_of_goods.py:20).
Он демонстрирует multi-SKU-array и разрешенные duplicate SKU, но не является
тестом ожидаемого результата.

В target DB найдены test document numbers `ПС-TEST-001` и `ПС-COMPLEX-001` с
реальными chains:

- initial receive;
- increase adjust;
- decrease adjust;
- partial decrease adjust.

Payload этих вызовов не хранится в репозитории/БД, поэтому он не приводится.

### 15.2 Отсутствующие примеры

В tests, Swagger Body config, fixtures, Postman и docs не найдены отдельные
payload/expected DB assertions для:

1. repeat identical message;
2. increase;
3. decrease;
4. add SKU;
5. remove SKU;
6. unknown product;
7. insufficient stock.

Фактические вызванные methods для этих сценариев описаны в разделах 6–8, но
искусственные payload не создавались.

## 16. Sequence diagrams

### 16.1 Первоначальное поступление

```text
1С
  -> POST 1CRoutingAPI /api/receipt_of_goods/update
  -> Pydantic List[ReceiptOfGoodsUpdate]
  -> ReceiptOfGoodsService.create_data
  -> ReceiptOfGoodsRepository.update_data
       -> TX1 legacy: invalidate + insert supply_to_sellers_warehouse
       -> COMMIT TX1
       -> TX2 legacy: invalidate incoming_items
                      insert incoming_documents/items
                      DB trigger -> inventory_transactions warehouse=1
       -> COMMIT TX2
  -> WMSIntegrationService.process_receipts
       -> SELECT products
       -> SELECT wms.receipt_items marker
       -> HTTP POST WMS /api/movements [receive effects]
            -> WMS transaction INSERT wms.movements
            -> WMS trigger UPDATE wms.inventory
            -> COMMIT WMS transaction
       -> direct SQL INSERT wms.receipt_items (отдельный autocommit)
  -> HTTP 201 legacy result + optional WMS stats
```

Разрыв: legacy, WMS movement и receipt marker — разные commits.

### 16.2 Корректировка

```text
1С
  -> POST 1CRoutingAPI /api/receipt_of_goods/update
  -> legacy TX1 + TX2 replacement and commits
  -> WMSIntegrationService
       -> SELECT old wms.receipt_items quantity (без lock)
       -> diff = new - old
       -> if decrease: SELECT SUM available at PUSHKINO-ПРИЁМКА (без lock)
       -> if physical delta applicable:
            HTTP POST WMS single adjust object
            [НЕСОВМЕСТИМО с current WMS array contract]
            -> при исторически совместимом WMS:
                 movement commit -> inventory trigger
       -> if decrease and available=0: HTTP не вызывается
       -> direct SQL UPDATE receipt snapshot = full new quantity
  -> HTTP 201 даже при большинстве per-document WMS errors
```

Разрыв: нет full-document diff, locking, revision, atomicity и durable failure
record.

## 17. Ответы на ключевые вопросы

1. **Где writer?** В `1CRoutingAPI`:
   `WMSIntegrationService` + `WMSReceiptRepository`.
2. **Точка входа?** `POST /api/receipt_of_goods/update`.
3. **Payload?** JSON array `ReceiptOfGoodsUpdate`; полный schema — раздел 3.
4. **Full snapshot или patch?** Legacy код трактует как full replacement по
   guid; WMS bridge фактически применяет только присутствующие lines как
   upsert/patch. Контракт противоречив.
5. **Первичное поступление?** Отсутствует `(guid, product_id)` marker.
6. **Correction?** `diff=new-old`; positive incoming adjust, negative outgoing
   adjust до available, затем full snapshot UPDATE.
7. **Всегда movement?** Нет: diff zero, unknown product, excluded supplier,
   available zero и ошибки не создают movement.
8. **Snapshot без movement?** Да, явно при negative diff и available zero.
9. **Decrease?** Списывается максимум aggregate available fixed receipt zone;
   shortage не отклоняет snapshot update.
10. **Location?** Hard-coded `PUSHKINO-ПРИЁМКА`; legacy warehouse hard-coded 1.
11. **Original date?** В legacy сохраняются `document_created_at/supply_date`;
    WMS snapshot/movement — нет.
12. **Source edit date?** Legacy сохраняет `update_document_datetime`; WMS — нет.
13. **Revision/event id?** Нет.
14. **Idempotency?** Нет request idempotency; лишь marker+diff zero, при том
    legacy каждый replay переверсионируется.
15. **Snapshot и movement атомарны?** Нет, разные service/connection/commit.
16. **Кто сформировал расхождения?** Git writer после `c560fa2`; test adjusts —
    ранняя object-compatible версия, production snapshot-only — наиболее
    вероятно shortage branch available zero после `75d2b7a`.
17. **Что нужно для future revisions?** Передать external dates/version, хранить
    full raw event, сделать document-level transaction/lock/idempotency,
    structural source links и единый full-document diff; это перечисление
    интеграционных требований, не реализация.
18. **Расширять writer или новый flow?** Current writer содержит несовместимые
    semantics и distributed best-effort chain. Безопаснее создать новый
    versioned receipt application flow и переключить endpoint/producer после
    backfill; повторное использование current endpoint возможно только после
    явного versioned contract и отказа от старой orchestration path.

## 18. Данные, которых не хватает

- Подтверждение, какая Git branch/image сейчас deployed.
- Реальный OpenAPI/1С contract owner и гарантия, что payload — full snapshot.
- Семантика `event_status` и список значений: проведен, отменен, удален и т.д.
- Гарантии порядка `update_document_datetime`; timezone у входных naive dates.
- Наличие stable document revision/event/line id на стороне 1С, не включенных в
  текущий payload.
- Правило duplicates одного SKU и допустимость negative/zero quantities.
- Правило выбора WMS location для разных организаций/складов.
- Production logs request-а для шести snapshot-only changes.
- SLA/retry behavior reverse proxy и 1С при timeout/non-2xx.
- Полное назначение legacy `current_balances` и consumers
  `inventory_transactions`.

## 19. Риски и рекомендации для будущей revision-интеграции

До изменения архитектуры текущий flow следует считать dual-write без
гарантированной согласованности. Особенно рискованны replay после timeout,
parallel update, duplicate SKU, missing SKU, unposted status и decrease после
перемещения товара из receipt zone.

Для подключения будущих `receipt_revisions` необходимо сначала утвердить:

1. Full snapshot contract и stable external document/revision/event/line ids.
2. Mapping трех времен: document/effective, source updated, WMS recorded.
3. Один owner transaction для revision + effects + receipt current projection;
   WMS movement нельзя успешно commit-ить отдельно от revision marker.
4. Document advisory/row lock и inventory row locks.
5. Structural movement link `source_type='receipt_revision'`, source id/item id
   и `(movement_id, created_at)` back-link.
6. Явную политику stale/duplicate/cancel/delete/missing SKU.
7. Решение по legacy warehouse: синхронная атомарная проекция в общей БД либо
   отдельная надежная доставка; текущие две legacy transactions плюс WMS HTTP
   сохранять нельзя как одну «успешную операцию».
8. Migration audit существующих markers/movements с confidence и отдельный
   разбор snapshot-only anomalies без автоматического backdated movement.

Рекомендуемый путь — новый versioned flow рядом с текущим, shadow comparison на
реальных messages, затем controlled cutover. Простое добавление revision INSERT
в текущий `WMSIntegrationService` сохранит его основные race и partial-commit
проблемы.
