# API Map

Базовый префикс API: `settings.API_V1_PREFIX`, по умолчанию `/api`.

Также есть системные endpoints без этого префикса:

- `GET /` - информация о сервисе.
- `GET /health` - health check.

## Locations

Префикс: `/api/locations`.

- `POST /api/locations` - создать локацию.
- `GET /api/locations/zones` - список активных зон (`level = 1`).
- `GET /api/locations/zones/tree` - дерево локаций с ограничением `max_level`.
- `GET /api/locations/{location_id}` - локация по ID.
- `GET /api/locations/by-code/{location_code}` - локация по коду.
- `GET /api/locations/{location_id}/children` - дочерние локации, рекурсивно или только прямые.
- `PUT /api/locations/{location_id}` - обновить параметры локации.
- `PATCH /api/locations/{location_id}/deactivate` - деактивировать локацию.
- `GET /api/locations/find-available` - найти доступную ячейку через `wms.find_available_location`.
- `GET /api/locations/{zone_id}/qr-codes` - ZIP с PDF QR-ярлыками для зоны.
- `GET /api/locations/{location_id}/qr-code` - ZIP с QR-ярлыком одной локации.

## Inventory

Префикс: `/api/inventory`.

- `GET /api/inventory/product/{product_id}` - остатки товара по локациям.
- `GET /api/inventory/location/{location_id}` - остатки в локации.
- `GET /api/inventory/location/{location_id}/recursive-summary` - агрегированные остатки по локации и всем дочерним локациям.
- `GET /api/inventory/location/by-code/{location_code}` - остатки в локации по коду.
- `GET /api/inventory/summary` - агрегированные остатки через `wms.v_product_stock`.
- `GET /api/inventory/container/{qr_code}` - остатки в контейнере.
- `GET /api/inventory/location/{location_id}/loose` - россыпь в локации.
- `GET /api/inventory/search` - поиск по товару, названию, партии или контейнеру.

## Movements

Префикс: `/api/movements`.

- `POST /api/movements` - создать batch movements, 1-500 элементов, атомарно.
- `GET /api/movements` - история движений с фильтрами.
- `GET /api/movements/product/{product_id}` - история движений товара.

## Containers

Префикс: `/api/containers`.

- `POST /api/containers/register` - зарегистрировать контейнер и содержимое через `wms.register_container`.
- `GET /api/containers/{qr_code}` - получить контейнер по QR.
- `PUT /api/containers/{container_id}/location` - переместить контейнер.
- `POST /api/containers/{container_id}/unpack` - извлечь товар из контейнера в россыпь через `wms.unpack_from_container`.
- `PATCH /api/containers/{container_id}/status` - изменить статус контейнера, кроме уже заблокированного.
- `GET /api/containers/{qr_code}/history` - история движений контейнера.
- `GET /api/containers/location/{location_id}` - контейнеры в локации.

## Tasks

Префикс: `/api/tasks`.

- `GET /api/tasks` - список заявок с фильтрами.
- `GET /api/tasks/my` - активные заявки сотрудника.
- `GET /api/tasks/available` - свободные pending-заявки.
- `GET /api/tasks/{task_id}` - детальная карточка заявки.
- `POST /api/tasks` - создать заявку с позициями.
- `PUT /api/tasks/{task_id}` - обновить pending/assigned заявку.
- `DELETE /api/tasks/{task_id}` - отменить pending/assigned заявку.
- `PUT /api/tasks/{task_id}/assign` - взять заявку в работу.
- `PUT /api/tasks/{task_id}/start` - начать выполнение заявки.
- `PUT /api/tasks/{task_id}/complete` - завершить заявку с фактическими данными.
- `GET /api/tasks/{task_id}/suggestions` - FIFO-подсказки по ячейкам.
- `PUT /api/tasks/{task_id}/approve-discrepancy` - подтвердить расхождение.
- `PUT /api/tasks/{task_id}/reject-discrepancy` - отклонить расхождение.
- `PUT /api/tasks/{task_id}/recount` - отправить на пересчет.
- `PUT /api/tasks/{task_id}/complete-recount` - завершить пересчет.

## Reports

Префикс: `/api/reports`.

- `GET /api/reports/zones` - отчет по зонам.
- `GET /api/reports/top-products` - топ товаров по движениям.
- `GET /api/reports/abc-analysis` - ABC-анализ.
- `GET /api/reports/turnover` - оборачиваемость.
- `GET /api/reports/batches` - отчет по партиям FIFO/FEFO.

## System

Префикс: `/api/system`.

- `GET /api/system/audit-summary` - read-only count-проверки известных рисков качества данных.
- `POST /api/system/validate-integrity` - сверить `inventory` с расчетом из `movements`.
- `POST /api/system/recalculate-inventory` - удалить и пересчитать остатки из `movements`.
- `POST /api/system/create-snapshot` - создать снимок остатков.
- `POST /api/system/refresh-materialized-views` - обновить `wms.mv_product_stock`.

## Notifications

Префикс: `/api/notifications`.

- `GET /api/notifications/unread` - непрочитанные уведомления пользователя.
- `PUT /api/notifications/{notification_id}/read` - пометить уведомление прочитанным.

## FBS Shipments

Префикс: `/api/fbs-shipments`.

- `GET /api/fbs-shipments/stats` - статистика по статусам.
- `GET /api/fbs-shipments` - список записей журнала.
- `GET /api/fbs-shipments/{shipment_id}` - детали записи с raw message и items.
- `POST /api/fbs-shipments/retry` - массовая переобработка validation_failed.
- `POST /api/fbs-shipments/{shipment_id}/retry` - переобработка одной записи.
