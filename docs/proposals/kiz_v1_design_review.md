# KIZ v1: технический design review минимального этапа

> **Статус: HISTORICAL DESIGN REVIEW.** Первая версия реализована с уточнениями ТЗ
> и согласованным MVCC touch. Актуальный контракт: [KIZ v1](../flows/kiz_v1.md).
> Ниже сохранён исходный design review, а не описание текущего API/DDL.
> Migration подготовлена для ручного применения; факт deployment отдельно не утверждается.

## 0. Основания и подтверждённое текущее состояние

Перед review сверены:

- актуальный Python-код endpoints/services/repositories/SQL queries;
- runtime schema-only snapshot production от 2026-08-08;
- миграции kit operations и re-sorting, меняющие `wms.movements`;
- актуальная документация `docs/current`, `docs/database`, `docs/flows`;
- ранее согласованное направление в
  [`kiz_tracking_architecture.md`](kiz_tracking_architecture.md);
- решение по будущему `movement_ref` через непартиционированный movement registry.

Критичные факты текущей реализации:

- `wms.movements` — append-only ledger, partitioned by `created_at`;
- `wms.inventory` — количественная проекция, изменяемая AFTER INSERT trigger на
  movements;
- ключ строки inventory:
  `(product_id, location_id, status, batch_number, container_code)` с
  `NULLS NOT DISTINCT`;
- расход определяется наличием `from_location_id`, а не названием movement type;
- inventory row с нулевым количеством удаляется;
- `recalculate-inventory` напрямую удаляет available inventory и затем вставляет
  рассчитанное из ledger состояние;
- movements создаются общим Python service, kit/re-sorting repositories, FBS/task
  flows и PL/pgSQL container functions/triggers;
- прямые изменения inventory, кроме maintenance recalculate и DB projection trigger,
  не являются нормальным write contract, но технически БД их сейчас не запрещает.

## 1. Рекомендуемая граница KIZ v1

### 1.1. Что входит

KIZ v1 работает только со следующим физическим stock scope:

```text
inventory.status = 'available'
inventory.batch_number IS NULL
inventory.container_code IS NULL
loose stock only
```

Функциональность первого этапа:

- глобальный реестр КИЗ;
- один неизменяемый глобально уникальный `kiz_code` на одну идентифицированную
  физическую единицу;
- assignment нового КИЗ существующей available loose единице без движения товара;
- current state и минимальный lifecycle КИЗ;
- отдельный малый audit событий идентичности;
- чтение карточки КИЗ и списков по товару/локации;
- расчёт physical/identified/unidentified quantity;
- конкурентно безопасный assignment;
- DB-level запрет уменьшить или удалить физический loose stock ниже количества
  активных КИЗ;
- read-only integrity check невозможных состояний.

Один активный КИЗ резервирует ровно `1` из `inventory.quantity`. Поскольку inventory
использует `numeric`, остаток может быть дробным; assignment разрешён только когда
`physical_quantity - identified_quantity >= 1`. Например, при physical `1.5` допустим
один активный КИЗ и останется `0.5` unidentified. Если домен запрещает такую
интерпретацию для конкретных SKU, это должно позднее задаваться единицами измерения
товара, но не требует materialized KIZ counters.

### 1.2. Что не входит

- KIZ/movement association;
- movement registry implementation;
- transfer, ship или write-off конкретных КИЗ;
- явный выбор КИЗ в FBS или tasks;
- batch stock;
- container и nested-container stock;
- damaged/quarantine;
- kit/re-sorting lifecycle КИЗ;
- receipt integration;
- аккаунты и приходные рейсы;
- автоматический выбор активных КИЗ количественной операцией;
- UI, печать и генерация формата КИЗ.

### 1.3. Существенная оговорка к «изолированному» MVP

KIZ registry можно изолировать от movement association, но нельзя изолировать от
защиты inventory. С момента появления первого active КИЗ все пути уменьшения того же
loose stock scope обязаны соблюдать unidentified quantity.

Без DB-защиты запуск registry небезопасен даже при отсутствии KIZ transfer/ship API.

## 2. Целевая модель данных

Для v1 достаточно двух новых доменных таблиц:

```text
wms.kiz
wms.kiz_events
```

Отдельная таблица quantity/counter или stock-scope table не нужна. Канонической
количественной проекцией остаётся `wms.inventory`, identified quantity вычисляется по
active KIZ rows.

### 2.1. `wms.kiz`

Назначение: current state и стабильная identity конкретного КИЗ.

Рекомендуемые поля:

