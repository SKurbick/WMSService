
---

# ТЗ: мягкий резерв товаров в WMS

## 1. Цель

Реализовать в WMS механизм **мягкого резерва товара по `product_id`** на основании сообщений из RabbitMQ.

Резерв должен показывать, сколько товара уже обещано под внешние заказы, но при этом не должен менять физический складской остаток.

Текущая модель WMS остаётся неизменной:

```text
wms.inventory = физический остаток на складе
wms.movements = event-log физических движений товара
резервы = отдельная сущность, не physical movement
```

Резерв **нельзя** записывать в `wms.inventory` или `wms.movements`, потому что в текущей архитектуре изменения физического остатка должны идти через movement-flow, а прямые изменения inventory запрещены.  

---

## 2. Бизнес-правила

### 2.1. Тип резерва

На первом этапе реализуется только **мягкий резерв**:

```text
product_id + external_order_id
```

Не реализовывать в MVP:

```text
location_id
container_code
batch_number
ячейку
партию
FIFO/FEFO привязку
```

---

### 2.2. Маппинг RabbitMQ

Входящее поле:

```json
{
  "wild": "wild1605"
}
```

считать как:

```python
product_id = item["wild"]
```

`wild` точно соответствует `public.products.id` / `wms.inventory.product_id`.

---

### 2.3. Количество

Бизнес-правило:

```text
1 external_order_id = 1 штука товара
```

Поэтому для MVP:

```text
reserved_qty = 1
```

Но поле `reserved_qty` всё равно использовать в коде и БД, чтобы потом можно было расшириться до `quantity > 1`.

---

### 2.4. Статусы резерва

Активные статусы резерва:

```python
RESERVED_STATUSES = {"new", "processing", "fictitious"}
```

Статусы снятия резерва:

```python
RELEASE_STATUSES = {"shipped", "burned"}
```

Поведение:

```text
new        -> is_reserved = true
processing -> is_reserved = true
fictitious -> is_reserved = true

shipped    -> is_reserved = false
burned     -> is_reserved = false
```

Важно:

```text
shipped только снимает резерв.
shipped НЕ создаёт физическое списание.
```

Физическое списание должно оставаться в существующем FBS-процессе через `ship` movement.

---

## 3. Таблицы БД

SQL можно сделать вручную, но код должен ожидать наличие следующих объектов.

---

### 3.1. Таблица текущего состояния резервов

```sql
wms.stock_reservation_orders
```

Ожидаемые поля:

```sql
reservation_order_id bigserial primary key,

source_type text not null default 'fbs',
product_id text not null,

external_order_id bigint not null,
external_status text not null,

is_reserved boolean not null,
reserved_qty numeric(20,3) not null default 1,

external_created_at timestamptz null,
last_event_at timestamptz not null default now(),

raw_payload jsonb null,

created_at timestamptz not null default now(),
updated_at timestamptz not null default now()
```

Уникальный ключ:

```sql
UNIQUE (source_type, product_id, external_order_id)
```

---

### 3.2. Таблица audit-событий

```sql
wms.stock_reservation_events
```

Назначение: хранить все входящие события из RabbitMQ, включая успешные, повторные, ошибочные, неизвестные статусы и неизвестные товары.

Ожидаемые поля:

```sql
reservation_event_id bigserial primary key,

source_type text not null default 'fbs',
product_id text null,
external_order_id bigint null,
external_status text null,

reserved_qty numeric(20,3) null,

external_created_at timestamptz null,
event_received_at timestamptz not null default now(),

processing_result text not null,
error_message text null,

raw_payload jsonb not null
```

Примеры `processing_result`:

```text
processed
released
unknown_status
product_not_found
invalid_payload
db_error
```

---

### 3.3. View доступности товара

```sql
wms.v_product_availability
```

Важно: `free_qty` может быть отрицательным.

Это бизнес-смысл: отрицательный свободный остаток показывает нехватку товара под активные резервы.

Ожидаемые поля view:

```text
product_id
physical_qty
reserved_qty
free_qty
shortage_qty
```

Формула:

```text
physical_qty = SUM(wms.inventory.quantity) WHERE status = 'available'
reserved_qty = SUM(wms.stock_reservation_orders.reserved_qty) WHERE is_reserved = true
free_qty = physical_qty - reserved_qty
shortage_qty = GREATEST(reserved_qty - physical_qty, 0)
```

Пример:

```json
{
  "product_id": "wild1605",
  "physical_qty": 10,
  "reserved_qty": 15,
  "free_qty": -5,
  "shortage_qty": 5
}
```

---

## 4. RabbitMQ-обработка

### 4.1. Формат входящего сообщения

```json
[
  {
    "wild": "wild1605",
    "orders": [
      {
        "order_id": 12345,
        "status": "new",
        "created_at": "2026-05-22T10:30:00+03:00"
      }
    ]
  }
]
```

---

### 4.2. Основная логика обработки

Для каждого элемента массива:

```python
product_id = item["wild"]
orders = item["orders"]
```

