# KIZ Stage 2A — Phase 5: explicit shipment

Статус: реализовано в коде. Production migration применяет владелец БД вручную.

## Endpoint

POST /api/kiz-operations/ship

```json
{
  "source_system": "manual",
  "external_operation_id": "ship-testwild-001",
  "author": "operator",
  "items": [
    {
      "external_line_id": "1",
      "product_id": "testwild",
      "from_location_code": "KIZTEST-SOURCE",
      "quantity": 5,
      "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"]
    }
  ]
}
```

`quantity` — полное число штучных единиц в строке. Это строго положительное целое JSON-число: `5`, без кавычек. Строка `"5"`, `5.0` и дробные значения отклоняются с 422.

`external_line_id` — стабильный идентификатор строки внутри внешней команды. Источник назначает его один раз и повторяет без изменения при retry. Он связывает строку результата и `kiz_operation_item` с её movement; это не product ID и не порядковый номер, который следует пересчитывать при replay. В одной операции значения уникальны.

`source_system` вместе с `operation_type=ship` и `external_operation_id` образует ключ идемпотентности. `author` попадает в physical movement и shipped events. `product_id` задаёт SKU, `from_location_code` — точный исходный адрес. `kiz_codes` содержит только явно выбранные active КИЗ; порядок не важен, дубли запрещены.

Ответ:

```json
{
  "operation_id": 2,
  "operation_type": "ship",
  "source_system": "manual",
  "external_operation_id": "ship-testwild-001",
  "items": [
    {
      "external_line_id": "1",
      "product_id": "testwild",
      "from_location_code": "KIZTEST-SOURCE",
      "quantity": 5,
      "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"],
      "movement_ref": 15002
    }
  ]
}
```

Exact replay возвращает сохранённый идентичный physical result. Тот же ключ с другим intent даёт 409. Schema errors дают 422; изменившийся KIZ, недостаточный physical или неидентифицированный остаток и concurrency conflicts дают 409.

## Остатки и mixed shipment

Операция работает только с доступным россыпным остатком точной локации: `status=available`, `batch_number IS NULL`, `container_code IS NULL`.

- `P` — physical quantity;
- `I` — количество active KIZ в этом scope;
- `U=P-I` — неидентифицированный по КИЗ остаток;
- `Q` — quantity строки;
- `N` — число явно выбранных KIZ.

Допустимо `0 <= N <= Q` и `Q-N <= U`. Пустой `kiz_codes` списывает только неидентифицированный остаток и никогда не выбирает КИЗ автоматически. Один item создаёт один movement полного `Q`, независимо от `N`.

## Transaction и защита

Все items выполняются в одной READ COMMITTED transaction:

`idempotency row → canonical location rows → source inventory scopes → canonical KIZ rows → повторная validation → controlled shipped transition → ship movement → registry lookup → operation item attachment → KIZ links → shipped events → final integrity → stored result → commit`.

`wms.ship_kiz(...)` — SECURITY DEFINER function с фиксированным `search_path`. Она создаёт узкую transaction-local authorization и допускает только `active + source + closed_at NULL → shipped + location NULL + closed_at now()`. Обычный UPDATE продолжает блокировать `guard_kiz_identity()`.

Deferred trigger не разрешает commit authorization без согласованного ship operation, saved result, operation item, movement provenance, movement_ref, KIZ link и shipped event. Authorization удаляется при успешной проверке и не является ledger. Любая ошибка откатывает lifecycle, inventory, movement, registry, item attachment, links, events, operation и result вместе.

## Deployment

Только вручную владельцем БД:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/migrations/20260911_kiz_phase5_preflight.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/migrations/20260911_add_kiz_ship.sql
```

Preflight read-only. Backfill не нужен. Runtime role требуется точечный `EXECUTE` на `wms.ship_kiz(bigint,bigint,varchar,bigint,numeric)`; PUBLIC лишён доступа.

Phase 1–5 completed. Stage 2A внутренне завершён. Stage 2B FBS KIZ и Stage 2C receipt KIZ не реализованы.
