# API Gap Analysis

Источник: `docs/context/api_map.md`, `app/api/v1/endpoints/`, SQL/service context in `app/infrastructure/database/queries/`, `known_issues.md`, `write_operations_policy.md`.

Базовый API prefix: `/api`. Системные root endpoints без prefix: `/`, `/health`.

Risk scale:

- `low` - read-only или ограниченная write-операция без прямого влияния на остатки.
- `medium` - write-операция со статусами/иерархией/служебными данными или read endpoint, результат которого легко использовать неверно.
- `high` - операция меняет остатки, movements, контейнерное состояние, FBS retry или системно пересчитывает inventory.

## Existing Endpoints

### Locations

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| POST | `/api/locations` | Создать локацию в иерархии | write | `wms.locations`, triggers `generate_location_code`, `generate_location_path` | medium |
| GET | `/api/locations/zones` | Активные зоны `level=1` | read-only | `wms.locations` | low |
| GET | `/api/locations/zones/tree` | Дерево локаций до `max_level` | read-only | `wms.locations`, LTREE `path` | low |
| GET | `/api/locations/{location_id}` | Локация по ID | read-only | `wms.locations` | low |
| GET | `/api/locations/by-code/{location_code}` | Локация по коду | read-only | `wms.locations` | low |
| GET | `/api/locations/{location_id}/children` | Дочерние локации, direct/recursive | read-only | `wms.locations`, LTREE `path`, `get_child_locations`-style query | low |
| PUT | `/api/locations/{location_id}` | Обновить параметры локации | write | `wms.locations` | medium |
| PATCH | `/api/locations/{location_id}/deactivate` | Деактивировать локацию | write | `wms.locations` | medium |
| GET | `/api/locations/find-available` | Найти ячейку через DB function | read-only advisory | `wms.find_available_location`, `wms.locations`, `wms.inventory`, `public.products` | medium |
| GET | `/api/locations/{zone_id}/qr-codes` | ZIP/PDF QR labels для зоны | read-only/export | `wms.locations` | low |
| GET | `/api/locations/{location_id}/qr-code` | ZIP/PDF QR label локации | read-only/export | `wms.locations` | low |

### Inventory

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/api/inventory/product/{product_id}` | Остатки товара по локациям, партиям, контейнерам | read-only | `wms.inventory`, `wms.locations`, `public.products` | low |
| GET | `/api/inventory/location/{location_id}` | Остатки в локации | read-only | `wms.inventory`, `wms.locations`, `public.products` | low |
| GET | `/api/inventory/location/{location_id}/recursive-summary` | Агрегированные остатки по локации и всем дочерним локациям | read-only | `wms.inventory`, `wms.locations`, `public.products`, LTREE `path` | low |
| GET | `/api/inventory/location/by-code/{location_code}` | Остатки в локации по коду | read-only | `wms.inventory`, `wms.locations`, `public.products` | low |
| GET | `/api/inventory/summary` | Агрегированные остатки | read-only | `wms.v_product_stock`, `public.products`, `wms.inventory` | low |
| GET | `/api/inventory/container/{qr_code}` | Остатки в контейнере | read-only | `wms.inventory`, `wms.locations`, `public.products` | low |
| GET | `/api/inventory/location/{location_id}/loose` | Россыпь в локации | read-only | `wms.inventory`, `public.products` | low |
| GET | `/api/inventory/search` | Поиск по товару/названию/партии/container | read-only | `wms.inventory`, `wms.locations`, `public.products` | low |

### Containers

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| POST | `/api/containers/register` | Зарегистрировать контейнер и содержимое | write | `wms.register_container`, `wms.containers`, `wms.container_contents`, `wms.movements`, `wms.inventory`, `wms.locations` | high |
| GET | `/api/containers/{qr_code}` | Контейнер по QR с содержимым | read-only | `wms.containers`, `wms.container_contents`, `wms.locations` | low |
| PUT | `/api/containers/{container_id}/location` | Переместить контейнер | write | `wms.containers`, trigger `move_container_inventory`, `wms.movements`, `wms.inventory`, `wms.locations` | high |
| POST | `/api/containers/{container_id}/unpack` | Извлечь товар из контейнера в россыпь | write | `wms.unpack_from_container`, `wms.container_contents`, `wms.containers`, `wms.movements`, `wms.inventory` | high |
| PATCH | `/api/containers/{container_id}/status` | Изменить статус контейнера | write | `wms.containers` | medium |
| GET | `/api/containers/{qr_code}/history` | История движений контейнера | read-only | `wms.movements`, `wms.locations` | low |
| GET | `/api/containers/location/{location_id}` | Контейнеры в локации | read-only | `wms.containers`, `wms.container_contents` | low |

### Movements

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| POST | `/api/movements` | Создать batch movements, 1-500 | write | `wms.movements`, trigger `update_inventory_from_movement`, `wms.inventory`, `wms.locations`, `public.products` | high |
| GET | `/api/movements` | История движений с фильтрами | read-only | `wms.movements`, `wms.locations`, `public.products` | low |
| GET | `/api/movements/product/{product_id}` | История движений товара | read-only | `wms.movements`, `wms.locations`, `public.products` | low |

### Tasks

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/api/tasks` | Список заявок с фильтрами | read-only | `wms.v_tasks_with_users`, `wms.tasks`, `wms.task_items`, `wms.locations`, `public.users` | low |
| GET | `/api/tasks/my` | Активные заявки сотрудника | read-only | `wms.v_tasks_with_users`, `wms.tasks`, `public.users` | low |
| GET | `/api/tasks/available` | Свободные pending заявки | read-only | `wms.v_tasks_with_users`, `wms.tasks` | low |
| GET | `/api/tasks/{task_id}` | Детальная карточка заявки | read-only | `wms.tasks`, `wms.task_items`, `wms.get_task_items_summary`, `wms.locations`, `public.products` | low |
| POST | `/api/tasks` | Создать заявку с позициями | write | `wms.tasks`, `wms.task_items`, `wms.locations`, `public.users`, `public.products` | medium |
| PUT | `/api/tasks/{task_id}` | Обновить pending/assigned заявку | write | `wms.tasks`, possibly `wms.task_items` | medium |
| DELETE | `/api/tasks/{task_id}` | Отменить pending/assigned заявку | write | `wms.tasks` | medium |
| PUT | `/api/tasks/{task_id}/assign` | Взять заявку в работу | write | `wms.tasks`, `public.users` | medium |
| PUT | `/api/tasks/{task_id}/start` | Начать выполнение | write | `wms.tasks`, reads `wms.inventory` for warnings | medium |
| PUT | `/api/tasks/{task_id}/complete` | Завершить с фактическими данными | write | `wms.tasks`, `wms.task_items`, `wms.movements`, `wms.inventory`, `wms.notifications`, `wms.locations` | high |
| GET | `/api/tasks/{task_id}/suggestions` | FIFO-подсказки по ячейкам | read-only advisory | `wms.task_items`, `wms.inventory`, `wms.locations` | medium |
| PUT | `/api/tasks/{task_id}/approve-discrepancy` | Подтвердить расхождение | write | `wms.tasks`, `wms.task_items`, `wms.movements`, `wms.inventory`, `wms.locations`, `public.user_permissions` | high |
| PUT | `/api/tasks/{task_id}/reject-discrepancy` | Отклонить расхождение | write | `wms.tasks` | medium |
| PUT | `/api/tasks/{task_id}/recount` | Отправить на пересчет | write | `wms.tasks`, metadata | medium |
| PUT | `/api/tasks/{task_id}/complete-recount` | Завершить пересчет | write | `wms.tasks`, `wms.task_items`, metadata | medium |

