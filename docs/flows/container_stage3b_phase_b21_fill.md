# Container Stage 3B — Phase B2.1 fill

Статус: `COMPLETED` в коде и migration-файлах. Production migration и physical
операции агентом не выполнялись.

## API

`POST /api/container-operations/fill` принимает одну идемпотентную multi-item команду:

- business key: `(source_system, operation_type=fill, external_operation_id)`;
- stable target identity: `container_id`;
- каждая строка имеет stable `external_line_id`, `product_id`, exact `batch_number`
  и положительное `numeric(10,2)` quantity;
- JSON float запрещён, decimal передаётся строкой или точным целым JSON-числом.

Exact replay возвращает сохранённый response. Другой normalized intent с тем же key
возвращает `409 CONTAINER_IDEMPOTENCY_CONFLICT`.

## Create empty

Отдельный endpoint не добавлен. Существующий `POST /api/containers/register` с
`contents=[]` уже создаёт `empty` container без contents, inventory и movements.
Регистрация с товаром остаётся legacy receipt-like и не используется для fill.

## Physical protocol

Fill поддерживает только `available` loose stock (`container_code IS NULL`) на direct
location target flat container. Допустимы статусы `empty` и `open`; успешный fill делает
`empty -> open`, а `open` оставляет без изменения.

На каждую строку создаются два связанных `transfer` movements:

1. outgoing: `from_location=container.location`, `to_location=NULL`,
   `container_code=NULL`;
2. incoming: `from_location=NULL`, `to_location=container.location`,
   `container_code=container.qr_code`.

Одна movement row не может выразить разные loose/container стороны, потому что legacy
projection имеет одно поле `container_code` на обе стороны. Пара сохраняет:

```text
loose - Q
contained + Q
total unchanged
```

Обе записи имеют `source_type=container_operation`, общий `source_id=operation_id` и
`source_item_id=operation_item_id`. Global movement registry trigger выдаёт каждой
записи отдельный `movement_ref`; оба ref сохраняются в immutable operation item.

## Contents и transaction

`container_contents` обновляется в той же transaction после projection movements.
Новый active scope создаётся через transaction-local authorization, чтобы legacy
AFTER INSERT receive-trigger не создавал receipt. Existing scope увеличивается через
`ON CONFLICT ON CONSTRAINT uq_container_content`.

Финальная проверка требует равенства contained inventory и active contents, а также
неизменности `loose + contained` для каждого product/batch scope. Любая ошибка откатывает
operation, items, status, movements, registry refs, inventory и contents.

Порядок блокировок:

```text
idempotency key
-> container row
-> products
-> operation items
-> inventory scopes в (product_id, batch_number) order
-> status/movements/contents
-> invariant/result
```

Container row сериализует fill с другим fill, legacy move и обновлённым legacy unpack.
Loose inventory row сериализует разные containers и generic outgoing movement.
B2.2 extract использует тот же container-first/canonical inventory lock order.

## Ручное применение

1. Остановить container writers или использовать maintenance window.
2. Выполнить read-only
   `scripts/migrations/20260915_container_b21_fill_preflight.sql`.
3. Вручную применить `scripts/migrations/20260915_add_container_b21_fill.sql`.
4. Выполнить B1 regression и B2.1 PostgreSQL/API smoke tests.

Migration создаёт только schema/protocol objects и заменяет две existing functions.
Она не выполняет fill, не создаёт movements и не меняет существующие quantities.

## Граница

B1, B2.1 fill и B2.2 extract реализованы. B3 move/unpack normalization, B4 generic
bypass closure, B5 history read model и Stage 3C KIZ containers не реализованы.
B2.2 contract: [controlled extract](container_stage3b_phase_b22_extract.md).

До B4 generic movement остаётся неподдерживаемым bypass: последующая прямая операция
над contained inventory может рассинхронизировать dual representation. B2.1 сам bypass
не использует и проверяет invariant перед commit. Batch ambiguity и full extract legacy
unpack остаются до B2.2/B3.
