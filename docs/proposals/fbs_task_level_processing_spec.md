# Техническое задание: обработка FBS на уровне сборочных заданий

> **Статус: PROPOSAL.** ТЗ фиксирует согласованное направление разработки.
> Оно не описывает уже работающую production-логику.

Дата: 2026-09-28.

Связанный клиентский read model:
[`fbs_task_results_read_model.md`](fbs_task_results_read_model.md).

## 1. Цель

Изменить FBS write-off так, чтобы ранее успешно списанное СЗ внутри payload не
откатывало обработку новых СЗ того же `product_id`.

Новая логика должна:

- списывать ровно количество уникальных новых СЗ;
- безопасно пропускать только подтвержденные дубли;
- изолировать неконсистентные и отсутствующие СЗ;
- сохранять результат по каждому СЗ;
- не допускать двойного списания при параллельной обработке и retry;
- сохранять обратную совместимость входного контракта и существующих adapters.

## 2. Текущая проблема

Сейчас `validate_assembly_tasks` требует атомарно захватить все СЗ product group.
Если хотя бы одно СЗ уже имеет `is_shipped=true`, вся product group завершается
ошибкой. Новые СЗ из той же группы не создают movement, даже если сами еще не
были обработаны.

Такой отказ защищает от дублей, но создает потенциальные пропуски списаний при
смешанном payload.

## 3. Границы задачи

В задачу входят:

- миграция `wms.fbs_shipment_task_results`;
- task-level классификация и атомарное списание новых СЗ;
- интеграция с standard Rabbit consumer, external consumer, HTTP ingestion,
  retry worker и manual retry через общий handler;
- отдельный read-only endpoint результатов;
- тесты конкурентности, retry и обратной совместимости;
- документация текущего состояния после включения.

Не входят:

- автоматическое повторное списание `inconsistent` СЗ;
- изменение формата входного RabbitMQ/HTTP payload;
- прямое изменение `wms.inventory`;
- исторический backfill с недоказанным исходом;
- изменение внешней системы, которая заполняет `public.assembly_task`.

## 4. Обязательная обратная совместимость

1. `WriteOffAccordingToFBS` и правило
   `quantity == len(assembly_tasks)` не изменяются.
2. Существующие RabbitMQ очереди, consumer adapters и
   `POST /api/fbs-shipments` продолжают принимать прежний payload.
3. Существующие поля `fbs_shipments` и `fbs_shipment_items` не удаляются и не
   переименовываются.
4. Существующие GET endpoints продолжают работать с прежними response schemas.
5. Сохраняется инвариант: `fbs_shipment_items.status='success'` требует
   непустой `movement_id`.
6. Старые строки без task results остаются читаемыми.
7. Новая логика включается конфигурационно и допускает немедленный возврат в
   legacy mode без rollback миграции.

## 5. Режимы запуска

Предлагается настройка:

```text
FBS_TASK_PROCESSING_MODE=legacy|observe|task_level
```

- `legacy` — точное текущее поведение; новая таблица не влияет на решение.
- `observe` — вычисляется и сохраняется классификация, но write-off выполняется
  по текущему all-or-nothing алгоритму. Режим нужен для сравнения результатов.
- `task_level` — подтвержденные дубли и аномалии не блокируют новые СЗ.

Значение по умолчанию первого релиза — `legacy`. Переключение выполняется
отдельно для development/stage, затем для production после проверки метрик.

## 6. Классификация СЗ

После нормализации и удаления дублей внутри входной product group СЗ делятся на:

1. `new`:
   - строка существует в `public.assembly_task`;
   - `is_shipped=false`;
   - подтвержденного предыдущего success/movement нет.
2. `confirmed_duplicate`:
   - существует `fbs_shipment_items.status='success'`;
   - заполнен `movement_id`;
   - соответствующий movement существует.
3. `inconsistent`:
   - `is_shipped=true`;
   - подтвержденной цепочки success item -> movement нет.
4. `not_found`:
   - строка отсутствует в `public.assembly_task`.

Если один `task_id` присутствует несколько раз внутри входной группы, он
участвует в расчете не более одного раза. Повторные вхождения фиксируются в
`details`, но не увеличивают movement quantity.

## 7. Целевой алгоритм

Для каждой product group:

1. Заблокировать связанные `fbs_shipment_items`.
2. Преобразовать `assembly_tasks` в уникальные числовые task IDs.
3. Получить все `public.assembly_task` одним `SELECT ... FOR UPDATE` в
   детерминированном порядке `task_id`.
4. Одним set-based запросом получить предыдущие success item/movement links.
5. Классифицировать все СЗ.
6. Если `new` пуст:
   - movement не создавать;
   - сохранить результаты дублей/аномалий;
   - сохранить legacy item status по правилам раздела 10.
