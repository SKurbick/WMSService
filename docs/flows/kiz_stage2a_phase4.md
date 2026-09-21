# KIZ Stage 2A — Phase 4 explicit transfer

Статус: Phase 1–3 completed и применены владельцем БД; Phase 4 completed в коде и
ожидает ручного применения migration; Phase 5 not implemented.

## HTTP contract

`POST /api/kiz-operations/transfer` принимает одну атомарную operation:

```json
{
  "source_system": "manual",
  "external_operation_id": "stable-id",
  "author": "operator",
  "items": [{
    "external_line_id": "1",
    "product_id": "wild123",
    "from_location_code": "A",
    "to_location_code": "B",
    "quantity": 5,
    "kiz_codes": ["KIZ-1", "KIZ-2"]
  }]
}
```

Ответ содержит operation identity и те же normalized строки с `movement_ref`.
`quantity` приходит и возвращается целым JSON-числом. Items и KIZ в результате
сортируются, поэтому snapshot стабилен. Exact replay возвращает сохранённый первый
результат.

### Поля запроса

| Поле | Как использовать |
|---|---|
| `source_system` | Стабильное имя системы-источника, например `manual`, `scanner` или `erp`. Оно входит в idempotency key. Не меняйте его при повторе одной команды. |
| `external_operation_id` | Уникальный стабильный ID физической команды в рамках `source_system`. При сетевом retry отправляйте тот же ID и тот же payload. Новый transfer получает новый ID. |
| `author` | Логин оператора или имя вызывающей системы для аудита. Это не механизм авторизации. |
| `items` | Непустой список строк. Все строки выполняются в одной транзакции: ошибка одной строки откатывает весь запрос. |
| `external_line_id` | Уникальный стабильный ID строки внутри операции. Он входит в fingerprint и связывает строку с одним movement. |
| `product_id` | SKU существующего товара, например `testwild` или `testwild2`. Все выбранные в строке КИЗ должны принадлежать этому товару. |
| `from_location_code` | Точный код исходной локации. Остаток и каждый выбранный КИЗ должны находиться именно на ней. |
| `to_location_code` | Точный код целевой локации. Он должен отличаться от исходного. |
| `quantity` | Полное перемещаемое количество штучных единиц: строго положительное целое JSON-число, например `5`. Значения `"5"`, `5.0` и `1.5` не принимаются. |
| `kiz_codes` | Явно выбранные active КИЗ. Порядок незначим, дубли запрещены. Пустой список означает перенос только неидентифицированного по КИЗ остатка. |

`quantity` — полное количество строки, а не только количество КИЗ. Например,
`quantity=5` и два `kiz_codes` означают две КИЗ-идентифицированные и три
неидентифицированные по КИЗ единицы.

### Поля ответа

`operation_id` — внутренний ID операции; `operation_type` всегда `transfer`;
`source_system` и `external_operation_id` повторяют idempotency identity. В каждой
строке `movement_ref` является стабильной ссылкой на созданный physical movement.
Остальные поля фиксируют нормализованный физический intent первого успешного вызова.

422 означает ошибку формы: quantity <= 0, одинаковые адреса, duplicate line/KIZ,
`quantity < count(kiz_codes)`, запрещённый JSON/Unicode. 409 означает idempotency,
KIZ, stock или concurrent write conflict. Неизвестные product/location используют
существующие 404 contracts.

## Physical semantics and transaction

Scope: точная локация и доступный россыпной остаток (`status=available`, партия и
контейнер не заданы). Один item создаёт
один transfer movement полного quantity. Explicit KIZ получают destination location и
links на этот movement; `quantity - count(KIZ)` является неидентифицированной по КИЗ
частью. Проверяется, что эта часть не превышает неидентифицированный по КИЗ остаток
источника, включая сумму items одного source scope. Пустой `kiz_codes` переносит только
неидентифицированный по КИЗ остаток.

Одна READ COMMITTED transaction выполняет:

1. idempotency acquire/replay/conflict;
2. создание operation items;
3. canonical location и существующие inventory row locks;
4. canonical KIZ locks и повторную проверку lifecycle/product/location;
5. для каждого item controlled KIZ location update;
6. movement с provenance `source_type=kiz_operation`;
7. registry lookup по точным `(movement_id, created_at)`;
8. attachment movement_ref и KIZ links;
9. конечную проверку: КИЗ-идентифицированный остаток не превышает физический;
10. сохранение result и commit.

Destination без inventory row блокируется через стабильную location row; сам projection
создаёт существующий movement trigger. Items, scopes и KIZ сортируются независимо от
порядка JSON. Старый assignment touch сохранён. Terminal KIZ flow после row lock теперь
повторно проверяет product/location, чтобы transfer и deactivate не использовали stale state.

## Controlled location update

Обычный `UPDATE wms.kiz.location_id` по-прежнему отклоняет `guard_kiz_identity`.
`wms.transfer_kiz_location` создаёт transaction-local authorization только для active
KIZ с точными expected product/source и конкретным незавершённым transfer item.
Deferred constraint требует до commit matching transfer movement/provenance,
movement_ref, KIZ link, destination state и operation result. После успешной проверки
authorization удаляется; неполный протокол целиком откатывается. Функция SECURITY
DEFINER использует фиксированный search_path; PUBLIC execute/table access отозваны.
Владелец БД должен выдать EXECUTE только фактической runtime role, если приложение не
подключается владельцем функции.

## Deployment

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_kiz_phase4_preflight.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260910_add_kiz_transfer.sql
```

Preflight read-only. Агент migration и write smoke в production не выполняет.

Ship, receive/FBS KIZ, containers, batch, accounts, tasks/kit/re-sorting KIZ lifecycle
не входят в Phase 4.
