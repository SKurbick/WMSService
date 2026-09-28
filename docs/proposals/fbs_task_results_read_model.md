# Результаты обработки FBS по сборочным заданиям

> **Статус: PROPOSAL.** Таблица и API из этого документа пока не реализованы и не
> являются текущим production-контрактом.

Дата проектирования: 2026-09-28.

## 1. Назначение

Документ описывает будущую read-only модель для внешнего клиента, которому нужно
понимать результат обработки каждого сборочного задания (СЗ) внутри FBS payload.

Внешний клиент только читает результаты. Создание и изменение строк выполняет
исключительно WMSService во время обработки RabbitMQ/HTTP FBS payload и retry.

Модель должна отвечать на вопросы:

- было ли конкретное СЗ физически списано;
- было ли оно безопасно пропущено как подтвержденный дубль;
- какое движение изменило остаток;
- почему СЗ не было списано;
- требуется ли ручная сверка.

## 2. Что такое `task_id`

В этом контексте `task_id` — идентификатор сборочного задания Wildberries из
`public.assembly_task.task_id`. Это не идентификатор внутренней складской заявки
`wms.tasks.task_id`.

WMSService не создает исходные сборочные задания. Он получает их номера в поле
`assembly_tasks` FBS payload, проверяет строки в `public.assembly_task` и при
успешном физическом списании атомарно выставляет `is_shipped = TRUE`.

Текущий контракт позиции:

```json
{
  "author": "FBS 2.0",
  "product_id": "wild399",
  "quantity": 3,
  "assembly_tasks": ["5882109569", "5882118664", "5882147946"]
}
```

Для валидного входа `quantity` равно количеству элементов `assembly_tasks`.
Одно уникальное СЗ соответствует одной единице товара.

## 3. Место таблицы в модели

```text
wms.fbs_shipments                         одно входящее сообщение
  └── wms.fbs_shipment_items              одна позиция исходного payload
        └── wms.fbs_shipment_task_results один результат на СЗ позиции
```

Предлагаемая таблица является детализацией существующего item. Она не заменяет:

- `fbs_shipments.raw_message` — неизмененный входной payload;
- `fbs_shipment_items` — позицию, retry и legacy-статус;
- `wms.movements` — единственный источник факта физического изменения остатка;
- `public.assembly_task` — внешний реестр СЗ и его текущий `is_shipped`.

## 4. Предлагаемые поля

| Поле | Тип | Смысл |
|---|---|---|
| `result_id` | `bigserial` | Внутренний идентификатор результата |
| `shipment_id` | `bigint` | Входящее FBS-сообщение |
| `item_id` | `bigint` | Исходная позиция FBS payload |
| `task_id` | `bigint` | Номер сборочного задания Wildberries |
| `product_id` | `varchar` | Товар из позиции payload |
| `outcome` | `varchar` | Итог обработки конкретного СЗ |
| `effect_quantity` | `smallint` | `1`, если текущая обработка создала физическое списание, иначе `0` |
| `movement_id` | `bigint`, nullable | Новое движение текущей обработки |
| `existing_success_item_id` | `bigint`, nullable | Ранее успешный item для подтвержденного дубля |
| `existing_movement_id` | `bigint`, nullable | Ранее созданное движение для подтвержденного дубля |
| `is_shipped_before` | `boolean`, nullable | Значение `assembly_task.is_shipped` до обработки |
| `reason` | `text`, nullable | Краткое диагностическое пояснение |
| `details` | `jsonb` | Дополнительные технические данные без изменения основного контракта |
| `created_at` | `timestamptz` | Первое сохранение результата |
| `updated_at` | `timestamptz` | Последнее изменение результата после retry |

Предлагаемая уникальность: `(item_id, task_id)`. Retry обновляет существующий
результат, а не создает второй текущий итог для той же пары.

FK от `task_id` к `public.assembly_task` намеренно не нужен: таблица должна
сохранять `not_found`, когда соответствующей строки не существует.

`movement_id` и `existing_movement_id` не получают FK, пока у partitioned
`wms.movements` нет гарантированной глобальной уникальности, пригодной для FK.

## 5. Значения `outcome`

| Outcome | Физический эффект текущей обработки | Значение |
|---|---:|---|
| `written_off` | `-1` единица | СЗ было новым и списано текущим movement |
| `duplicate_skipped` | нет | Найден прежний `success` item с существующим movement |
| `inconsistent` | неизвестен | `is_shipped=true`, но подтвержденное FBS-движение не найдено |
| `not_found` | нет | СЗ отсутствует в `public.assembly_task` |
| `pending_retry` | нет | СЗ новое, но движение не создано, например из-за нехватки остатка |
| `failed` | нет | Обработка СЗ завершилась другой ошибкой |

`duplicate_skipped` безопасен только при наличии подтвержденной цепочки
`success item -> movement_id -> movement`. Одного `is_shipped=true` для этого
недостаточно.