| Поле | Назначение |
|---|---|
| `kiz_id bigint` | Внутренний стабильный surrogate ID |
| `kiz_code varchar/text` | Внешний глобально уникальный код КИЗ |
| `product_id varchar(50)` | Товар физической единицы |
| `location_id bigint` | Текущий/последний direct loose holder v1 |
| `lifecycle_status varchar` | `active`, `error`, `deactivated` |
| `origin_type varchar` | В v1 только `warehouse_assignment` |
| `origin_reference text NULL` | Необязательная внешняя ссылка; в v1 обычно NULL |
| `assigned_at timestamptz` | Момент успешного assignment |
| `closed_at timestamptz NULL` | Момент перехода из active в terminal state |
| `created_at timestamptz` | Создание identity row |
| `updated_at timestamptz` | Последнее изменение current state |
| `created_by varchar(100)` | Автор первоначальной операции |
| `metadata jsonb` | Неразмеченные дополнительные данные, default `{}` |

`created_at` и `assigned_at` в v1 могут совпадать, но имеют разный смысл. Отдельного
состояния «зарегистрирован, но не присвоен» v1 не вводит: identity row создаётся только
в успешной assignment transaction.

`location_id` для active v1 является текущим holder. Для terminal row он сохраняет
последнее известное место assignment как диагностический снимок; terminal row не
участвует в identified count. Исторический факт закрытия и место события сохраняются
в `kiz_events`.

Не включать в v1:

- `inventory_id` — строка inventory удаляется при нуле и пересоздаётся;
- `container_id/container_code` — containers вне scope;
- `batch_number` — batch вне scope;
- `movement_id/movement_ref` — association вне scope;
- materialized `identified_quantity` или `unidentified_quantity`;
- account/receipt/flight FK.

#### Constraints

- PK: `kiz_id`.
- UNIQUE: `kiz_code`.
- FK: `product_id -> public.products(id) ON DELETE RESTRICT`.
- FK: `location_id -> wms.locations(location_id) ON DELETE RESTRICT`.
- CHECK: `btrim(kiz_code) <> ''`.
- CHECK: код не содержит ведущих/замыкающих пробелов; код сохраняется без изменения
  регистра и сравнивается case-sensitive.
- CHECK: `lifecycle_status IN ('active', 'error', 'deactivated')`.
- CHECK: `origin_type = 'warehouse_assignment'` в v1; constraint расширяется вместе с
  receipt integration.
- CHECK: `created_by` непустой.
- CHECK: active требует `closed_at IS NULL`, terminal требует `closed_at IS NOT NULL`.
- `product_id`, `location_id`, `assigned_at`, `created_at`, `updated_at`, `created_by`,
  `metadata` — NOT NULL.
- Hard delete КИЗ не является поддерживаемой операцией.

`kiz_code` после создания неизменяем. Ошибочно введённый код переводится в `error`, а
новая корректная маркировка получает отдельный KIZ row. Это сохраняет глобальную
историю и не требует rename semantics.

#### Индексы

- unique index по `kiz_code`;
- критичный partial index `(product_id, location_id)`
  `WHERE lifecycle_status = 'active'` для identified count и guard;
- `(product_id, lifecycle_status, kiz_id)` для списка товара;
- `(location_id, lifecycle_status, kiz_id)` для списка локации;
- при фактических query plans можно заменить два последних одним подходящим composite
  index; заранее добавлять другие индексы не нужно.

### 2.2. `wms.kiz_events`

Назначение: небольшой immutable audit identity/lifecycle событий, а не второй
складской ledger.

Рекомендуемые поля:

| Поле | Назначение |
|---|---|
| `kiz_event_id bigint` | PK события |
| `kiz_id bigint` | Ссылка на current-state row |
| `event_type varchar` | Тип события v1 |
| `from_status varchar NULL` | Предыдущее состояние |
| `to_status varchar` | Новое состояние |
| `product_id varchar(50)` | Audit snapshot товара |
| `location_id bigint` | Audit snapshot места события |
| `author varchar(100)` | Автор |
| `reason text NULL` | Причина; обязательна для terminal events |
| `metadata jsonb` | Дополнительный audit context |
| `occurred_at timestamptz` | Время события |

Audit snapshots `product_id/location_id` позволяют читать историю без трактовки
текущего состояния как исторического. `kiz_code` дублировать не обязательно, поскольку
код immutable, а hard delete KIZ запрещён.

#### Constraints

