# Decisions

## 2026-04-30 - Первичная документация текущего состояния

Создана папка `docs/context/` и первичная карта проекта без изменения рабочего кода.

Основание:

- в репозитории отсутствовала контекстная документация;
- перед будущими изменениями AGENTS.md требует изучать и поддерживать `docs/context/`;
- проект содержит критичные операции с остатками, движениями и адресным хранением, где важно фиксировать текущие правила и неизвестные места.

Решения:

- Документировать только факты, найденные в коде, SQL-запросах, схемах и комментариях.
- Не выдумывать DDL, constraints, таблицы и endpoint'ы.
- Все неподтвержденные детали вынести в `open_questions.md`.
- Так как ORM-моделей и миграций нет, `database_map.md` составлен по `queries`, `repositories` и Pydantic-схемам.

Код приложения не изменялся.


## 2026-05-21 - Recursive inventory summary по subtree локации

Добавлен read-only endpoint `GET /api/inventory/location/{location_id}/recursive-summary`.

Решения:

- Возвращать список агрегированных остатков без метаданных исходной локации.
- Группировать только по `product_id`; `status`, `batch_number` и `container_code` не выделять отдельными строками.
- Включать остатки во всех статусах и в неактивных дочерних локациях.
- Не добавлять endpoint по `location_code`.
- Использовать `wms.locations.path <@ parent.path`, включая саму исходную локацию.
- Не добавлять миграцию: индекс `idx_locations_path USING gist(path)` уже описан в DDL-контексте.

## 2026-05-28 - Мягкие резервы товара по RabbitMQ

Добавлен MVP мягких резервов по `product_id + external_order_id`.

Решения:

- Не использовать `wms.inventory` и `wms.movements` для резервов, чтобы не смешивать обещанный внешний спрос с физическим складским остатком.
- Хранить текущее состояние в `wms.stock_reservation_orders`, а все входящие события и бизнес-ошибки - в `wms.stock_reservation_events`.
- Использовать UPSERT по `(source_type, product_id, external_order_id)` для идемпотентности повторных сообщений.
- Первичная реализация была разделена позднее: stock reservations обрабатываются отдельным consumer и отдельной очередью, чтобы не смешивать их с FBS write-off flow.
- Таблицы и view считаются внешне созданными объектами БД согласно ТЗ; миграция в проект не добавлялась, так как существующего механизма миграций в репозитории нет.

## 2026-05-28 - Отдельная RabbitMQ очередь для мягких резервов

Решено разделить RabbitMQ consumers для FBS списаний и мягких резервов.

Решения:

- FBS write-off consumer продолжает слушать `settings.RABBITMQ_QUEUE`.
- Stock reservation consumer слушает отдельную `settings.STOCK_RESERVATION_QUEUE`.
- Запуск reservation consumer управляется отдельным флагом `settings.RESERVATION_CONSUMER_ENABLED`.
- Ошибки БД при обработке резервов логируются и приводят к NACK/requeue, чтобы отсутствие таблиц или view резервов было видно в логах и не ACK-алось как успешная бизнес-ошибка.

## 2026-06-14 - Внешне обнаруженные FBS-отгрузки используют общий pipeline

Решено принимать внешне обнаруженные отгрузки из отдельной RabbitMQ queue, но обрабатывать их существующим FBS pipeline. Источники разделяются через `wms.fbs_shipments.source`; отдельные shipment/item таблицы не создаются, чтобы сохранить общие retry, историю и movement flow.

В рамках решения атомарная граница product group расширена до `is_shipped + movement + inventory trigger + item success/movement_id`. Shipment status остается отдельным post-group пересчетом.

## 2026-06-16 - Тестовое отключение проверки assembly_task для FBS

Добавлен флаг `settings.FBS_VALIDATE_ASSEMBLY_TASKS` с дефолтом `True`.

Решения:

- По умолчанию сохранить прежнюю защиту: проверка существования assembly tasks, запрет уже отгруженных СЗ и атомарная отметка `public.assembly_task.is_shipped`.
- При `FBS_VALIDATE_ASSEMBLY_TASKS=False` не обращаться к `public.assembly_task` в общем FBS pipeline, включая standard, external-detected и retry/manual reprocessing flows.
- Не ослаблять Pydantic-контракт Rabbit payload: `assembly_tasks` остаются обязательными, `quantity` должен равняться их количеству.
- Миграция не нужна, потому что меняется только runtime-настройка Python-кода.

## 2026-07-01 - validate-integrity считает нетто-остаток по movements

Исправлена логика сверки `POST /api/system/validate-integrity`: расчетная сторона должна строиться как ledger из `wms.movements`, где `to_location_id` дает `+quantity`, а `from_location_id` дает `-quantity`.

Решения:

- Сверять только `wms.inventory.status = 'available'` с нетто-остатком по ключу `(product_id, location_id, status, batch_number, container_code)`; `damaged` и `quarantine` не входят в movement-ledger сверку.
- Использовать `FULL OUTER JOIN`, чтобы видеть строки только в movements и строки только в inventory.
- Сравнивать nullable `batch_number` и `container_code` через `IS NOT DISTINCT FROM`.
- Не менять `POST /api/system/recalculate-inventory` в рамках этого исправления: текущий SQL пересчета использует старый `COALESCE(to_location_id, from_location_id)` подход и требует отдельной безопасной правки перед использованием как maintenance operation.

## 2026-07-01 - recalculate-inventory пересобирает только available по net ledger

Исправлена логика `POST /api/system/recalculate-inventory`: maintenance-пересчет пересобирает только `status='available'` из `wms.movements` по той же ledger-модели, что и live trigger `wms.update_inventory_from_movement()`.

Решения:

- `from_date` временно запрещен, потому что частичный пересчет может удалить inventory шире, чем восстановить movements после даты.
- Перед удалением available inventory выполняется диагностика calculated net остатков; отрицательные значения останавливают транзакцию с диагностикой ключа остатка.
- Удаляются только строки `wms.inventory.status = 'available'`; `damaged` и `quarantine` не трогаются.
- Вставляются только calculated available rows с `quantity > 0.0001`; нулевые остатки не материализуются.
- Ключ пересчета: `(product_id, location_id, status, batch_number, container_code)` без нормализации nullable полей в пустую строку.