### Reports

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/api/reports/zones` | Отчет по зонам | read-only | `wms.locations`, `wms.inventory`, `wms.containers` | low |
| GET | `/api/reports/top-products` | Топ товаров по movements | read-only | `wms.movements`, `public.products` | low |
| GET | `/api/reports/abc-analysis` | ABC-анализ по движениям | read-only | `wms.movements`, `public.products` | low |
| GET | `/api/reports/turnover` | Оборачиваемость | read-only | `wms.movements`, `wms.v_product_stock`/`wms.inventory` | low |
| GET | `/api/reports/batches` | Партии FIFO/FEFO | read-only | `wms.inventory`, `wms.movements`, `wms.locations`, `public.products` | low |

### FBS

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/api/fbs-shipments/stats` | Статистика shipment по статусам | read-only | `wms.fbs_shipments` | low |
| GET | `/api/fbs-shipments` | Список FBS shipments | read-only | `wms.fbs_shipments` | low |
| GET | `/api/fbs-shipments/{shipment_id}` | Детали shipment с raw message и items | read-only | `wms.fbs_shipments`, `wms.fbs_shipment_items` | low |
| POST | `/api/fbs-shipments/retry` | Массовая переобработка validation_failed | write | `wms.fbs_shipments`, `wms.fbs_shipment_items`, `wms.movements`, `wms.inventory`, `public.assembly_task` | high |
| POST | `/api/fbs-shipments/{shipment_id}/retry` | Переобработка одного validation_failed shipment | write | `wms.fbs_shipments`, `wms.fbs_shipment_items`, `wms.movements`, `wms.inventory`, `public.assembly_task` | high |