- PK: `kiz_event_id`.
- FK: `kiz_id -> wms.kiz(kiz_id) ON DELETE RESTRICT`.
- FK audit snapshot на product/location допустим с `ON DELETE RESTRICT`; если политика
  долгого хранения допускает удаление справочников, вопрос нужно решить отдельно.
- CHECK event type:
  `registered`, `assigned`, `marked_as_error`, `deactivated`.
- CHECK статусов по тому же набору lifecycle values.
- CHECK непустого author.
- CHECK `reason` непустой для `marked_as_error/deactivated`.
- `metadata` default `{}`, `occurred_at` default `now()`.
- UPDATE/DELETE events не входят в поддерживаемый контракт.

#### Индексы

- `(kiz_id, occurred_at, kiz_event_id)` для audit конкретного КИЗ;
- отдельные product/location indexes на events в v1 не нужны без подтверждённых
  запросов.

### 2.3. Почему replacement не входит в lifecycle v1

Правила replacement и повторного использования закрытого кода ещё не согласованы.
Минимальная безопасная последовательность — пометить старый КИЗ как `error` или
`deactivated`, затем assign нового уникального кода той же физической loose unit в
одной доменной транзакции, когда отдельный replacement use case будет утверждён.

До этого момента отдельные `replaced`, `replaced_by_kiz_id` и replacement endpoint не
нужны. Добавление их позднее не ломает выбранные PK, scope и audit model.

## 3. Stock scope и количества

### 3.1. Точное определение scope v1

Канонический ключ:

```text
(product_id, location_id, 'available', NULL batch, NULL container)
```

Это соответствует реальному unique key `wms.inventory`. Благодаря
`UNIQUE NULLS NOT DISTINCT` для такого ключа существует не более одной inventory row.

Location subtree не используется: `location_id` сравнивается точно.

### 3.2. Physical quantity

Логический запрос:

```sql
SELECT quantity
FROM wms.inventory
WHERE product_id = :product_id
  AND location_id = :location_id
  AND status = 'available'
  AND batch_number IS NULL
  AND container_code IS NULL;
```

Отсутствующая строка трактуется как `0`. Для assignment этот запрос выполняется с
`FOR UPDATE`; отсутствие строки означает отказ assignment.

Не следует суммировать все inventory rows товара/локации: это смешает batch,
container и другие statuses.

### 3.3. Identified quantity

```sql
SELECT count(*)
FROM wms.kiz
WHERE product_id = :product_id
  AND location_id = :location_id
  AND lifecycle_status = 'active';
```

Поскольку v1 создаёт active KIZ только в available loose no-batch scope, дополнительных
scope columns для подсчёта v1 не требуется.

### 3.4. Unidentified quantity

```text
unidentified_quantity = physical_quantity - identified_quantity
```

Результат имеет тип numeric, identified — целое. Отрицательный результат является
нарушением integrity, а не допустимым бизнес-состоянием.

### 3.5. Materialization

Не хранить `identified_quantity` и `unidentified_quantity` физически в v1.

Причины:

- physical уже материализован в inventory;
- partial index делает active count дешёвым;
- новый counter потребует отдельной атомарной синхронизации;
- recalculate inventory создаст дополнительный reconciliation problem;
- преждевременная materialization даст третий источник истины.

Materialized counters можно рассматривать только после измерения нагрузки.

## 4. Transaction flow assignment

### 4.1. Рекомендуемый lock anchor

Для ограниченного v1 canonical inventory row является достаточным точным lock anchor.
Assignment возможен только при существующей положительной physical row. После
появления active КИЗ guard не позволит удалить эту строку, пока identified count не
станет нулевым.

Поэтому дополнительный advisory lock в v1 не требуется. Это также избегает нового
lock ordering, который мог бы конфликтовать с kit/re-sorting: они уже берут свои
advisory locks, затем блокируют inventory row.

Если в будущем scope сможет существовать без inventory row, либо holder станет
container tree, понадобится общий advisory/scope-lock protocol. Это второй этап.

### 4.2. Порядок assignment

В одной asyncpg connection и одной DB transaction:

1. Валидировать форму входа: непустые `kiz_code`, `product_id`, `location_code`,
   `author`; код не нормализовать скрыто и запретить surrounding whitespace.
2. Найти product. Существование обязательно. Не запрещать assignment только из-за
   `products.is_active`, пока это не является общим правилом WMS: физический остаток
   неактивного SKU может требовать идентификации.
3. Найти точную location по `location_code`. Существование обязательно. Не вводить
   отдельный запрет inactive location только для КИЗ без общего бизнес-решения.