7. Если `new` не пуст:
   - атомарно выставить `is_shipped=true` только для `new`;
   - проверить `UPDATE ... RETURNING` против ожидаемого множества;
   - создать один movement на product group с
     `quantity = count(distinct new.task_id)`;
   - записать task results;
   - связать item с новым movement там, где item содержит списанные СЗ;
   - пересчитать shipment status.
8. Commit выполняется только после согласованной записи movement, task results,
   item links и shipment status.

`confirmed_duplicate`, `inconsistent` и `not_found` не увеличивают quantity
нового movement и не блокируют `new`.

## 8. Транзакционная граница

```text
BEGIN
  -> lock fbs_shipment_items
  -> lock assembly_task rows in task_id order
  -> read confirmed success/movement links
  -> classify tasks
  -> UPDATE is_shipped only for new tasks RETURNING task_id
  -> INSERT movement(quantity = count(new tasks))
  -> inventory trigger
  -> UPSERT fbs_shipment_task_results
  -> update item status/movement links
  -> update parent shipment status
COMMIT
```

Ошибка movement, inventory trigger, неполный claim новых СЗ или неполная запись
task results откатывает movement, inventory, новые `is_shipped`, task results и
item links одной product group.

После rollback из-за нехватки остатка результаты `pending_retry` и legacy retry
status сохраняются отдельной короткой транзакцией, как сегодня сохраняется
ошибка item.

## 9. Таблица результатов

Предварительная DDL-модель:

```sql
CREATE TABLE wms.fbs_shipment_task_results (
    result_id                  bigserial PRIMARY KEY,
    shipment_id               bigint NOT NULL,
    item_id                   bigint NOT NULL,
    task_id                   bigint NOT NULL,
    product_id                varchar NOT NULL,
    outcome                   varchar NOT NULL,
    effect_quantity           smallint NOT NULL DEFAULT 0,
    movement_id               bigint,
    existing_success_item_id  bigint,
    existing_movement_id      bigint,
    is_shipped_before         boolean,
    reason                    text,
    details                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_fbs_task_result_shipment
        FOREIGN KEY (shipment_id)
        REFERENCES wms.fbs_shipments(shipment_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_fbs_task_result_item
        FOREIGN KEY (item_id)
        REFERENCES wms.fbs_shipment_items(item_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_fbs_task_result_outcome CHECK (
        outcome IN (
            'written_off',
            'duplicate_skipped',
            'inconsistent',
            'not_found',
            'pending_retry',
            'failed'
        )
    ),
    CONSTRAINT chk_fbs_task_result_effect CHECK (
        effect_quantity IN (0, 1)
    ),
    CONSTRAINT uq_fbs_task_result_item_task UNIQUE (item_id, task_id)
);

CREATE INDEX idx_fbs_task_results_task
    ON wms.fbs_shipment_task_results(task_id);

CREATE INDEX idx_fbs_task_results_shipment_outcome
    ON wms.fbs_shipment_task_results(shipment_id, outcome);

CREATE INDEX idx_fbs_task_results_product_created
    ON wms.fbs_shipment_task_results(product_id, created_at DESC);
```

Финальная миграция должна учитывать фактические типы production-колонок и
соглашения именования миграций. FK на movements не добавляется без решения
проблемы глобальной identity partitioned `wms.movements`.

## 10. Совместимость legacy item status

Первая версия не добавляет новые значения `fbs_shipment_items.status`.

- Item с успешно списанными новыми СЗ и только подтвержденными дублями получает
  `success` и новый `movement_id`.
- Item только с подтвержденными дублями сохраняет legacy-представление `failed`
  с нормализованным сообщением о no-op; task results показывают, что физической
  ошибки и нового списания нет.
- Item с `inconsistent` или `not_found` остается `failed`, даже если новые СЗ
  этой product group были успешно списаны. Если item содержит успешно списанные
  новые СЗ, его `movement_id` заполняется для сохранения audit link.
- Нехватка остатка сохраняет текущий `pending_retry/retry_exhausted` flow.
- `success` без `movement_id` не создается.

Для нового клиента авторитетным источником частичного результата является
`fbs_shipment_task_results`. Legacy item status остается консервативным общим
индикатором и не используется для расчета фактического количества смешанной
позиции.

Перед реализацией необходимо подтвердить, допускают ли текущие response schemas
и клиенты `movement_id` у failed item. Если нет, связь частичного movement
публикуется только через task results, а legacy item остается без movement_id.

## 11. Retry

- Retry выбирает только task results `pending_retry` и при необходимости
  повторно классифицирует `inconsistent/not_found` после изменения внешних
  данных.
- `written_off` и `duplicate_skipped` повторно не списываются.
- Полный повтор того же payload после успеха становится безопасным no-op.
- Retry использует те же блокировки и общий `_process_shipment_group`, отдельной
  упрощенной write-логики быть не должно.
- Параллельные worker/manual retry одной позиции сериализуются row lock или
  claim через `FOR UPDATE SKIP LOCKED`.

## 12. Read-only API

Добавить отдельный endpoint:

```text
GET /api/fbs-shipments/{shipment_id}/task-results
```

