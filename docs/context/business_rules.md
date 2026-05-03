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