4. Выполнить предварительный lookup `kiz_code` для понятного conflict response. Он не
   является concurrency protection; окончательную уникальность обеспечивает UNIQUE.
5. Выбрать точную available/NULL-batch/NULL-container inventory row
   `SELECT ... FOR UPDATE`.
6. Если row отсутствует или `quantity <= 0`, вернуть conflict `physical stock not
   found`.
7. Под блокировкой inventory row посчитать active KIZ для `(product_id, location_id)`.
8. Вычислить unidentified. Если `< 1`, вернуть conflict `no unidentified unit`.
9. Вставить `wms.kiz` со статусом `active`. UNIQUE violation по коду преобразовать в
   deterministic `KIZ already exists` conflict.
10. Вставить immutable events `registered` и `assigned`. Они могут иметь одинаковое
    время: первое фиксирует создание identity, второе — привязку к физическому scope.
11. Commit.

Запись KIZ, оба audit event и результат assignment должны откатываться вместе.

### 4.3. Конкурентный пример

Исходно:

```text
physical = 5
identified = 4
```

Две транзакции пытаются assign разные КИЗ:

- первая получает row lock inventory;
- вторая ждёт тот же row;
- первая видит unidentified `1`, создаёт active KIZ и commit;
- вторая после ожидания видит уже `identified = 5`, unidentified `0` и получает
  conflict;
- physical quantity и movements не изменяются.

### 4.4. Terminal transitions

`active -> error` и `active -> deactivated` также должны:

1. открыть transaction;
2. заблокировать KIZ row `FOR UPDATE`;
3. заблокировать соответствующую inventory row `FOR UPDATE` в согласованном порядке;
4. проверить допустимый переход и отсутствие повторной terminal operation;
5. обновить status/closed_at/updated_at;
6. записать event с обязательной reason;
7. commit.

Чтобы не создавать deadlock с assignment/consumption, общий порядок должен быть
формально единым. Рекомендуемый порядок для всех KIZ v1 mutations:

```text
inventory scope row -> KIZ row(s) -> KIZ event(s)
```

Для terminal endpoint сначала известен KIZ, но после его чтения без блокировки нужно
заблокировать inventory row, затем повторно выбрать KIZ `FOR UPDATE` и перепроверить
state. Нельзя удерживать KIZ lock и затем ждать inventory, если assignment/другие
операции используют обратный порядок.

## 5. Защита unidentified quantity

### 5.1. Сравнение вариантов

#### A. Только Python/service checks

Не подходит как authoritative protection.

Не покрывает:

- kit/re-sorting direct SQL repositories;
- PL/pgSQL container functions/triggers;
- maintenance recalculate;
- manual movement/direct inventory SQL;
- будущие writers, которые забудут вызвать helper.

Service check полезен только для понятной ранней бизнес-ошибки.

#### B. Trigger только на INSERT в movements

Подходит лишь частично.

Покрывает все movement writers, если проверяет физический эффект по
`from_location_id/batch_number/container_code`, а не allow-list типов. Но не покрывает
`recalculate-inventory` и прямой UPDATE/DELETE inventory.

#### C. Общий service/repository primitive

Полезен как будущая архитектурная граница, но недостаточен для v1: существующие DB
functions и repositories уже обходят один Python primitive.

#### D. DB-level authoritative protection + service-level validation

Рекомендуемый вариант.

### 5.2. Рекомендуемая DB boundary

Authoritative guard должен находиться на `wms.inventory`, потому что именно изменение
этой проекции является конечным фактом уменьшения physical quantity независимо от
источника.

Guard применяется только к v1 scope:

```text
status = available
batch_number IS NULL
container_code IS NULL
```

Правила BEFORE UPDATE/DELETE:

- увеличение quantity без изменения identity scope пропускается без KIZ count;
- уменьшение quantity блокирует inventory row естественным row lock и проверяет
  `NEW.quantity >= active KIZ count`;
- DELETE разрешён только если active KIZ count равен нулю;
- изменение product/location/status/batch/container рассматривается как удаление из
  OLD scope и запрещается, если там есть active KIZ;
- INSERT физического inventory не нарушает инвариант, поскольку active KIZ без
  предварительно существующего stock в v1 создать нельзя.

Existing movement trigger выполняет UPDATE inventory внутри той же movement insert
transaction. Если inventory guard отклоняет уменьшение, откатываются и projection
update, и movement row, поэтому ledger и projection не расходятся.

Service-level проверки могут заранее считать unidentified и возвращать понятные 409,
но не являются источником истины.

