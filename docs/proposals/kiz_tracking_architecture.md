# Архитектурное направление учёта КИЗ

> **Статус: AGREED DESIGN DIRECTION.** Ограниченная первая версия реализована:
> [действующий контракт KIZ v1](../flows/kiz_v1.md). Movement registry/association,
> физические KIZ movements и container-модель ниже остаются направлением второго этапа.
> SQL KIZ v1 подготовлен на ручную проверку/применение владельцем БД.

## 1. Назначение документа

Документ сохраняет текущий результат архитектурного обсуждения КИЗ, чтобы продолжить
проектирование в новой сессии без повторного исследования базовых решений.

Здесь намеренно не задаются финальные DDL, API-контракты и план миграции. Перед
реализацией они должны быть спроектированы и проверены отдельно на актуальном коде и
целевой PostgreSQL.

## 2. Бизнес-контекст

КИЗ — глобально уникальный идентификатор одной конкретной физической единицы товара.

Зафиксированы следующие правила:

- один КИЗ относится к одной физической единице;
- один `product_id` может одновременно иметь товар с КИЗ и товар без КИЗ;
- КИЗ не является обязательным свойством артикула;
- одна единица конкретного артикула может иметь КИЗ, а другая единица того же артикула
  — не иметь;
- товар может пройти весь складской цикл и быть отгружен без КИЗ;
- КИЗ может быть указан уже в приходных данных;
- если КИЗ отсутствовал при поступлении, его можно присвоить физической единице позже
  непосредственно на складе.

В дальнейшем потребуется учитывать аккаунт владельца товара и приходную
операцию/рейс. Эти области не должны искусственно расширять первый KIZ MVP.

## 3. Главная архитектурная модель

Согласовано следующее разделение ответственности:

```text
wms.movements
    ↓
количественная история

wms.inventory
    ↓
текущий количественный остаток

KIZ
    ↓
поштучная детализация идентифицированной части этого остатка
```

КИЗ не должен добавляться в ключ `wms.inventory`.

Причины:

- inventory должен оставаться компактной количественной проекцией;
- один inventory row может содержать как идентифицированные, так и
  неидентифицированные единицы;
- добавление КИЗ в inventory превратит количественный учёт в поштучный;
- возникнет риск двойного счёта;
- существующие movements, reports, integrity check и recalculate станут значительно
  сложнее.

Пример:

```text
inventory:
wild123 / location A / quantity = 10

KIZ:
KIZ-001 -> wild123
KIZ-002 -> wild123
KIZ-003 -> wild123
```

Интерпретация:

```text
physical = 10
identified = 3
unidentified = 7
```

## 4. Главный инвариант

КИЗ не является независимым вторым складским учётом. Всегда должно выполняться:

```text
identified_quantity <= physical_quantity
```

Проверка выполняется не только по `product_id`. По текущей модели физический stock
scope как минимум включает:

```text
product_id
location_id
inventory status
batch_number
container / loose
```

Точный состав и нормализация stock scope должны быть закреплены при проектировании
KIZ v1. Для первого MVP scope намеренно сужается до available loose stock без партии.

`inventory_id` нельзя использовать как постоянную identity физического остатка:
нулевые inventory rows удаляются, а после пересчёта остатка строка может получить новый
ID.

Логически:

```text
unidentified_quantity =
physical_quantity - identified_quantity
```

## 5. Присвоение КИЗ существующему товару

Если КИЗ наклеивается на уже существующую физическую единицу россыпи:

```text
до:
physical = 10
identified = 2

после:
physical = 10
identified = 3
```

Создавать `wms.movements` не нужно, потому что:

- количество товара не изменилось;
- товар не появился и не исчез;
- location не изменилась;
- изменилась только идентичность существующей физической единицы.

Операция assignment должна быть транзакционной. Она должна блокировать согласованный
stock scope и защищать от конкурентного превышения `identified_quantity` над
`physical_quantity`.

## 6. Расход без явно выбранных КИЗ

Количественная расходная операция без списка КИЗ может расходовать только
неидентифицированную часть остатка.

Пример:

```text
physical = 10
identified = 4
unidentified = 6
```

Тогда:

```text
quantity = 5 -> допустимо
quantity = 6 -> допустимо
quantity = 7 -> недопустимо
```

Такая операция не должна автоматически выбирать, деактивировать или уничтожать
существующие КИЗ. Автоматический выбор КИЗ может быть добавлен позднее только как
отдельно согласованное бизнес-правило.

После появления активных КИЗ существующие расходные write-flow нельзя оставить без
защиты: иначе `wms.inventory.quantity` сможет стать меньше числа активных КИЗ в том же
stock scope.

## 7. История КИЗ

Не следует создавать второй полноценный ledger физических движений параллельно
`wms.movements`.

