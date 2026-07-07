# Kit Operations

## Назначение

Kit operations добавляют MVP комплектации и разукомплектации комплектов/metawild внутри WMS Service. 1С из этого flow не вызывается, RabbitMQ не используется.

## Разрешённые локации

Комплектация/разукомплектация выполняется только на активных WMS-локациях, которые разрешены через `wms.operation_locations`:

- `operation_code = 'kit_operations'`;
- `scope = 'direct'`;
- `is_active = true`.

Разрешённых локаций может быть несколько. `POST /api/kit-operations` по-прежнему требует `location_code`, но перед выполнением операции проверяет, что этот код есть в `wms.locations`, локация активна и для неё есть активная строка в `wms.operation_locations`.

`scope='direct'` означает: используются только остатки непосредственно на `operation_locations.location_id`. Дочерние адреса и subtree не учитываются.

Проверка `level=5` для kit operations отсутствует: разрешённой может быть зона или адрес другого уровня.

## Источник состава

Состав комплекта читается из `public.products.kit_components` по `products.id = kit_product_id`.

Комплект валиден, если:

- `is_active = true`;
- `is_kit = true`;
- `kit_components IS NOT NULL`;
- `kit_components` является непустым объектом `{component_product_id: quantity_per_kit}`.

Каждый component product должен существовать в `public.products`, быть активным, а `quantity_per_kit` должен быть больше 0.

## Assembly Flow

`POST /api/kit-operations` с `operation_type = assembly`:

- проверяет активную локацию по `location_code`;
- проверяет активное разрешение `wms.operation_locations` для `kit_operations/direct`;
- берет advisory lock по `kit_product_id + location_id`;
- блокирует расходную россыпь компонентов через `SELECT ... FOR UPDATE`;
- создает `wms.kit_operations` со статусом `processing`, включая `operation_location_id`, `location_id`, `location_code`;
- для каждого компонента создает item `component_consumption` и movement `kit_assembly` с `from_location_id`;
- создает item `kit_result` и movement `kit_assembly` с `to_location_id`;
- обновляет operation до `completed`.

## Disassembly Flow

`operation_type = disassembly`:

- блокирует расходную россыпь самого комплекта на выбранной direct-локации;
- создает item `kit_consumption` и movement `kit_disassembly` с `from_location_id`;
- для каждого компонента создает item `component_result` и movement `kit_disassembly` с `to_location_id`.

## MVP Ограничения

Расходный остаток поддерживается только как россыпь на выбранной direct-локации:

- `wms.inventory.location_id = operation_locations.location_id`;
- `wms.inventory.status = 'available'`;
- `batch_number IS NULL`;
- `container_code IS NULL`.

Если нужный расходный остаток есть только в контейнере, endpoint возвращает HTTP 409 с detail `Kit operation supports only loose stock in MVP`.

## Location Management API

- `GET /api/kit-operations/locations` - список разрешённых локаций.
- `POST /api/kit-operations/locations` - добавить или реактивировать разрешённую direct-локацию.
- `PATCH /api/kit-operations/locations/{operation_location_id}/deactivate` - деактивировать разрешённую локацию.

## Movements

Все движения создаются только через `INSERT INTO wms.movements`; `wms.inventory` напрямую не меняется.

Общие поля:

- `source_type = kit_operation`;
- `source_id = operation_id`;
- `source_item_id = item_id`;
- `metadata.role` - роль строки;
- `metadata.operation_type` - `assembly` или `disassembly`;
- `metadata.kit_product_id` - комплект.

Assembly использует `movement_type = kit_assembly`. Disassembly использует `movement_type = kit_disassembly`.

## Ошибки

- HTTP 400: невалидный или пустой `kit_components`, `quantity_per_kit <= 0`.
- HTTP 404: не найдена локация, комплект, component product или operation location при деактивации.
- HTTP 409: inactive location, location не разрешена для `kit_operations`, inactive/non-kit kit product, inactive component, недостаточно россыпи, container-only stock.