### 5.3. Почему guard не должен ветвиться по movement type

`wms.update_inventory_from_movement()` уменьшает stock для любого movement с
`from_location_id IS NOT NULL`. Он не использует type для вычисления delta. Более того,
DB допускает типы, которых нет в Python enum, а API позволяет некоторые нетипичные
комбинации сторон.

Поэтому защита на inventory physical delta точнее и безопаснее списка типов.

### 5.4. Direct DML и permissions

Inventory guard защищает также manual UPDATE/DELETE. Дополнительно целевой deployment
должен подтвердить DB roles:

- обычный application role не должен иметь произвольный обход triggers;
- отключение triggers, работа superuser/owner и direct writes вне утверждённой
  maintenance procedure остаются административной зоной риска;
- никакая прикладная гарантия не защищает от superuser, намеренно отключившего guard.

### 5.5. Recalculate inventory

Текущий алгоритм сначала удаляет все available rows, затем вставляет их заново. Immediate
DELETE guard закономерно запретит удаление KIZ-bearing scope даже если итоговый
recalculated quantity был бы достаточным.

Поэтому `recalculate-inventory` обязательно адаптируется в KIZ v1. Рекомендуемая
семантика:

1. в одной transaction построить calculated available ledger state;
2. до изменения inventory проверить для всех KIZ-enabled loose scopes:
   `calculated_quantity >= active KIZ count`;
3. при нарушении полностью остановить recalculate с диагностикой scope;
4. upsert рассчитанные строки до удаления obsolete rows;
5. удалить только отсутствующие в calculated state rows; DB guard не даст удалить
   scope с active KIZ;
6. проверить итоговый KIZ integrity перед commit.

Отключать guard на время recalculate или временно допускать broken state не следует.

### 5.6. Стоимость для stock без КИЗ

Поведение WMS сохраняется:

- incoming/increase не делает KIZ count;
- batch/container/non-available scope guard пропускает;
- eligible loose decrease выполняет один indexed count, обычно возвращающий zero;
- при отсутствии active КИЗ результат движения идентичен прежнему.

Это минимальная дополнительная стоимость без отдельного materialized counter.

## 6. Какие physical effects являются расходом

Authoritative критерий:

```text
from_location_id IS NOT NULL
AND batch_number IS NULL
AND container_code IS NULL
```

при проекции в `status='available'`.

Фактические типы/пути:

- `ship` — FBS и ручной movement;
- `transfer` — API movements, tasks и loose transfer;
- `adjust` с `from_location_id` — ручное уменьшение;
- `re_sorting` source/outgoing movement;
- `kit_assembly` component consumption;
- `kit_disassembly` kit consumption;
- `pick` при наличии `from_location_id` — разрешён DB DDL, хотя текущий Python enum не
  предоставляет обычный create contract;
- `putaway` при наличии `from_location_id` — аналогично поддерживается DB DDL;
- `unpack` source movement имеет `container_code` и поэтому не затрагивает loose v1;
  парный incoming loose movement только увеличивает stock;
- `receive` обычно incoming, но нетипичный direct SQL receive с from-side физически
  уменьшит stock и потому также должен быть защищён;
- любой будущий/ручной тип с from-side должен защищаться автоматически.

`write_off` присутствует в Python enum, но отсутствует в актуальном DB movement-type
constraint, поэтому сейчас такой insert должен завершиться constraint violation. Для
KIZ guard отдельная ветка `write_off` не нужна.

## 7. Existing write flows impact

