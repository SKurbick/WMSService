# KIZ v1

Статус: CURRENT для кода; migration подготовлена, в рабочую БД агентом не применялась.

## Scope и операции

Только exact location, inventory.status=available, batch_number/container_code NULL.
Один active KIZ занимает 1 единицу physical. Fractional quantity поддерживается:
при physical=1.5 первый assignment разрешён, второй запрещён. Active count вычисляется,
counter нет. Terminal записи сохраняются навсегда; код case-sensitive, immutable,
не переиспользуется. Неактивность SKU/location не является отдельным запретом assignment.

Assignment: product/location lookup → inventory FOR UPDATE → UPDATE updated_at для MVCC
→ count → KIZ INSERT → assigned event → commit. Touch не меняет physical/key и не создаёт
movement, но создаёт версию строки; весь flow на одной asyncpg connection/transaction.
Caller-owned transaction поддерживается через assign_in_transaction; при ошибке caller
обязан rollback всей операции. Собственные write transactions READ COMMITTED.
Terminal сначала читает scope без lock, затем inventory → KIZ FOR UPDATE → status/event.
Разрешены active → error/deactivated с обязательными author/reason.

## HTTP

Prefix /api/kiz. Query location — location_code, точное совпадение, без subtree.
Коды в URL нужно percent-encode; поддерживаются коды со slash через path converter.
Статический stock-summary route имеет приоритет перед кодом с таким же именем.

| Метод | URL | Результат |
|---|---|---|
| POST | /api/kiz/assign | 201: kiz + stock-summary |
| GET | /api/kiz/{kiz_code} | current state |
| GET | /api/kiz | items/total/limit/offset |
| GET | /api/kiz/{kiz_code}/events | audit items/total/limit/offset |
| POST | /api/kiz/{kiz_code}/mark-error | current state error |
| POST | /api/kiz/{kiz_code}/deactivate | current state deactivated |
| GET | /api/kiz/stock-summary | physical/identified/unidentified/integrity_ok |
| GET | /api/system/kiz-integrity | list нарушенных scopes |

List filters: product_id, location_code, lifecycle_status. Limit 1..200, default 50,
offset >=0; сортировка kiz_id, audit occurred_at/kiz_event_id. Count/page используют
один read-only REPEATABLE READ snapshot. Summary product/location обязательны.
Integrity возвращает product_id/location_id/location_code, physical, identified,
difference=identified-physical, inventory_missing. Пустой список означает отсутствие
нарушений KIZ; это отдельная проверка от movements ↔ inventory.

Assignment request:

```json
{"kiz_code":"KIZ-001","product_id":"sku","location_code":"KIZTEST","author":"operator","metadata":{}}
```

Response при исходном physical=10 и identified=0 (timestamps пример):

```json
{
  "kiz":{
    "kiz_id":1,"kiz_code":"KIZ-001","product_id":"sku",
    "location_id":1,"location_code":"KIZTEST","lifecycle_status":"active",
    "origin_type":"warehouse_assignment","origin_reference":null,
    "assigned_at":"2026-09-06T10:00:00Z","closed_at":null,
    "created_at":"2026-09-06T10:00:00Z","updated_at":"2026-09-06T10:00:00Z",
    "created_by":"operator","metadata":{}
  },
  "product_id":"sku","location_id":1,"location_code":"KIZTEST",
  "physical_quantity":"10.00","identified_quantity":1,
  "unidentified_quantity":"9.00","integrity_ok":true
}
```

Decimal quantities сериализуются строками, count — integer. GET карточки возвращает объект
kiz из примера, список — массив таких объектов в items. Terminal request:

```json
{"author":"operator","reason":"Ошибочно указан код","metadata":{"ticket":"123"}}
```

Terminal response — current state с новым lifecycle/closed_at/updated_at.
Metadata terminal принадлежит событию, assignment metadata в карточке не заменяется.
Stock summary:
GET /api/kiz/stock-summary?product_id=sku&location_code=KIZTEST возвращает summary
поля из assignment response без вложенного kiz.