### Notifications

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/api/notifications/unread` | Непрочитанные уведомления пользователя | read-only | `wms.notifications`, `public.users` | low |
| PUT | `/api/notifications/{notification_id}/read` | Пометить уведомление прочитанным | write | `wms.notifications` | low |

### System/Admin

| Method | Path | Назначение | Mode | Tables/functions | Risk |
|---|---|---|---|---|---|
| GET | `/` | Информация о сервисе | read-only | none | low |
| GET | `/health` | Health check | read-only | database connectivity | low |
| GET | `/api/system/audit-summary` | Count-проверки известных рисков качества данных | read-only audit | `wms.movements`, `wms.inventory`, `wms.containers`, `wms.fbs_shipment_items`, `wms.locations` | low |
| POST | `/api/system/validate-integrity` | Сверить inventory с расчетом из movements | read-only audit | `wms.movements`, `wms.inventory`, `wms.locations` | medium |
| POST | `/api/system/recalculate-inventory` | Удалить и пересчитать inventory из movements | write/maintenance | `wms.inventory`, `wms.movements`, `wms.locations` | high |
| POST | `/api/system/create-snapshot` | Создать snapshot остатков | write/maintenance | `wms.inventory`, `wms.inventory_snapshots` | medium |
| POST | `/api/system/refresh-materialized-views` | Refresh materialized views | write/maintenance | `wms.mv_product_stock` | medium |

## Potential API Gaps

### Read-only gaps

Не предлагать как новые: базовые списки/детали для locations, inventory, movements, containers, tasks, reports, FBS shipments, unread notifications уже есть.

Потенциально полезные read-only endpoints:

- `GET /api/inventory/snapshots` и `GET /api/inventory/snapshots/{snapshot_date}` - просмотр созданных snapshots. Создание snapshot есть, чтения snapshot history нет.
- `GET /api/containers/{qr_code}/contents` - явный read-only endpoint active contents контейнера. Сейчас contents, вероятно, входят в `GET /containers/{qr_code}`, но отдельного endpoint для быстрого списка contents/партий нет.
- `GET /api/tasks/{task_id}/children` - дочерние approval/recount tasks для parent task. Общий список может скрывать child tasks через `hide_child_tasks`.
- `GET /api/fbs-shipments/items` - поиск FBS items по статусу, `next_retry_at`, `product_id`, `movement_id`. Сейчас items доступны только внутри конкретного shipment.
- `GET /api/notifications` - список всех уведомлений пользователя с фильтром `is_read`, сейчас есть только unread.

Closed gap:

- `GET /api/system/audit-summary` добавлен как read-only endpoint для counts по bad movement quantity, movements без направления, orphan `container_code`, orphan FBS movement refs, negative inventory quantity и orphan location parents.
- `GET /api/inventory/location/{location_id}/recursive-summary` добавлен как read-only endpoint для сводки остатков внутри subtree локации.

### Write gaps

Потенциальные write endpoints, которые могут быть полезны, но требуют проверки policy:

- `POST /api/containers/{container_id}/block-empty` - вызов DB function `wms.block_empty_container`. В БД функция есть, API endpoint отсутствует. Из-за read-then-write риска нужен lock или предварительное решение concurrency policy.
- `PUT /api/notifications/read-all` - пометить все уведомления пользователя прочитанными. Низкий риск, если операция ограничена `user_id` и транзакционна.
- `POST /api/fbs-shipments/items/{item_id}/retry` - ручной retry одной FBS item, а не всего shipment. Требует защиты от параллельных retry (`FOR UPDATE SKIP LOCKED`/advisory lock).
- `POST /api/system/run-audit-queries` - сохранить/вернуть результаты audit queries. Если только read-only counts, риск medium/low; если сохраняет результат, нужна отдельная таблица audit runs.

### Dangerous to add before resolving `known_issues.md`

Не добавлять до предварительного решения known issues:

- Любой endpoint для полной распаковки/массовой распаковки контейнера, пока `unpack_from_container` конфликтует с `container_contents` constraints.
- Endpoint для произвольных movements, который допускает `quantity <= 0` или пустые `from/to` стороны. Текущий `POST /api/movements` уже существует, но расширять его нельзя без валидации этих инвариантов.
- Endpoint для прямой правки `wms.inventory`, кроме системного пересчета.
- Endpoint, который использует `find_available_location` как финальную гарантию размещения или capacity reservation.
- Endpoint для переноса subtree locations через смену `parent_location_id`, пока не решено каскадное обновление LTREE path потомков.
- Endpoint, который заполняет или исправляет `fbs_shipment_items.movement_id`, пока нет устойчивой ссылочной модели/FK для partitioned `movements`.
- Endpoint для операций с `container_code`, создающий ссылки на несуществующие `containers.qr_code`.
- Endpoint для параллельных retry/complete/unpack/ship сценариев без row lock или advisory lock.

## Notes on Existing API Risk

- `GET /api/locations/find-available` должен оставаться advisory-only. Он не резервирует место и не должен использоваться клиентом как гарантия размещения.
- `POST /api/containers/{container_id}/unpack` уже существует, но находится в зоне known issue: полная распаковка может падать constraints.
- `POST /api/movements` уже существует, поэтому новые endpoints для movements не нужны; важнее усилить валидацию `quantity` и направлений.
- `POST /api/system/recalculate-inventory` является допустимым исключением из запрета прямого изменения inventory, но это high-risk maintenance operation.

## Three Safest Next API Extensions

1. `GET /api/inventory/snapshots` / `GET /api/inventory/snapshots/{snapshot_date}` - read-only доступ к уже создаваемым snapshots; не меняет остатки и закрывает очевидный read gap после `create-snapshot`.
2. `GET /api/fbs-shipments/items` - read-only поиск FBS items по status/retry/product/movement_id; помогает операторскому контролю retry без новых write risks.
3. `GET /api/notifications` - read-only список уведомлений пользователя с фильтром `is_read`; расширяет существующий `unread` без влияния на остатки.