Для каждого заказа:

```python
external_order_id = order["order_id"]
external_status = order["status"]
external_created_at = order.get("created_at")
reserved_qty = 1
```

Если статус в `RESERVED_STATUSES`:

```text
создать или обновить запись в stock_reservation_orders
is_reserved = true
reserved_qty = 1
external_status = текущий статус
last_event_at = now()
raw_payload = исходное событие
```

Если статус в `RELEASE_STATUSES`:

```text
создать или обновить запись в stock_reservation_orders
is_reserved = false
reserved_qty = 1
external_status = текущий статус
last_event_at = now()
raw_payload = исходное событие
```

Повторное сообщение должно быть идемпотентным.

То есть повтор:

```text
product_id=wild1605
external_order_id=12345
status=new
```

не должен увеличить резерв второй раз.

Для этого использовать UPSERT по:

```text
(source_type, product_id, external_order_id)
```

---

### 4.3. Неизвестный product_id

Если `product_id` не найден в `public.products`:

```text
не создавать / не обновлять stock_reservation_orders
записать событие в stock_reservation_events
processing_result = product_not_found
ACK RabbitMQ-сообщение
```

Это бизнес-ошибка, а не инфраструктурная ошибка.

---

### 4.4. Неизвестный статус

Если статус не входит ни в `RESERVED_STATUSES`, ни в `RELEASE_STATUSES`:

```text
не менять stock_reservation_orders
записать событие в stock_reservation_events
processing_result = unknown_status
ACK RabbitMQ-сообщение
```

Не игнорировать неизвестные статусы молча.

---

### 4.5. ACK/NACK правило

```text
успешно обработали событие -> ACK
записали бизнес-ошибку в audit -> ACK
ошибка БД / транзакции / инфраструктуры -> NACK или retry
```

Примеры:

```text
processed           -> ACK
released            -> ACK
unknown_status      -> ACK
product_not_found   -> ACK
invalid_payload     -> ACK, если audit удалось записать
db_error            -> NACK/retry
connection_error    -> NACK/retry
```

---

## 5. API endpoints

Добавить read-only endpoints в раздел inventory.

Существующий API уже использует `/api/inventory` для операций чтения остатков, поэтому новые endpoints должны лечь туда же. 

---

### 5.1. Доступность товара

```http
GET /api/inventory/product/{product_id}/availability
```

Назначение: вернуть физический остаток, активный резерв, свободный остаток и нехватку.

Ответ:

```json
{
  "product_id": "wild1605",
  "physical_qty": 10,
  "reserved_qty": 15,
  "free_qty": -5,
  "shortage_qty": 5
}
```

Если товара нет в availability view, вернуть нули:

```json
{
  "product_id": "wild1605",
  "physical_qty": 0,
  "reserved_qty": 0,
  "free_qty": 0,
  "shortage_qty": 0
}
```

---

### 5.2. Список резервов

```http
GET /api/inventory/reservations
```

Назначение: read-only просмотр текущих резервов.

Фильтры:

```text
product_id
external_order_id
is_reserved
external_status
source_type
older_than_hours
limit
offset
```

Примеры:

```http
GET /api/inventory/reservations?product_id=wild1605&is_reserved=true
GET /api/inventory/reservations?is_reserved=true&older_than_hours=72
GET /api/inventory/reservations?external_order_id=12345
```

`older_than_hours` должен фильтровать по `last_event_at`.

Например:

```text
is_reserved=true&older_than_hours=72
```

означает: показать активные резервы, у которых последнее событие было больше 72 часов назад.

Автоматически снимать такие резервы нельзя.

---

### 5.3. Audit событий резерва

```http
GET /api/inventory/reservation-events
```

Назначение: read-only просмотр входящих событий RabbitMQ по резервам.

Фильтры:

```text
product_id
external_order_id
external_status
processing_result
source_type
date_from
date_to
limit
offset
```

`date_from/date_to` фильтруют по `event_received_at`.

---

## 6. Слои кода

Проект использует layered architecture:

```text
endpoints -> services -> repositories -> SQL queries
```

SQL-запросы лежат отдельно, работа с БД идёт через `asyncpg`, ORM не используется. 

Нужно придерживаться существующего стиля проекта.

---

### 6.1. Schemas

Добавить Pydantic-схемы примерно для:

```text
StockReservationOrderResponse
StockReservationEventResponse
ProductAvailabilityResponse
ReservationListQueryParams
ReservationEventsQueryParams
RabbitReservationMessage
RabbitReservationOrder
```

---

### 6.2. Repository

Добавить repository для резервов.

Примерные методы:

```python
product_exists(product_id: str) -> bool

upsert_reservation_order(...)
insert_reservation_event(...)

get_product_availability(product_id: str)

list_reservations(
    product_id: str | None,
    external_order_id: int | None,
    is_reserved: bool | None,
    external_status: str | None,
    source_type: str | None,
    older_than_hours: int | None,
    limit: int,
    offset: int,
)

list_reservation_events(
    product_id: str | None,
    external_order_id: int | None,
    external_status: str | None,
    processing_result: str | None,
    source_type: str | None,
    date_from,
    date_to,
    limit: int,
    offset: int,
)
```

