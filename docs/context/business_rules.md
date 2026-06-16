# Business Rules

Этот файл разделяет правила, подтвержденные DDL `docs/context/wms_schema.sql`, и правила, которые должны обеспечиваться Python-кодом или внешними процессами.

## Подтверждено DDL

### Локации

- `location_code` уникален и генерируется `wms.generate_location_code()` при insert.
- `path` обязателен и генерируется `wms.generate_location_path()` при insert или смене `parent_location_id`.
- Иерархия хранится через `parent_location_id` и LTREE `path`.
- Parent location должен существовать; удаление parent с зависимостями ограничено FK.
- Допустимые `zone_type`: `receiving`, `storage`, `picking`, `packing`, `shipping`, `quarantine`, `NULL`.
- DDL не запрещает неактивного parent и не задает допустимый диапазон `level`.

### Остатки и движения

- `wms.movements` - event log; `wms.inventory` - materialized state.
- Insert в `movements` автоматически обновляет `inventory`.
- `to_location_id` увеличивает остаток; `from_location_id` уменьшает остаток.
- Inventory row удаляется при `quantity <= 0`.
- `inventory.quantity` не может быть отрицательным.
- Inventory уникален по `product_id/location_id/status/batch_number/container_code` с `NULLS NOT DISTINCT`.
- `movement_type` ограничен набором `receive`, `putaway`, `transfer`, `pick`, `ship`, `unpack`, `adjust`.
- DDL не требует положительного `movements.quantity` и не требует заполнения хотя бы одной стороны movement.

### Контейнеры

- `containers.qr_code` уникален.
- Допустимые типы: `pallet`, `box`, `unit`.
- Допустимые статусы: `sealed`, `opened`, `empty`, `blocked`.
- Контейнер может быть вложен в другой контейнер.
- Active содержимое контейнера при insert создает `receive` movement и через него inventory.
- Перемещение контейнера по `location_id` создает `transfer` movements для всех inventory rows с `container_code = qr_code`.
- `block_empty_container` блокирует контейнер только если нет active contents.

### Распаковка

- `unpack_from_container` должна уменьшать content, создавать пару `unpack` movements и открывать контейнер.
- По DDL есть конфликт: функция использует `quantity=0` и `status='empty'`, но constraints `container_contents` это запрещают.

### Заявки и FBS

- Допустимые `task_type` и `tasks.status` enforced check constraints.
- Task может иметь parent task; task items каскадно удаляются с task.
- FBS shipment/item statuses enforced check constraints.
- Retry state хранится в `retry_count`, `max_retries`, `next_retry_at`; для pending retry есть partial index.

## Зависит от Python-кода или внешнего процесса

- Запрет дочерней локации под неактивным parent.
- Запрет размещения в неактивную локацию.
- Batch movements all-or-nothing и лимит batch size.
- Создание task минимум с одной позицией и положительным `quantity_planned`.
- Жизненный цикл assign/start/complete/cancel/approve/recount и права пользователей.
- Уведомление approvers и логика `public.user_permissions`.
- FIFO/FEFO рекомендации.
- FBS consumer, RabbitMQ ACK/NACK, Pydantic validation, группировка по `product_id`, retry worker/backoff.
- Атомарность отметки `public.assembly_task.is_shipped` и создания movement.

## Мягкие резервы товаров

- Резерв является отдельной сущностью и не является физическим движением товара.
- Резерв нельзя записывать в `wms.inventory` и нельзя отражать через `wms.movements`.
- MVP резервируется только по `product_id + external_order_id`; location, container, batch/FIFO/FEFO не используются.
- Входящее RabbitMQ-поле `wild` трактуется как `product_id` и должно соответствовать `public.products.id`.
- Для MVP один `external_order_id` означает `reserved_qty = 1`.
- Статусы `new`, `processing`, `fictitious` делают резерв активным (`is_reserved=true`).
- Статусы `shipped`, `burned` снимают резерв (`is_reserved=false`).
- `shipped` только снимает мягкий резерв и не создает физическое списание. Физическое списание остается в существующем FBS `ship` movement flow.
- Неизвестный товар записывается в audit как `product_not_found` без изменения текущего состояния резервов.
- Неизвестный статус записывается в audit как `unknown_status` без изменения текущего состояния резервов.
- `free_qty` в доступности товара может быть отрицательным; это показывает нехватку под активные резервы.
- `older_than_hours` в списке резервов только фильтрует по `last_event_at`; автоснятие резерва по TTL не выполняется.

### Availability API

- JSON-поля `physical_qty`, `reserved_qty`, `free_qty`, `shortage_qty` в availability responses отдаются числами, а не строками Decimal.
- `GET /api/inventory/availability` читает `wms.v_product_availability` и не изменяет `wms.inventory`, `wms.movements` или резервы.
- `only_shortage=true` показывает только строки с `shortage_qty > 0`.
- `only_reserved=true` показывает только строки с `reserved_qty > 0`.
- `GET /api/inventory/availability/totals` считает `shortage_qty` как `SUM(shortage_qty)` по строкам availability, а не как `GREATEST(SUM(reserved_qty) - SUM(physical_qty), 0)`.
- `GET /api/inventory/location/{location_id}/availability` считает physical quantity внутри subtree локации через `wms.locations.path <@ parent.path`, а reserved quantity берет глобально по `product_id`.
- Для location availability: `free_qty = physical_qty_in_location_subtree - reserved_qty_global`, `shortage_qty = GREATEST(reserved_qty_global - physical_qty_in_location_subtree, 0)`.

## External FBS write-off

- Standard и external-detected FBS используют одну бизнес-логику и одну FBS location.
- `fbs_shipments.source` задается consumer-ом, а не определяется из payload.
- Movement создается только если атомарно захвачены все уникальные assembly tasks группы через `UPDATE ... RETURNING`. Дубли assembly tasks запрещены.
- `assembly_task.is_shipped`, movement, inventory trigger и success/movement_id всех items группы атомарны.
- Повторное списание уже отгруженной assembly task запрещено.
- Если `settings.FBS_VALIDATE_ASSEMBLY_TASKS = False`, FBS flow не читает и не обновляет `public.assembly_task`; это тестовый режим для контуров без таблицы/данных СЗ. Pydantic-контракт payload сохраняется: `assembly_tasks` обязательны и `quantity == len(assembly_tasks)`.