Целевая модель:

```text
физические количественные движения
        ↓
wms.movements

участие конкретной единицы
        ↓
movement <-> KIZ association

события идентичности и containment
        ↓
небольшой KIZ audit
```

KIZ audit нужен для событий, которые сами по себе не являются количественным
movement, например:

- `registered`;
- `assigned`;
- `replaced`;
- `marked_as_error`;
- `deactivated`;
- помещение в контейнер;
- извлечение из контейнера;
- другие изменения идентичности или direct holder, для которых нет соответствующего
  количественного движения.

Обычные warehouse transfer/ship movements не должны без необходимости дублироваться
отдельными KIZ movement rows.

## 8. Будущая связь KIZ и movement

Association понадобится при подключении физических операций КИЗ.

Пример:

```text
movement quantity = 5

linked KIZ:
KIZ-001
KIZ-002
KIZ-003
```

Интерпретация:

```text
3 identified units
2 unidentified units
```

Для association должны выполняться правила:

- число связанных КИЗ не превышает `movement.quantity`;
- все связанные КИЗ имеют тот же `product_id`, что movement;
- КИЗ действительно находятся в расходном stock scope;
- одна и та же пара KIZ/movement не дублируется;
- association, movement и изменение current state КИЗ выполняются атомарно.

Перед реализацией также нужно подтвердить, для каких товаров один КИЗ соответствует
ровно одной целой учётной единице при существующем `numeric` quantity.

Association не входит в первый изолированный KIZ registry.

## 9. Movement identity

По результатам отдельного аудита принято направление: не использовать
`(movement_id, created_at)` как новый постоянный доменный контракт для KIZ и будущих
сущностей.

Текущее состояние:

- `wms.movements` партиционирована по `created_at`;
- глобального PK/UNIQUE по одному `movement_id` на parent table нет;
- `(movement_id, created_at)` уже используется kit/re-sorting как физическая
  координата ledger row;
- FBS использует только `movement_id` без FK;
- operations-history `event_id` вычисляется read model и не является сохранённой
  identity.

Рекомендуемое долгосрочное направление:

```text
domain entity
      ↓
movement_ref
      ↓
unpartitioned movement registry
      ↓
(movement_id, created_at)
      ↓
partitioned wms.movements
```

`movement_ref` должен стать стабильным single-column reference для:

- KIZ;
- FBS;
- tasks;
- будущих container operations;
- других долгоживущих доменных связей.

При этом:

- существующий `wms.movements` остаётся partitioned quantitative ledger;
- `movement_id` сохраняется для совместимости;
- `(movement_id, created_at)` остаётся внутренней физической координатой ledger;
- переход существующих consumers может выполняться постепенно.

`movement_registry` не блокирует создание первого KIZ registry. Он должен быть готов
до появления:

- KIZ transfer;
- KIZ ship/write-off;
- receive с movement association;
- container movement association;
- movement-backed history KIZ.

Не следует создавать временную KIZ association по `(movement_id, created_at)`, если
целевым контрактом остаётся `movement_ref`.

## 10. Current state КИЗ

Следующее является предварительным направлением, а не финальным DDL.

KIZ должен иметь устойчивое текущее состояние, потенциально включающее:

```text
global KIZ code
product_id
lifecycle/status
batch_number при необходимости
direct holder
source of creation
timestamps
audit metadata
```

Direct holder предполагается как взаимоисключающее состояние:

```text
россыпь:
location_id != NULL
container_id = NULL

контейнер:
location_id = NULL
container_id != NULL

вне склада:
location_id = NULL
container_id = NULL
```

Не следует использовать:

- постоянную ссылку только на `inventory_id`;
- `container_code` как основную FK identity контейнера;
- одновременно direct location и direct container как два независимых источника
  местоположения.

Lifecycle/status КИЗ и `inventory.status` описывают разные аспекты и не должны
автоматически считаться одним полем: первый относится к идентификатору, второй — к
состоянию количественного остатка.

## 11. Контейнеры

Определены бизнес-правила:

- контейнер может содержать товар с КИЗ и без КИЗ;
- контейнер не ограничивается одним аккаунтом;
- товары разных приходных операций могут находиться вместе;
- необходимы вложенные контейнеры;
- при перемещении родительского контейнера всё дерево контейнеров и физического товара
  внутри него перемещается вместе.

Текущий WMS полноценно эту семантику не обеспечивает:

- `parent_container_id` существует;
- полноценного API вложения/извлечения нет;
- cycle protection нет;
- дочерние контейнеры могут иметь независимый `location_id`;
- container move работает только с inventory собственного `container_code`;
- descendants автоматически не перемещаются;
- исторический состав дерева нормально не восстанавливается;
- `unpack_from_container` имеет известный конфликт с constraints и проблемы
  конкурентного доступа.

