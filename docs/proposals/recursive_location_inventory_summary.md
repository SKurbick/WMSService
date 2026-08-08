# Recursive Location Inventory Summary Proposal

Статус: `PROPOSAL`; будущая доработка, не текущий контракт.

## Понимание задачи

Нужен read-only метод, который по `location_id` адреса или зоны берет саму эту локацию и все дочерние локации ниже по дереву, находит в них `wms.inventory` и возвращает суммарные остатки, сгруппированные по `product_id`.

Если передать ID зоны, стеллажа, секции или любой промежуточной локации, ответ должен показывать агрегированный остаток внутри этого узла дерева, а не только строки `inventory.location_id = location_id`.

## Текущая ситуация

Готового публичного inventory-метода для рекурсивной сводки остатков по адресу сейчас не найдено.

Существующие inventory endpoint'ы:

- `GET /api/inventory/location/{location_id}` возвращает остатки только в конкретной локации.
- `GET /api/inventory/location/by-code/{location_code}` также работает через конкретную локацию.
- `GET /api/inventory/location/{location_id}/loose` возвращает россыпь только в конкретной локации.
- `GET /api/inventory/summary` возвращает глобальную сводку через `wms.v_product_stock`, без фильтра по дереву локации.

В `app/infrastructure/database/queries/inventory.py` запрос `GET_INVENTORY_BY_LOCATION` фильтрует строго:

```sql
WHERE i.location_id = $1
```

Рекурсивная логика по дереву уже используется в других местах:

- `GET_CHILDREN_RECURSIVE` в `app/infrastructure/database/queries/locations.py` использует `l.path <@ parent.path`.
- `GET_SUGGESTIONS` в `app/infrastructure/database/queries/tasks.py` ищет остатки ниже `tasks.from_location_id`.
- `GET_PRODUCT_QTY_IN_ZONE` в `app/infrastructure/database/queries/tasks.py` считает сумму по одному товару внутри зоны через `l.path <@ (...)`.

## Предлагаемый endpoint

Основной вариант:

```http
GET /api/inventory/location/{location_id}/recursive-summary
```

Причина: `GET /api/inventory/summary` уже занят глобальной сводкой, а `recursive-summary` явно отделяет новый метод от текущего `GET /api/inventory/location/{location_id}`, который возвращает детальные строки только по одной локации.

Альтернативный вариант:

```http
GET /api/inventory/location/{location_id}/summary
```

Он короче, но менее явно показывает, что учитываются дочерние адреса.

## Предлагаемый SQL

```sql
SELECT
    i.product_id,
    p.name AS product_name,
    p.category,
    SUM(i.quantity) AS total_quantity,
    COUNT(DISTINCT i.location_id) AS locations_count,
    COALESCE(SUM(i.quantity) FILTER (WHERE i.container_code IS NOT NULL), 0) AS in_containers,
    COALESCE(SUM(i.quantity) FILTER (WHERE i.container_code IS NULL), 0) AS loose,
    MAX(i.updated_at) AS last_updated
FROM wms.inventory i
JOIN wms.locations l ON i.location_id = l.location_id
JOIN public.products p ON i.product_id = p.id
WHERE l.path <@ (
    SELECT path
    FROM wms.locations
    WHERE location_id = $1
)
  AND i.quantity > 0
GROUP BY i.product_id, p.name, p.category
ORDER BY p.name;
```

`l.path <@ parent.path` включает саму родительскую локацию. Для этой задачи это ожидаемое поведение: "на текущей зоне включая ее детей".

## Предлагаемая схема ответа

Минимальный вариант в стиле текущих inventory endpoint'ов:

```python
class InventoryLocationSummaryResponse(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    category: Optional[str] = None
    total_quantity: int
    locations_count: int
    in_containers: int = 0
    loose: int = 0
    last_updated: Optional[datetime] = None
```

Ответ:

```json
[
  {
    "product_id": "SKU-1",
    "product_name": "Товар 1",
    "category": "cat",
    "total_quantity": 42,
    "locations_count": 5,
    "in_containers": 30,
    "loose": 12,
    "last_updated": "2026-05-20T10:00:00Z"
  }
]
```

Расширенный вариант, если клиенту нужно получать метаданные исходной локации вместе со списком:

```json
{
  "location_id": 123,
  "location_code": "ZONE-A",
  "items": [
    {
      "product_id": "SKU-1",
      "product_name": "Товар 1",
      "category": "cat",
      "total_quantity": 42,
      "locations_count": 5,
      "in_containers": 30,
      "loose": 12,
      "last_updated": "2026-05-20T10:00:00Z"
    }
  ]
}
```

Для текущего стиля API практичнее начать со списка `List[InventoryLocationSummaryResponse]`.

## Предлагаемые изменения в коде

- `app/infrastructure/database/queries/inventory.py`: добавить SQL-запрос для recursive summary.
- `app/infrastructure/database/repositories/inventory_repository.py`: добавить метод `get_location_recursive_summary(location_id)`.
- `app/core/services/inventory_service.py`: добавить метод `get_location_recursive_summary(location_id)` с проверкой существования локации.
- `app/core/schemas/inventory.py`: добавить response schema.
- `app/api/v1/endpoints/inventory.py`: добавить endpoint.
- `docs/context/api_map.md`: добавить endpoint в карту API.
- `docs/context/api_gap_analysis.md`: при необходимости добавить строку с read-only low-risk endpoint.

Миграция БД для самого endpoint'а не нужна, если используется существующее поле `wms.locations.path`.

## Рекомендации

1. Проверять существование локации в service перед расчетом, как уже сделано для `get_inventory_by_location`.

2. Не менять поведение существующего `GET /api/inventory/location/{location_id}`. Новый метод лучше добавить отдельным endpoint'ом, потому что форма ответа другая: агрегированная сводка, а не детальные строки inventory.

3. Рассмотреть query-параметры только если они нужны клиенту:
   - `status: Optional[str]` для `available/damaged/quarantine`;
   - `category: Optional[str]`;
   - `include_zero: bool = False`, хотя по текущей модели нулевые inventory rows обычно удаляются триггером.

4. Добавить тесты минимум на сценарии:
   - остатки только в самой локации;
   - остатки в дочерних адресах;
   - несколько дочерних уровней;
   - несколько `product_id`;
   - контейнерный и loose остаток;
   - несуществующий `location_id`.

5. Проверить наличие индекса на `wms.locations.path`. Для `l.path <@ parent.path` нужен GiST/GiN индекс по `path`. Если индекса нет, стоит добавить отдельную миграцию на индекс.

6. Учитывать конкурентность: endpoint read-only, явные блокировки не нужны. Это будет снимок данных на момент SELECT в обычной изоляции PostgreSQL. Для отображения остатков это нормально, но для резервирования, списания или принятия write-решений такой метод использовать нельзя без отдельной транзакционной write-логики.

## Открытые вопросы

- Нужен ли фильтр по `status`, или метод должен всегда возвращать все статусы? - по всем статусам 
- Нужно ли разделять результат по `status`, `batch_number` или `container_code`, либо нужна только сумма по `product_id`? - сумма product_id
- Должен ли endpoint возвращать метаданные исходной локации (`location_id`, `location_code`, `name`) или достаточно списка остатков? - список остатков
- Должна ли сводка включать неактивные дочерние локации, если в них есть остатки? - да
- Нужен ли аналогичный endpoint по `location_code`, например `GET /api/inventory/location/by-code/{location_code}/recursive-summary`? - думаю нет