Ошибки: 404 product/location/KIZ; 422 malformed input; 409 duplicate/missing stock/
no unidentified unit/terminal conflict/P7501. Duplicate identical request тоже 409.
40001/40P01 дают 409 CONCURRENT_WRITE_CONFLICT: повторить всю операцию.
Пример guard response:

```json
{"detail":"Недостаточно неидентифицированного остатка: physical=10, identified=4, requested_remaining=3","error_code":"KIZ_CONFLICT","diagnostics":{"product_id":"sku","location_id":1,"physical_quantity":10,"identified_quantity":4,"requested_remaining":3}}
```

## Physical guard и maintenance

BEFORE UPDATE/DELETE inventory защищает OLD scope, независимо от movement_type/writer.
Increase/no-op без смены identity пропускается без KIZ count; delete/key change требуют
ноль active. P7501 откатывает movement вместе с projection. JSON DETAIL используется
для диагностики, тип определяется SQLSTATE, не текстом PostgreSQL exception.

Recalculate: одна READ COMMITTED transaction, table locks movements SHARE → inventory
EXCLUSIVE; calculated negative check; calculated>=identified diagnostics; UPSERT
positive target rows; DELETE obsolete available rows; final KIZ integrity. Guards включены.
Порог ledger 0.0001, направление и product filter сохранены; from_date всё ещё запрещён.
Table locks глобальные даже с product filter: maintenance временно блокирует writers.
При legacy lock-order deadlock rollback и 409; повтор всей maintenance операции.

## Existing flows и retry

Movements/outgoing adjust/kit/re-sorting/tasks/DB functions получают physical guard
автоматически. HTTP P7501/40001/40P01 преобразуются в 409 без рефакторинга writers.
FBS KIZ conflict сохраняется как failed и KIZ_CONFLICT: ... в error_message, next_retry_at=NULL.
Serialization/deadlock имеют bounded retry, после max_retries — retry_exhausted.
Ошибка сохраняется после rollback product group, не затирает concurrent success.
HTTP FBS сохраняет журнал и отдаёт 409 с результатом/идентификатором отгрузки.
При нескольких product groups успешные группы остаются committed, как до KIZ v1.
Worker декодирует assembly_tasks JSON из asyncpg перед формированием группы.

## Deployment и ограничения

[Миграция](../../scripts/migrations/20260906_add_kiz_v1.sql) применяется владельцем вручную
до deployment; backfill/down удаления identity/audit нет.
[Runtime role check](../database/kiz_v1_runtime_check.md): текущая роль superuser, guards
не защищают от её намеренного обхода. Raw KIZ SQL inserts вне протокола не поддерживаются.
Неатомарность task orchestration и retry-claim остаются прежним техдолгом.

Не реализованы movement registry, KIZ/movement association, containers/nested containers,
batch/damaged/quarantine KIZ, receipts, accounts/рейсы, KIZ shipment, FBS/tasks selection,
kit/re-sorting KIZ lifecycle, replacement, генерация/печать, UI.


## Проверка продолжения 2026-09-07

Полный набор: **239 passed, 0 skipped**, 68 существующих Pydantic deprecation warnings.
Запуск: `PYTHONPATH=. KIZ_TEST_DSN=<local kiz_test DSN> KIZ_LOCK_TEST_DSN=<same DSN>
.venv/bin/pytest -q -p no:cacheprovider` (одной командой).
Использован оставшийся локальный контейнер `wms-kiz-v1-test` на 127.0.0.1:55439.
Fixture восстанавливает сохранённый production DDL с явно перечисленными в conftest
исключениями внешних объектов, применяет миграцию и создаёт отдельную БД на каждый кейс.
Рабочая БД не изменялась. Это не проверка deployment на актуальной stage-схеме.

При продолжении исправлены два устаревших FBS mock-репозитория, добавлены 10 test cases:
четыре HTTP retry conflict cases, три порядка assignment/terminal/spend с подтверждённым
pg_blocking_pids ожиданием, ручной retry после KIZ-конфликта с восстановлением,
terminal со старым RR snapshot и maintenance, ожидающий assignment.
OpenAPI KIZ/FBS дополнен описаниями конфликтов 409.
[Итог и полный список изменённых файлов](kiz_v1_completion.md).
