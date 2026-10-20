# KIZ v1: MVCC-контрпример и принятое решение

Статус: `HISTORICAL / RESOLVED`. Проверено 2026-09-06.
Пользователь согласовал inventory touch; он реализован в KizService.
[Текущий контракт](../flows/kiz_v1.md). Ниже — исходный контрпример и история решения.
Production DDL и данные агентом не изменялись.

## Подтверждённый конфликт

ТЗ требует, чтобы inventory guard защищал инвариант в том числе от manual UPDATE.
Assignment берёт `SELECT inventory ... FOR UPDATE`, но изменяет только KIZ и audit.
Блокировка сама по себе не создаёт новой MVCC-версии inventory row.

При PostgreSQL REPEATABLE READ возможен сценарий:

1. В inventory quantity=5, active KIZ count=4.
2. Транзакция расхода открывает snapshot, в котором active count=4.
3. Другая транзакция assignment блокирует inventory row, проверяет свободную единицу,
   создаёт пятый KIZ и commit. Inventory row не обновляется.
4. Транзакция расхода делает UPDATE inventory SET quantity=4. Row lock доступен.
5. Даже VOLATILE PL/pgSQL guard видит active count=4 из старого transaction snapshot.
6. UPDATE и commit проходят: physical=4, identified=5.

Это не отключение триггера: достаточно обычного UPDATE с разрешённым уровнем изоляции.
В `connection.py` задан search_path, но не зафиксирован default_transaction_isolation.
Глобальный запрет REPEATABLE READ для writers в проекте не установлен.
Фактическая runtime-настройка production в этом исследовании не проверялась.

## Реальный эксперимент

[Исполняемый тест](../../tests/test_kiz_lock_protocol.py) использует две asyncpg connection
к отдельной локальной PostgreSQL 16. Создаёт случайную schema и удаляет только её.
Это минимальная модель протокола, не WMS bootstrap и не acceptance tests модуля.
Порядок перекрывающихся транзакций детерминированный; ожидание row lock здесь не тестируется.

| Расход | Assignment | Результат расхода | Physical | Identified |
|---|---|---|---:|---:|
| READ COMMITTED | Только row lock | Guard P7501 | 5 | 5 |
| REPEATABLE READ | Только row lock | Commit, инвариант нарушен | 4 | 5 |
| REPEATABLE READ | Row lock + UPDATE без изменения quantity | Serialization failure 40001 | 5 | 5 |

P7501 из эксперимента теперь используется и реальной KIZ миграцией.

Запуск на disposable локальной БД с именем kiz_test:

```bash
KIZ_LOCK_TEST_DSN='postgresql://USER:PASSWORD@127.0.0.1:PORT/kiz_test' \
  .venv/bin/pytest -q tests/test_kiz_lock_protocol.py
```

## Принятая поправка

Разрешить assignment служебно обновлять заблокированную inventory row в той же
транзакции, до INSERT KIZ, без изменения stock identity и quantity. Это создаёт
MVCC-версию, поэтому расход со старым repeatable-read snapshot получит 40001.
Ни counter columns, ни advisory locks, ни movement registry для этого не нужны.

В реализации зафиксировано:

- исключение для этого UPDATE в [write policy](../current/write_operations_policy.md),
  которая сейчас разрешает прямые inventory writes только maintenance recalculate;
- assignment не меняет физическое количество и не создаёт movement, но может менять
  inventory.updated_at: существующий trg_inventory_updated_at устанавливает NOW();
- assignment исполняется с READ COMMITTED либо при другом уровне изоляции создаёт
  новую row version до чтения identified и корректно обрабатывает 40001;
- HTTP mapping serialization conflict и поведение FBS retry для этой ошибки;
- дополнительные integration tests: stale-snapshot assignment, расход, terminal
  и обычные сценарии ТЗ с ожиданием row lock.

Эксперимент подтверждает конкретное исправление расхода, а не весь будущий протокол.
Acceptance-тесты реального KizService и inventory guard добавлены в test_kiz_integration.py.

Альтернатива — ограничить затронутые write transactions READ COMMITTED и enforce отказ
других уровней в БД до чтения KIZ count. Это ограничит старых/manual writers даже без
active KIZ и требует решения о совместимости.

## Выполненная работа и границы

Изучены код и сохранённый production schema-only snapshot от 2026-08-08; проверены
имена inventory/movement triggers. Затем выполнена [read-only runtime проверка](../database/kiz_v1_runtime_check.md).
Application code, endpoints и SQL миграция добавлены при реализации.

Первоначальная остановка снята после согласования поправки пользователем.
KIZ v1 реализован; возможности второго этапа не добавлялись.
Дальнейшая работа остаётся в feature/kiz. SQL-миграции подготавливаются на проверку;
создание объектов в рабочей БД выполняет пользователь вручную.