---

### 6.3. Service

Добавить service, который содержит бизнес-логику:

```python
class StockReservationService:
    RESERVED_STATUSES = {"new", "processing", "fictitious"}
    RELEASE_STATUSES = {"shipped", "burned"}

    async def process_rabbitmq_message(...)
    async def process_reservation_order(...)
    async def get_product_availability(...)
    async def list_reservations(...)
    async def list_reservation_events(...)
```

Вся обработка одного RabbitMQ-сообщения должна быть транзакционной насколько это возможно.

---

### 6.4. Endpoints

Добавить endpoints в inventory router или отдельный router внутри inventory-раздела:

```text
GET /api/inventory/product/{product_id}/availability
GET /api/inventory/reservations
GET /api/inventory/reservation-events
```

Все три endpoint’а read-only.

---

## 7. Важные ограничения

Не делать:

```text
не менять wms.inventory напрямую
не создавать wms.movements для резервов
не добавлять movement_type reserve
не делать hard reservation по ячейкам
не делать hard reservation по контейнерам
не делать hard reservation по batch_number
не делать автоснятие резерва по TTL
не делать physical ship movement при статусе shipped
не вмешиваться в существующий FBS write-off flow
```

---

## 8. Документация

После реализации обновить контекстные документы проекта:

```text
docs/context/api_map.md
docs/context/business_rules.md
docs/context/domain_model.md
docs/context/current_state.md
```

Если добавляются новые таблицы/view, обновить также:

```text
docs/context/database_map.md
docs/context/invariants.md
```

---

## 9. Acceptance criteria

Реализация считается готовой, если:

1. Повторное RabbitMQ-сообщение с тем же `source_type/product_id/external_order_id/status` не увеличивает резерв повторно.
2. `new`, `processing`, `fictitious` создают/удерживают активный резерв.
3. `shipped`, `burned` снимают резерв.
4. `shipped` не создаёт movement.
5. Резерв не пишет ничего в `wms.inventory`.
6. Резерв не пишет ничего в `wms.movements`.
7. Неизвестный `product_id` попадает в audit как `product_not_found`.
8. Неизвестный статус попадает в audit как `unknown_status`.
9. `GET /api/inventory/product/{product_id}/availability` возвращает `physical_qty`, `reserved_qty`, `free_qty`, `shortage_qty`.
10. `free_qty` может быть отрицательным.
11. `shortage_qty` показывает положительную нехватку.
12. `GET /api/inventory/reservations` позволяет найти активные, снятые и старые резервы.
13. `GET /api/inventory/reservation-events` позволяет посмотреть audit входящих событий.
14. Бизнес-ошибки ACK’аются после записи audit.
15. Инфраструктурные ошибки БД/транзакции не ACK’аются и должны уходить в retry/NACK.

---

## 10. Короткая версия для Codex

```text
Реализуй мягкий резерв товаров в WMS.

product_id приходит из RabbitMQ в поле wild.
wild точно соответствует public.products.id / wms.inventory.product_id.
1 external_order_id = 1 штука товара.

Резерв не является физическим движением.
Нельзя писать резерв в wms.inventory.
Нельзя создавать wms.movements для резервов.
Не добавлять movement_type reserve.

Ожидается, что в БД уже будут созданы:
- wms.stock_reservation_orders
- wms.stock_reservation_events
- wms.v_product_availability

Нужно реализовать:
1. Pydantic-схемы.
2. SQL queries.
3. Repository.
4. Service.
5. RabbitMQ processing logic.
6. Read-only endpoints:
   - GET /api/inventory/product/{product_id}/availability
   - GET /api/inventory/reservations
   - GET /api/inventory/reservation-events

Статусы:
- new, processing, fictitious -> is_reserved = true
- shipped, burned -> is_reserved = false

UPSERT делать по:
(source_type, product_id, external_order_id)

reserved_qty = 1.

Если product_id не найден:
- не создавать резерв
- записать audit event с processing_result='product_not_found'
- ACK

Если статус неизвестен:
- не менять резерв
- записать audit event с processing_result='unknown_status'
- ACK

Если ошибка БД/транзакции:
- NACK/retry

Availability:
- physical_qty = физический available остаток из wms.inventory
- reserved_qty = активный резерв из wms.stock_reservation_orders
- free_qty = physical_qty - reserved_qty
- shortage_qty = GREATEST(reserved_qty - physical_qty, 0)

Важно:
free_qty может быть отрицательным. Это бизнес-индикатор нехватки товара под активные резервы.

Не делать:
- hard reservation по location/container/batch
- TTL auto-release
- physical ship movement при shipped
- прямые изменения inventory
- movements для резервов
- вмешательство в существующий FBS write-off flow

После реализации обновить docs/context:
- api_map.md
- business_rules.md
- domain_model.md
- current_state.md
- database_map.md
- invariants.md
```