`inconsistent` не создает автоматическое повторное списание: это могло бы
привести к двойному уменьшению остатка. Такое СЗ не блокирует обработку новых
СЗ той же товарной группы, но требует сверки.

## 6. Пример смешанной позиции

Входная позиция:

```json
{
  "product_id": "wild399",
  "quantity": 5,
  "assembly_tasks": ["10001", "10002", "10003", "10004", "10005"]
}
```

Установлено:

- `10001` уже успешно списано движением `87001`;
- `10002` и `10003` новые;
- `10004` имеет `is_shipped=true`, но success/movement не найден;
- `10005` отсутствует в `public.assembly_task`.

WMS создает одно новое движение `90001` с `quantity=2`. Результаты:

| task_id | outcome | effect_quantity | movement_id | existing_movement_id | reason |
|---|---|---:|---:|---:|---|
| `10001` | `duplicate_skipped` | 0 | `NULL` | `87001` | Уже успешно списано |
| `10002` | `written_off` | 1 | `90001` | `NULL` | Списано текущей обработкой |
| `10003` | `written_off` | 1 | `90001` | `NULL` | Списано текущей обработкой |
| `10004` | `inconsistent` | 0 | `NULL` | `NULL` | Отгружено без подтвержденного FBS movement |
| `10005` | `not_found` | 0 | `NULL` | `NULL` | СЗ не найдено |

Итог для клиента:

```json
{
  "incoming_quantity": 5,
  "written_off_quantity": 2,
  "duplicate_quantity": 1,
  "inconsistent_quantity": 1,
  "not_found_quantity": 1,
  "requires_reconciliation": true
}
```

## 7. Правила чтения для клиента

Клиент не должен определять изменение остатка только по `outcome` или
`assembly_task.is_shipped`.

- Факт текущего физического списания: `outcome='written_off'`,
  `effect_quantity=1` и существует связанный movement.
- `duplicate_skipped` означает, что текущий запрос остаток не изменял; для
  исторического списания используются `existing_success_item_id` и
  `existing_movement_id`.
- `inconsistent` не означает ни подтвержденное списание, ни подтвержденное
  отсутствие списания.
- `pending_retry`, `not_found` и `failed` не являются физическим движением.
- Сумма `effect_quantity` строк одного `movement_id` должна совпадать с
  `wms.movements.quantity` для соответствующей FBS product group.

## 8. Предлагаемый read-only API

Для внешнего клиента предпочтителен отдельный additive endpoint:

```text
GET /api/fbs-shipments/{shipment_id}/task-results
```

Фильтры первой версии:

```text
product_id, outcome, task_id, limit, offset
```

Пример ответа:

```json
{
  "shipment_id": 160000,
  "summary": {
    "total_tasks": 5,
    "written_off": 2,
    "duplicate_skipped": 1,
    "inconsistent": 1,
    "not_found": 1,
    "pending_retry": 0,
    "failed": 0,
    "effect_quantity": 2,
    "requires_reconciliation": true
  },
  "items": [
    {
      "task_id": 10002,
      "item_id": 400001,
      "product_id": "wild399",
      "outcome": "written_off",
      "effect_quantity": 1,
      "movement_id": 90001,
      "existing_success_item_id": null,
      "existing_movement_id": null,
      "reason": "Списано текущей обработкой",
      "created_at": "2026-09-28T10:00:00Z",
      "updated_at": "2026-09-28T10:00:00Z"
    }
  ]
}
```

Endpoint не принимает `POST`, `PUT`, `PATCH` или `DELETE`. Авторизация должна
использовать существующий read-доступ к FBS-журналу. Прямой доступ внешнего
клиента к таблице не требуется; если он будет разрешен отдельно, DB-роль должна
иметь только `SELECT` на специально подготовленное представление без `details`.

## 9. Обратная совместимость чтения

- Существующие FBS endpoints и их поля не удаляются и не переименовываются.
- Новый endpoint добавляется отдельно и не меняет текущие response schemas.
- Исходные `quantity`, `assembly_tasks`, `raw_message`, item status и retry-поля
  продолжают возвращаться текущими endpoints.
- Для определения фактического количества в новой логике клиент использует
  task results, а не предполагает, что входной `item.quantity` всегда равен
  количеству нового movement в смешанном payload.
- До включения task-level режима отсутствие строк в новой таблице означает
  «детализация не записывалась», а не «СЗ не обрабатывались».

## 10. Ограничения первой версии

- Исторический backfill не выполняется автоматически: надежно восстановить
  исход каждого СЗ из старых смешанных failed items не всегда возможно.
- Таблица описывает результат WMS-обработки, но не заменяет внешний источник
  факта отгрузки Wildberries.
- Автоматическая коррекция `inconsistent` не входит в первую версию.
- Изменение остатков по-прежнему выполняется только через `wms.movements`.