Поэтому nested containers не входят в первый KIZ MVP.

Целевое направление:

```text
KIZ
 ↓
direct container
 ↓
parent container
 ↓
...
 ↓
root container
 ↓
physical location
```

При перемещении root location отдельных КИЗ не должна копироваться как независимый
источник истины. Для корректной истории понадобится восстанавливаемая история
containment/container operations, но её финальная модель пока не определена.

## 12. Граница первого KIZ MVP

Первый этап должен быть минимальным и относительно изолированным.

Ограниченный stock scope:

```text
inventory.status = available
batch_number IS NULL
container_code IS NULL
loose stock only
```

Первый модуль может включать:

- глобальный реестр КИЗ;
- DB-enforced глобальную уникальность КИЗ;
- связь с `product_id`;
- lifecycle/current state;
- assignment КИЗ существующей loose unit без movement;
- identity audit;
- error/deactivate/replace lifecycle;
- поиск КИЗ;
- список КИЗ товара;
- current state;
- audit/history идентичности;
- integrity check `active KIZ <= physical loose inventory`;
- корректную конкурентную защиту при assignment;
- вычисление `identified_quantity`;
- вычисление `unidentified_quantity`.

Первый MVP пока не включает:

- KIZ/movement association;
- transfer КИЗ;
- ship/write-off КИЗ;
- FBS;
- tasks;
- kit operations;
- re-sorting;
- batch stock;
- containers;
- nested containers;
- автоматический выбор КИЗ при количественном расходе.

Даже при таком scope существующие расходные операции должны быть защищены от
уменьшения stock ниже active identified quantity. Конкретный механизм этой защиты
должен быть спроектирован до запуска KIZ v1.

## 13. Write-flow для последующей адаптации

По актуальному аудиту потенциально затрагиваются:

- `POST /api/movements`;
- task completion;
- discrepancy approval;
- FBS write-off и все retry paths;
- container register;
- container move;
- unpack;
- kit assembly/disassembly;
- re-sorting;
- adjust;
- внешний writer поступлений;
- maintenance и manual movement scripts.

Эти места не должны изменяться в рамках документирования или без отдельного этапа
проектирования.

## 14. Известные технические риски

- нет глобальной DB-enforced movement identity;
- movement registry ещё не реализован;
- обычные расходные flow пока ничего не знают о marked stock;
- нет единого stock-scope locking protocol;
- часть task write-flow неатомарна;
- container tree фактически не обеспечивает требуемую физическую семантику;
- `unpack_from_container` имеет DDL/concurrency проблемы;
- receipt writer в текущем checkout не найден;
- `container_code` не является устойчивой FK-ссылкой;
- отрицательные adjust/re-sorting требуют отдельного контроля identified stock;
- соответствие одного КИЗ одной целой единице при `numeric` quantity ещё нужно
  формально закрепить.

## 15. Открытые вопросы

### Блокируют развитие физического движения КИЗ

- Каков lifecycle КИЗ при `ship` и `write_off`?
- Что происходит с КИЗ при `re-sorting`, когда меняется `product_id` физической
  единицы?
- Что происходит с идентичностью компонентов и результата при kit
  assembly/disassembly?
- Каковы правила КИЗ для batch stock и можно ли менять batch без физического movement?
- Где находится фактический receipt writer и как обеспечить атомарное появление
  movement, inventory effect и КИЗ из приходных данных?
- Как будет реализована association с movements после появления `movement_ref`?
- Для каких SKU один КИЗ всегда соответствует ровно одной целой учётной единице?

Эти вопросы не блокируют изолированный KIZ registry без физических movements, но
должны быть решены до подключения соответствующих write-flow.

### Не блокируют первый registry

- Что делать при конфликте КИЗ из приходного документа и фактически найденного товара?
- Можно ли менять принадлежность физической единицы аккаунту?
- Каковы правила replacement ошибочно наклеенного КИЗ?
- Возможно ли повторное использование ранее закрытого КИЗ?
- Какими будут UI, печать и массовые операции?
- Нужен ли позднее автоматический выбор КИЗ при количественном расходе?

## 16. Статус решения

- Архитектура находится на стадии согласованного design direction.
- KIZ ещё не реализован.
- `movement_registry` ещё не реализован.
- Container redesign ещё не выполнялся.
- Финальные DDL, API и locking protocol не утверждены этим документом.
- Код, DDL, миграции, API и конфигурация не должны считаться изменёнными только на
  основании этого документа.

## Next recommended step

Перед реализацией провести отдельное проектирование минимального KIZ v1: определить
конкретный DDL, API, stock-scope locking и способ защиты `unidentified_quantity`. Не
расширять этот этап movement association, containers, FBS и другими физическими
сценариями.