| Flow | Текущее поведение | Риск для KIZ | Менять в v1 | Минимальная корректировка |
|---|---|---|---|---|
| `POST /api/movements` / `MovementService` | Batch movements в одной transaction; inventory через trigger | Любой loose from-side может списать active КИЗ как unidentified | Да, на уровне DB; Python желательно | Inventory guard обязателен; optional precheck/error mapping в service |
| Ручной outgoing `adjust` | Тот же общий movement path; reason только рекомендация | Может уменьшить physical ниже identified | Да | Покрывается inventory guard; вернуть понятный conflict |
| FBS initial/HTTP/retry | `ship`, no batch/container, fixed FBS location; product group atomic | Списывает aggregate loose stock без КИЗ | Да для DB-защиты; selection нет | Guard запрещает расход active части; распознать новый conflict как нехватку unidentified, не бесконечно retry |
| Task complete | Создаёт batch loose transfer после отдельных updates task items | Movement может быть отклонён; task flow уже не полностью атомарен | DB guard — да; полный refactor — нет | Безопасность обеспечивает guard; отдельно корректно отобразить conflict, атомарность task оставить известным риском |
| Discrepancy approval | Movements создаются по одному в отдельных transactions, затем statuses | Возможен частичный business result при позднем KIZ conflict | DB guard — да; orchestration refactor желательно позже | Guard для каждого расхода; задокументировать/обработать conflict, не расширять KIZ v1 крупным task refactor |
| Kit assembly/disassembly | Одна transaction; loose no-batch расход, собственный advisory + inventory `FOR UPDATE` | Precheck учитывает physical, но не active KIZ | Да для guard; KIZ lifecycle нет | Existing row lock совместим; DB guard отклоняет расход identified части; map на 409 |
| Re-sorting | Одна transaction; loose no-batch outgoing, advisory + `FOR UPDATE` | Может переидентифицировать SKU активного КИЗ | Да для guard; KIZ re-sorting нет | Разрешать только unidentified quantity; DB guard, понятный conflict |
| Container register | Создаёт container receive с non-null container_code | Не относится к loose v1 | Нет | Без изменений |
| Container move | Transfer inventory собственного non-null container_code | Не относится к loose v1 | Нет | Без изменений; прежние container-tree риски остаются |
| Unpack | Container outgoing + loose incoming | Loose только увеличивается; active KIZ в контейнерах v1 нет | Нет | Без изменений; существующий DDL/concurrency defect остаётся |
| DB container triggers/functions | Создают movements внутри БД | Текущие source scopes non-loose или increase loose | Нет для scope v1 | Общий inventory guard всё равно является safety net |
| `recalculate-inventory` | DELETE всех available rows, затем rebuild | Временно удаляет KIZ-bearing scope и может восстановить physical меньше identified | Да, обязательно | Preflight against calculated ledger; upsert-first/delete-obsolete; итоговая проверка |
| `validate-integrity` | Сверяет movements и inventory по полному quantitative key | Не сравнивает active KIZ с physical | Существующий endpoint можно не ломать | Добавить отдельный KIZ integrity read path; ledger/inventory check оставить прежним |
| Snapshots/reports/history | Только читают inventory/movements | Не меняют инвариант | Нет | Без изменений |
| Manual `INSERT movements` | Возможен технически; inventory через trigger | Может обойти Python | Нет отдельного кода | Inventory DB guard покрывает |
| Manual UPDATE/DELETE inventory | Не является нормальным contract, но возможен по privileges | Обходит movement semantics и мог бы сломать KIZ | Да на DB boundary | Inventory guard + проверка runtime roles/maintenance policy |
| Внешний receipt writer | В checkout не найден | Неизвестный direct write может затронуть inventory | Не интегрировать в v1 | Guard защищает decreases; incoming не мешает; writer исследовать до receipt KIZ stage |

### 7.1. Минимальный обязательный объём существующих изменений при реализации

Для целостности, а не полной KIZ-интеграции, обязательны:

1. DB guard уменьшения/удаления eligible inventory scope.
2. Адаптация `SystemRepository.recalculate_inventory` и его SQL algorithm.
3. Единое распознавание DB conflict в API/worker boundaries, чтобы KIZ guard не
   превращался в необъяснимый HTTP 500 или бессмысленный retry.
4. Concurrency/integration tests с реальным PostgreSQL для assignment против movement,
   kit, re-sorting и recalculate.

Необязательно в v1 менять payloads или добавлять выбор КИЗ в FBS/tasks/kit/re-sorting.
Они продолжают расходовать только unidentified часть.

## 8. Minimal API

Префикс условно `/api/kiz`; точное именование утверждается в ТЗ.

### 8.1. `POST /api/kiz/assign`

Назначение: зарегистрировать новый глобальный КИЗ и assign существующей loose unit.

Минимальный input:

```text
kiz_code
product_id
location_code
author
metadata? 
```

`origin_type` клиент не передаёт: v1 фиксирует `warehouse_assignment`. Reason для
первичного assignment необязательна и может находиться в metadata.

Валидации/conflicts:

- malformed/empty/surrounding-whitespace code — 422;
- product не найден — 404;
- location не найдена — 404;
- KIZ code уже существует, независимо от его статуса — 409;
- точный physical loose scope отсутствует — 409;
- unidentified quantity `< 1` — 409;
- concurrent duplicate/last-unit race — deterministic 409 после DB serialization;
- DB/integrity failure — отдельная доменная ошибка, не masked success.

Response: карточка созданного active КИЗ и актуальные physical/identified/unidentified
значения после assignment.