Он не изменяет существующий `GET /api/fbs-shipments/{shipment_id}` и не имеет
write-аналогов. Контракт и пример ответа описаны в
[`fbs_task_results_read_model.md`](fbs_task_results_read_model.md).

## 13. Наблюдаемость

Структурированные логи product group должны содержать только агрегаты и
идентификаторы операции:

```text
shipment_id, product_id, incoming_tasks, new_tasks, duplicate_tasks,
inconsistent_tasks, not_found_tasks, movement_id, processing_mode
```

Полные списки СЗ доступны в task results и не обязаны дублироваться одной
длинной строкой application log.

Метрики:

- количество task results по `outcome`;
- количество task-level movements и их quantity;
- количество `requires_reconciliation` shipment/item;
- время классификации и транзакции;
- конфликты конкурентного claim;
- расхождение `SUM(effect_quantity)` с movement quantity.

## 14. Тестирование

Обязательные unit/integration cases:

1. Все СЗ новые — поведение и итоговый movement совпадают с legacy flow.
2. Часть СЗ — подтвержденные дубли, часть новые — списываются только новые.
3. Все СЗ — подтвержденные дубли — movement не создается.
4. `is_shipped=true` без success link не блокирует новые и получает
   `inconsistent`.
5. Отсутствующее СЗ не блокирует новые и получает `not_found`.
6. Дубли task ID внутри payload не увеличивают movement quantity.
7. Нехватка остатка откатывает все новые СЗ и movement, результаты переходят в
   `pending_retry`.
8. Retry после пополнения остатка создает ровно одно движение.
9. Два параллельных payload с одним task ID создают не более одного физического
   списания.
10. Сбой между movement и task results откатывает всю product group.
11. Старые shipments без task results читаются прежними endpoints.
12. `legacy`, `observe` и `task_level` дают ожидаемое различие поведения.
13. GET task results не выполняет writes и соблюдает права доступа.

Проверки остатков и конкурентности выполняются только на отдельном test/stage
PostgreSQL, не на production.

## 15. Этапы внедрения

### Этап A — additive schema и чтение

- миграция новой таблицы и индексов;
- repository и read schemas;
- read-only endpoint;
- режим по умолчанию `legacy`.

### Этап B — observe

- классификация task-level без изменения физического write-off;
- сравнение классификации с текущими failed/success результатами;
- проверка производительности и объема таблицы.

### Этап C — task-level на development/stage

- включение нового write flow;
- конкурентные и fault-injection тесты;
- проверка retry и mixed payload.

### Этап D — production canary

- включение для ограниченного источника/автора или процента shipments;
- мониторинг outcome и movement invariants;
- возможность немедленно вернуть `legacy`.

### Этап E — штатный режим

- включение `task_level` по умолчанию;
- актуализация `docs/current/`, database map, migrations README и решений;
- отдельное решение о допустимости исторического backfill.

## 16. Критерии приемки

- Смешанная группа с подтвержденным дублем создает movement только на новые СЗ.
- Для каждого входного валидного СЗ сохраняется один текущий task result.
- Один task ID не может создать два физических списания при конкурентной
  обработке.
- `SUM(effect_quantity)` строк `written_off` для movement совпадает с movement
  quantity.
- `duplicate_skipped` всегда содержит подтвержденный существующий movement.
- `inconsistent` не создает автоматическое физическое списание.
- Старый payload принимается без изменений.
- Существующие endpoints проходят regression tests без удаления или
  переименования полей.
- Feature mode позволяет вернуть legacy behavior без удаления таблицы.

## 17. Риски

- Item `quantity` остается входным количеством, а movement mixed item — только
  количеством новых СЗ. Старые клиенты не должны использовать item quantity как
  доказательство физического эффекта в смешанном случае.
- Некорректная классификация `is_shipped=true` как дубля может скрыть потерянное
  списание; поэтому требуется success item и существующий movement.
- Длительная блокировка больших групп может увеличить contention; запросы
  должны быть set-based, а lock order — детерминированным.
- Task results увеличат объем БД примерно на одну строку на входное СЗ; нужны
  оценка retention и индексов на реальном потоке.
- Retry и основной consumer могут конкурировать за один item/task.
- Частичный физический успех при legacy item status `failed` требует аккуратной
  адаптации manual retry и клиентского отображения.
- Старые записи нельзя надежно backfill без ложных выводов.

## 18. Вопросы до реализации

1. Допускают ли текущие внешние клиенты `movement_id` у item со статусом
   `failed`, если часть его СЗ была успешно списана?
2. Какой срок хранения task results нужен и требуется ли партиционирование?
3. Нужен ли внешний поиск по `task_id` между shipments в первой версии или
   достаточно detail endpoint одного shipment?
4. По какому ключу включать production canary: `author`, `source`, account или
   процент shipment IDs?
5. Требуется ли отдельный ручной workflow разрешения `inconsistent`, или на
   первом этапе достаточно read-only диагностики?

