# Container Stage 3B — Phase B2.2 controlled extract

Статус: `COMPLETED` в коде и migration-файлах. Production migration и physical extract
агентом не выполнялись.

## API

`POST /api/container-operations/extract` принимает идемпотентную multi-item команду:

```json
{
  "source_system": "manual",
  "external_operation_id": "extract-001",
  "author": "operator",
  "container_id": 123,
  "items": [
    {
      "external_line_id": "1",
      "product_id": "wild123",
      "quantity": "2.00",
      "batch_number": null
    }
  ]
}
```

Response `201` для new и exact replay содержит `operation_type="extract"`, stable
container identity, итоговый `container_status` и два `movement_ref` на item. Business
key: `(source_system, operation_type='extract', external_operation_id)`.

## Physical protocol

Extract разрешён только для `open` flat container с direct location. Exact source:
`available + container QR + product + NULL-safe batch + container.location_id`;
destination — loose `available` того же product/batch/location.

На item создаются два `transfer` movements:

1. contained outgoing: `from_location=container.location`, `to_location=NULL`,
   `container_code=container.qr_code`;
2. loose incoming: `from_location=NULL`, `to_location=container.location`,
   `container_code=NULL`.

Оба movement имеют `source_type=container_operation`, общий `source_id=operation_id` и
`source_item_id=operation_item_id`; refs выдаёт global movement registry.

```text
contained - Q
loose + Q
total unchanged
```

## Contents и status

Partial extract уменьшает exact active content row. Full extract вызывает controlled
`wms.apply_container_extract_content(operation_item_id)` и удаляет current row, не
записывая `quantity=0`. Функция допускает только unfinished extract item с attached
movement refs и требует exact delta между old contents и спроецированным contained
inventory, поэтому повторная mutation отклоняется.

После всех items status определяется по контейнеру целиком: active contents остались —
`open`; последний active content удалён — `empty`.

## Transaction и locks

Одна connection и transaction:

```text
idempotency operation
→ container row
→ products / immutable operation items
→ all inventory scopes in canonical order
→ all active content scopes in canonical order
→ validation
→ paired movements / registry refs
→ controlled contents mutation
→ whole-container status
→ final contents and conservation invariants
→ saved result
→ commit
```

Container lock сериализует fill, extract, legacy unpack и legacy move одного container.
Inventory locks сериализуют generic writers затронутых scopes. Если generic bypass уже
нарушил dual representation, extract возвращает conflict/rollback; будущие generic writes
после commit остаются известным риском до B4.

## Idempotency и rollback

Exact replay возвращает сохранённый response без второго physical effect. Тот же key с
другим container/product/batch/quantity возвращает `409
CONTAINER_IDEMPOTENCY_CONFLICT`. Любая ошибка откатывает operation/items, movements,
registry refs, contents, inventory, status и result.

## Ручное применение

1. Остановить container writers или использовать maintenance window.
2. Выполнить read-only `scripts/migrations/20260916_container_b22_extract_preflight.sql`.
3. Вручную применить `scripts/migrations/20260916_add_container_b22_extract.sql`.
4. Выполнить B2.2 PostgreSQL/API smoke и B2.1 regression.

Migration не выполняет extract и не меняет существующие quantities.

## Граница

B1, B2.1 fill и B2.2 extract реализованы. Legacy unpack остаётся backward-compatible с
известными ограничениями. B3 move/unpack normalization реализована отдельно. B4 generic bypass closure, B5
history, nested containers и Stage 3C KIZ containers не реализованы.