Идемпотентность по `kiz_code` намеренно строгая: повтор того же запроса возвращает
conflict, а не создаёт второй объект. Если транспортным retries нужен idempotent replay,
это должно быть явно добавлено через request id; молча считать любой duplicate успехом
опасно при отличающемся product/location.

### 8.2. `GET /api/kiz/{kiz_code}`

Возвращает current state, product/location данные и timestamps. Не обязан возвращать
весь audit.

### 8.3. `GET /api/kiz`

Пагинированный список. Минимальные filters:

- `product_id`;
- `location_id` или `location_code` (выбрать один канонический HTTP contract);
- `lifecycle_status`;
- `limit/offset`.

Чтобы сохранить минимальность, отдельные endpoints «список товара» и «список
локации» не нужны: их покрывают filters.

### 8.4. `GET /api/kiz/{kiz_code}/events`

Пагинированный immutable identity audit.

### 8.5. `POST /api/kiz/{kiz_code}/mark-error`

Input: `author`, обязательная непустая `reason`, optional metadata. Разрешён только
`active -> error`.

### 8.6. `POST /api/kiz/{kiz_code}/deactivate`

Input: `author`, обязательная непустая `reason`, optional metadata. Разрешён только
`active -> deactivated`.

Если бизнес не различает error и обычную деактивацию на старте, endpoints можно
объединить в один explicit status-transition endpoint, но whitelist переходов должен
оставаться серверным.

### 8.7. `GET /api/kiz/stock-summary`

Read-only расчёт для точного `product_id + location`:

```text
physical_quantity
identified_quantity
unidentified_quantity
integrity_ok
```

Этот endpoint полезен оператору перед assignment и не материализует counters.

Итого минимальный контракт: один assignment write, два terminal writes, карточка,
filtered list, events и scope summary. Movement endpoints в v1 не добавляются.

## 9. Audit и integrity

### 9.1. Почему отдельный event audit нужен

Одних `created_at/updated_at` current row недостаточно, чтобы ответить:

- кто назначил КИЗ;
- когда и почему его закрыли;
- был ли terminal transition ошибкой или обычной деактивацией;
- какие metadata относились к конкретному переходу.

`wms.kiz_events` решает это без event sourcing current state. Current row остаётся
authoritative для operational reads; events — immutable audit.

### 9.2. Integrity query

Read-only проверка должна строить множество всех KIZ scopes и LEFT JOIN точную loose
inventory row. Она возвращает случаи:

```text
active_kiz_count > COALESCE(inventory.quantity, 0)
```

Диагностика должна содержать:

- product_id;
- location_id/location_code;
- physical quantity;
- active KIZ count;
- difference;
- признак missing inventory row.

Дополнительные полезные checks v1:

- active KIZ без product/location FK невозможен при валидных FK;
- terminal KIZ с `closed_at IS NULL` или active с non-null closed_at — DB CHECK;
- KIZ row без initial audit events — audit query;
- неизвестный lifecycle/origin — DB CHECK;
- duplicate code — DB UNIQUE.

### 9.3. Размещение проверки

Для первого этапа рекомендуется отдельный read-only endpoint
`GET /api/system/kiz-integrity` с детальной пагинированной выдачей. Существующий
`validate-integrity` имеет другой контракт — movements против inventory — и не должен
смешивать два типа расхождений в одной response schema.

В `audit-summary` позднее можно добавить только агрегированный count нарушений, но это
не заменяет детальную KIZ integrity выдачу.

Integrity endpoint — диагностика, не замена write-time DB guard.

## 10. Риски

### 10.1. Blocking implementation

Следующие пункты должны входить в первое ТЗ; без них v1 нельзя безопасно запускать:

- authoritative DB guard на decrease/delete available loose NULL-batch/NULL-container
  inventory;
- единый lock order `inventory scope -> KIZ rows -> events`;
- assignment под `SELECT inventory ... FOR UPDATE`;
- адаптация destructive `recalculate-inventory`;
- mapping DB guard violation в устойчивую прикладную conflict semantics;
- запрет hidden normalization/изменения регистра `kiz_code`, точная глобальная UNIQUE;
- integration concurrency tests на реальной PostgreSQL, а не только mocked unit tests;
- наличие активной movements partition на время тестов существующих write-flow.

Нерешённых бизнес-вопросов, блокирующих именно предложенный loose assignment-only v1,
после принятия указанных контрактов не остаётся.

### 10.2. Important

- Task complete и discrepancy approval уже имеют неидеальные transaction boundaries;
  KIZ guard сохраняет складской инвариант, но не исправляет partial business state.
- FBS retry должен отличать потенциально временную нехватку обычного stock от запрета
  расходовать active KIZ; иначе возможны бессмысленные retries.
- Kit/re-sorting prechecks показывают physical quantity и могут пройти до DB guard;
  API должен вернуть понятный 409 unidentified shortage.
- Любой superuser/owner может намеренно отключить triggers; нужны проверенные runtime
  roles и maintenance procedure.
- Existing `inventory.quantity` — numeric, а KIZ count — integer; контракт «один КИЗ
  занимает 1 base unit» должен быть явно указан в ТЗ и API docs.
- Production snapshot датирован 2026-08-08; перед миграцией нужен повторный read-only
  schema/data audit целевой БД.

### 10.3. Acceptable technical debt v1

- отсутствие movement registry и KIZ/movement association;
- отсутствие replacement relation;
- terminal location хранится как last-known snapshot;
- отсутствие materialized KIZ counters;
- отсутствие batch/container/status extensions;
- отсутствие receipt source integration;
- отдельный KIZ integrity endpoint вместо объединения с существующим integrity API;
- возможный дополнительный indexed count на каждом eligible loose decrease;
- deadlock retry для редких multi-scope transactions остаётся общей эксплуатационной
  обязанностью; v1 не вводит новый advisory lock graph.

## 11. Совместимость с текущей архитектурой

### Event sourcing movements -> inventory

Не нарушается. Assignment и terminal identity events не меняют количество и не создают
movement. Любой отклонённый inventory projection вызывает rollback исходного movement
insert, поэтому ledger не получает неотражённое событие.

### Partitioned movements и movement identity

KIZ v1 не хранит movement reference, поэтому отсутствие global movement PK и будущий
movement registry не блокируют этап. Временную пару `(movement_id, created_at)` в KIZ
не добавлять.

### Validate integrity

Существующая сверка movements/inventory сохраняется без изменения смысла. KIZ получает
отдельную orthogonal проверку identified/physical.

### Recalculate inventory

Совместимость требует обязательного изменения алгоритма: destructive delete-first
после появления active KIZ неприемлем.

### Asyncpg transaction model

Подходит: assignment выполняется на одной acquired connection внутри
`conn.transaction()`. Нельзя использовать существующий паттерн нескольких repository
calls, каждый из которых самостоятельно acquire-ит connection.

### Existing PostgreSQL functions/triggers

Inventory-level guard покрывает их physical effect независимо от места создания
movement. Container flows v1 не создают active KIZ в container scope и практически не
затрагиваются.

## 12. Что потребуется во втором этапе

Короткая последовательность развития без изменения основы v1:

- реализовать стабильный `movement_ref` и movement registry;
- добавить KIZ/movement association;
- определить lifecycle при transfer/ship/write-off;
- интегрировать receive writer;
- расширить stock scope на batch;
- отдельно переработать container holder/tree/history;
- затем подключать FBS/tasks/kit/re-sorting с явными КИЗ.

Таблицы `wms.kiz` и `wms.kiz_events`, immutable code, current-state/audit split и
вычисляемые quantities при этом сохраняются.

## 13. Итоговая рекомендация

После этого design можно переходить к подготовке первого ТЗ на реализацию KIZ v1 при
условии, что ТЗ включает не только две новые таблицы и endpoints, но также:

1. inventory-level DB guard identified quantity;
2. точный row-lock protocol;
3. безопасный recalculate algorithm;
4. единый mapping conflicts;
5. PostgreSQL integration/concurrency tests.

Если эти пункты исключить ради формально «изолированного» registry, запуск будет
небезопасен: существующий movement или maintenance flow сможет нарушить главный
инвариант.

### Next recommended step

Подготовить реализационное ТЗ KIZ v1 строго в границах этого документа: конкретизировать
DDL двух таблиц, DB guard, recalculate adaptation, asyncpg transaction/repository
boundary, API schemas/errors и набор PostgreSQL concurrency scenarios. Не добавлять в
этот этап movement registry, movement association, containers, receipt, FBS selection,
tasks selection, kit или re-sorting lifecycle.

## 2026-09-06: проверка MVCC и согласованное решение

Inventory row lock без новой row version не защищает от расхода со старым
REPEATABLE READ snapshot. [Воспроизведение и предлагаемая поправка](kiz_v1_lock_protocol_review.md).
Поправка согласована пользователем и реализована: inventory lock → touch → count → KIZ/event.
