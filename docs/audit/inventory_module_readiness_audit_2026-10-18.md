# Аудит WMS перед реализацией модуля инвентаризации

Дата: 18.10.2026.

Статус: исследовательский отчёт и предварительные рекомендации. Сохранён по запросу пользователя из результата аудита в диалоге. Реализация модуля не выполнялась.

Полноценного модуля инвентаризации в текущем дереве нет. Есть пригодная основа — физические остатки, журнал движений, транзакционные операции и иерархия локаций, — но существующие `tasks/recount/approval` не обеспечивают требуемый процесс.

Главные препятствия для безопасной реализации: неполностью воспроизводимая актуальная схема БД, отсутствие общей границы между пересчётом и конкурентными операциями, особенности `adjust`, а также смешение маркированной и немаркированной россыпи в одной строке физического остатка.

Аудит выполнен чтением текущей ветки `feature/kiz-fbs-integration`, включая существующие незакоммиченные изменения. Дополнительно прочитаны отдельные миграции из локальной ветки `feature/kiz`, без переключения веток. К рабочей БД не подключался. Тесты не запускал: некоторые PostgreSQL fixtures выполняют `DROP SCHEMA` и `TRUNCATE`. Во время аудита файлы не создавались и не изменялись; итоговый `git status` соответствовал исходному. Этот файл добавлен позднее по отдельному запросу пользователя.

В отчёте используются обозначения:

- **Код** — подтверждено текущими исходниками.
- **DDL** — подтверждено сохранённой production-схемой от 08.08.2026 либо указанной миграцией; применение новых миграций в рабочей БД не подтверждено.
- **Рекомендация** — предлагаемое решение, отсутствующее в текущей реализации.
- **Не проверено** — требует актуального read-only аудита БД или внешних процессов.

Ссылки указывают на файлы и строки, прочитанные во время аудита; при последующих изменениях кода номера строк могут сместиться.

## 1. Что уже есть

| Компонент | Назначение и фактическое состояние | Готовность и пригодность для инвентаризации |
|---|---|---|
| `wms.inventory` | Текущий физический остаток по товару, локации, статусу, партии и контейнеру. Количество `numeric(10,2)`, ограничение `quantity >= 0`. **DDL:** [таблица](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1039), [уникальность с `NULLS NOT DISTINCT`](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:3166). | Подходит как источник учётного количества. Гранулярность таблицы подробнее, чем требуемая строка документа `product_id + location_id`. |
| `wms.movements` и триггер inventory | Направленный журнал: приход через `to_location_id`, расход через `from_location_id`. Inventory изменяется в транзакции вставки movement. **DDL:** [структура](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1391), [функция](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:659). | Основной механизм корректировок. Требуются дополнительные гарантии для расходного `adjust`, идентификации и конкурентного применения. |
| `POST /api/movements` | Принимает 1–500 движений одним атомарным пакетом. Есть внутренний метод работы на переданном соединении. **Код:** [endpoint](/home/skurbick/PROJECTS/WMSService/app/api/v1/endpoints/movements.py:151), [транзакции](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:34). | Можно переиспользовать принцип и транзакционную точку расширения. Текущий контракт не принимает source-поля и ограничивает quantity целыми числами. |
| `wms.tasks`, `wms.task_items` | Заявки на складские операции, плановое/фактическое количество, дочерние задачи согласования и пересчёта. **Код:** [TaskService](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:324). | Рабочие части процесса перемещения, но не готовая инвентаризация. Согласование неатомарно, история пересчётов недостаточна. |
| `wms.inventory_snapshots` | Сохранение текущего `available` inventory с календарной датой. **Код:** [запрос](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:172). | Для документа инвентаризации в текущем виде непригодно: нет заголовка снимка, связи с документом, партии и области. |
| `wms.locations`, LTREE | Дерево адресов, поиск потомков через `<@`. **Код:** [SQL локаций](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/locations.py:90). | Подходит для формирования области. Нужна материализация выбранных ID и контроль целостности дерева. |
| ФБС: consumers, handler, retry | Общая обработка standard/external/HTTP. Атомарность — на товарную группу, а не на всё сообщение. **Код:** [handler](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:678), [worker](/home/skurbick/PROJECTS/WMSService/app/retry_worker.py:31). | Можно учитывать фактические списания по movements. Нельзя считать одно сообщение одним движением или моментом физической отгрузки. |
| Комплектация и пересортица | Собственные документы, строки, source-связи movements, транзакции и блокировки расходных остатков. **Код:** [комплектация](/home/skurbick/PROJECTS/WMSService/app/core/services/kit_operation_service.py:37), [пересортица](/home/skurbick/PROJECTS/WMSService/app/core/services/re_sorting_operation_service.py:23). | Хорошие образцы транзакционной организации нового модуля. Их блокировки не являются общим протоколом всех операций WMS. |
| КИЗ | Отдельный реестр идентичностей поверх physical inventory; activation не меняет physical quantity. **Код:** [активация](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_activation_service.py:25), [расчёт active КИЗ](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_activation.py:47). | Обязательно учитывать при исключении КИЗ из MVP. Фильтра по контейнеру недостаточно. |
| История остатков | Дневной расчёт по movements, согласованное чтение через read-only `REPEATABLE READ`. **Код:** [репозиторий](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/inventory_history_repository.py:14). | Полезен подход к согласованному чтению. Дневная агрегация не заменяет журнал изменений между конкретными пересчётами. |
| Системное восстановление inventory | Удаление `available` строк и восстановление из movements. **Код:** [репозиторий](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/system_repository.py:39). | Особый источник изменения inventory без нового movement. Должен входить в протокол контроля инвентаризации. |

### Ограничение достоверности схемы

Документация действительно перенесена из `docs/context/` в `docs/current/`, `docs/database/` и другие разделы: [маршрут документации](/home/skurbick/PROJECTS/WMSService/docs/README.md:1).

Сохранённый production-аудит относится к **08.08.2026**, до нынешних изменений КИЗ: [дата и результаты](/home/skurbick/PROJECTS/WMSService/docs/database/production_schema_audit_2026-08-08.md:1).

Текущий код использует `wms.kiz`, `wms.movement_registry`, операции и разрешения КИЗ, но базовые миграции создания части этих объектов отсутствуют в текущем дереве. Они обнаружены в локальной ветке `feature/kiz`; её код нельзя считать текущим API или подтверждённым deployment. Разрыв отражён также в [аудите КИЗ](/home/skurbick/PROJECTS/WMSService/docs/audit/kiz_progress_business_source.md:32) и [ограничениях миграций](/home/skurbick/PROJECTS/WMSService/scripts/migrations/README.md:31).

Следовательно, применять рекомендации к production без сверки фактических функций и триггеров преждевременно.

## 2. Карта изменения остатков

В таблице «атомарно» означает наличие одной транзакционной границы в коде или выполнение внутри одного SQL statement. Это не заменяет проверку поведения настоящих триггеров под конкуренцией.

| Сценарий | Точка входа | Где создаётся movement | Как обновляется inventory | Транзакционность | Риски |
|---|---|---|---|---|---|
| Поступление, перемещение, отгрузка, ручной `adjust` | `POST /api/movements` | [MovementService](/home/skurbick/PROJECTS/WMSService/app/core/services/movement_service.py:99) → `CREATE_MOVEMENT` | Movement trigger | Весь HTTP batch в одной transaction | Нет idempotency key; нет явной блокирующей проверки остатка; source-поля не записываются этим запросом |
| Завершение складской задачи | `PUT /api/tasks/{id}/complete` | [`_create_movements_for_task`](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:801), всегда `transfer` | Movement trigger | Пакет movements атомарен; обновление task/items находится вне него | Повтор/гонка completion; частично сохранённые фактические данные; нулевое количество не проходит MovementCreate |
| Подтверждение расхождения задачи | `PUT /api/tasks/{id}/approve-discrepancy` | [цикл transfer и adjust](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:570) | Movement trigger | Отдельные transaction для отдельных вызовов movements; статусы отдельно | Повторное движение, частичное применение, неподходящая для инвентаризации семантика |
| Standard и external ФБС | RabbitMQ consumers | [общий handler](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:752) | Movement trigger | Product-group transaction включает movements, СЗ, результаты items и статус shipment | Сообщение может примениться частично по товарам; ACK предшествует business processing |
| HTTP ФБС | `POST /api/fbs-shipments` | Тот же handler | Movement trigger | Та же product-group граница | HTTP-успех при приёме не равен успешному списанию всех позиций |
| ФБС retry | Фоновый worker и HTTP retry | [`_process_shipment_group`](/home/skurbick/PROJECTS/WMSService/app/retry_worker.py:80) | Movement trigger | Product-group transaction | Списание может произойти значительно позже первоначального сообщения; поведение зависит от режима |
| ФБС с КИЗ | Ветка общего handler | [по одному movement на физический источник](/home/skurbick/PROJECTS/WMSService/app/core/services/fbs_kiz_shipment_service.py:158) | Movement trigger плюс согласованное изменение lifecycle КИЗ | Все источники одной product group атомарны | Источник может быть вне ФБС-зоны; один movement может включать разные категории единиц |
| Комплектация/разукомплектация | `POST /api/kit-operations` | [создание строк и movements](/home/skurbick/PROJECTS/WMSService/app/core/services/kit_operation_service.py:255) | Movement trigger | Документ, строки и movements в одной transaction; расходные строки `FOR UPDATE` | Только null batch/container; Decimal quantity; отдельный advisory namespace |
| Пересортица | `POST /api/re-sorting-operations` | [два направленных movements](/home/skurbick/PROJECTS/WMSService/app/core/services/re_sorting_operation_service.py:80) | Movement trigger | Одна transaction, advisory lock и расходный row lock | Нулевой итог по числу единиц не означает отсутствие изменения каждого SKU |
| Регистрация контейнера с содержимым | `POST /api/containers` → `register_container` | [триггер вставки contents](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:455) создаёт `receive` | Movement trigger | DB function и вложенные triggers атомарны | Обычно вне области россыпи; фактический DB-контракт мог быть заменён контейнерными миграциями другой ветки |
| Перемещение контейнера | `PUT /api/containers/{id}/location` | [`move_container_inventory`](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:334) | Movement trigger | UPDATE контейнера и вызванные triggers атомарны; предварительные Python-проверки отдельно | Legacy read/check/write не защищён общей transaction; движения контейнеров надо отделять от россыпи |
| Распаковка | `POST /api/containers/{id}/unpack` | [`unpack_from_container`](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:517): расход контейнера и приход россыпи | Movement trigger | Одна DB function | Для инвентаризируемой россыпи это **приход**, хотя общий остаток SKU в локации не изменился; legacy full-unpack конфликтует с constraints |
| Восстановление inventory | `POST /api/system/recalculate-inventory` | Новый movement не создаётся | Прямые `DELETE` и `INSERT … ON CONFLICT` | [Одна transaction](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/system_repository.py:51), но без общего барьера с writers | Журнал movements не сообщает о произошедшей замене проекции; влияние КИЗ guards требует проверки |
| Импорт/активация КИЗ | RabbitMQ inbox → process; `POST /api/kiz/activate` | При registration/activation movement отсутствует | Physical inventory не меняется | [Business apply/savepoint](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_import_service.py:160); activation — одна transaction | Activation меняет долю немаркированного остатка без physical movement |
| Мягкие резервы | Reservation consumer | Не создаётся | Inventory не меняется | Отдельный контур резервов | `free_qty` нельзя принимать за пересчитываемый физический `available`; [правила](/home/skurbick/PROJECTS/WMSService/docs/current/business_rules.md:84) |
| Snapshot, refresh views, read-only отчёты | System/read API | Не создаётся | Inventory не меняется | Snapshot пишет другую таблицу; refresh — представление | Не являются движениями или инвентаризационными корректировками |

### Полнота журнала

**Подтверждено кодом:** обычные найденные physical write flows проходят через movements. Исключение — системное восстановление inventory.

**Не подтверждено:** что рабочая БД не имеет дополнительных функций, прямых SQL-writers, административных операций и внешних сервисов, меняющих inventory или lifecycle КИЗ.

Для поступлений есть историческое исследование внешнего `1CRoutingAPI`: movement отправлялся через HTTP WMS, а `receipt_items` записывались отдельно. Это **историческое свидетельство, не проверка текущего внешнего сервиса**: [аудит поступлений](/home/skurbick/PROJECTS/WMSService/docs/archive/audits/receipt_writer_flow_analysis.md:18). Интеграцию с 1С в MVP добавлять не требуется, но существующие поступления необходимо учитывать.

### Фактическая поддержка `adjust`

**Код:** quantity всегда положительное; знак задаётся стороной movement:

- Излишек: `to_location_id = location`, количество `actual − expected`.
- Недостача: `from_location_id = location`, количество `expected − actual`.
- Нулевое расхождение: movement не нужен.

Это соответствует [MovementCreate](/home/skurbick/PROJECTS/WMSService/app/core/schemas/movement.py:30).

**Критическая особенность сохранённого DDL:** при расходе отсутствующая строка вызывает исключение только для `ship`, `transfer`, `kit_assembly`, `kit_disassembly`. `adjust` в списке отсутствует. Поэтому расходный `adjust` может записаться в журнал, не уменьшив inventory, если соответствующей строки нет. При существующей строке уход ниже нуля блокируется CHECK. [Функция расхода](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:699).

Дополнительное несоответствие: расход сравнивает batch/container через `COALESCE(value, '')`, а уникальность различает `NULL` и пустую строку. При наличии обеих разновидностей расход может затронуть несколько строк. Наличие таких данных не проверено.

**Вывод:** атомарный массовый `adjust` технически возможен, но до использования для инвентаризации необходимо проверить и обеспечить:

1. Точное соответствие расходуемым измерениям.
2. Ошибку при отсутствии/недостатке расходного остатка.
3. Совпадение записанного movement с фактическим эффектом.
4. Сохранение ограничений КИЗ.
5. Единую transaction с утверждением документа.

Публичный HTTP batch нельзя использовать несколькими запросами для большого документа: это потеряет атомарность всего согласования. Нужен внутренний вызов на одном соединении.

### Партии и точность количества

В inventory партия — часть уникального ключа. `batch_number=NULL` означает конкретный остаток без партии, а не «любая партия».

**Не проверено:** есть ли сейчас остатки с непустым batch. Сохранённый production-аудит не содержит соответствующей количественной проверки.

Если они есть, интерфейс всё равно может иметь одну строку `product_id + location_id`, но сервер должен:

- Суммировать допустимые physical rows для показа.
- Сохранить детализацию snapshot по партиям.
- При недостаче распределить расход по конкретным batch rows.
- При излишке применить заранее определённую политику назначения партии.
- Записать распределение в audit.

Например, при `batch A=4`, `batch B=6` и факте `8` нельзя отправить `adjust -2` с `batch=NULL`: такого остатка может не существовать. Автоматически определить, какая партия потеряла две единицы, из агрегированного пересчёта невозможно.

Это техническое правило распределения, а не предложение вернуть партии в пользовательские фильтры.

Ещё одно ограничение: inventory имеет десятичную точность, kit operations принимают Decimal, а публичный MovementCreate — integer. Дробные остатки технически допустимы: [kit quantity](/home/skurbick/PROJECTS/WMSService/app/core/schemas/kit_operations.py:29). Нельзя молча преобразовывать корректировку в `int`.

## 3. Существующая логика tasks / recount / approval

### Что действительно реализовано

**DDL:** допустимы типы:

`replenishment`, `transfer`, `picking`, `putaway`, `inventory`, `discrepancy_approval`, `recount`.

Статусы:

`pending`, `assigned`, `in_progress`, `pending_approval`, `pending_recount`, `completed`, `completed_with_discrepancy`, `cancelled`.

Источник: [таблица tasks и CHECK constraints](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:2474).

**Код:** создание задачи требует источника, назначения и непустого списка положительных плановых количеств. Отдельной ветки бизнес-процесса `inventory` нет: [TaskCreate](/home/skurbick/PROJECTS/WMSService/app/core/schemas/task.py:55).

Существующие endpoints `approve-discrepancy`, `reject-discrepancy`, `recount`, `complete-recount` зарегистрированы: [маршруты](/home/skurbick/PROJECTS/WMSService/app/api/v1/endpoints/tasks.py:236).

### Завершение и согласование

`complete_task`:

- Проверяет исполнителя и статус `in_progress`.
- Проверяет полноту переданных item IDs и их принадлежность задаче.
- Сохраняет actual quantities отдельными repository-вызовами.
- При расхождении и отсутствии разрешения создаёт дочернюю approval-задачу.
- Иначе создаёт `transfer` по фактическим количествам и затем завершает задачу.

Это процесс выполнения складского перемещения, а не корректировка обнаруженного расхождения: [реализация](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:324).

`approve_discrepancy`:

- Требует `approve_discrepancies`.
- Проверяет тип дочерней задачи, но не защищает операцию проверкой её текущего статуса под блокировкой.
- Позволяет передать словарь отдельных утверждённых количеств.
- Обновляет item по его ID без проверки принадлежности родительской задаче.
- Создаёт transfers, а при `actual > planned` дополнительно приходный adjust в исходную локацию.
- Изменяет статусы после движений отдельными вызовами.

Источники: [сервис согласования](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:570), [SQL изменения строки](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/tasks.py:303).

**Следствия:**

- Повторное подтверждение может повторно создать движения.
- Ошибка после одного movement не откатит предыдущие вызовы.
- Согласование может затронуть посторонний item.
- Полномочия противоречат согласованному MVP без разделения ролей.
- Семантика transfer + дополнительный приход не соответствует `actual − expected` для inventory.
- Проверка доступности суммирует все статусы и контейнеры, хотя создаваемое движение использует конкретные batch и loose stock: [SQL остатка](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/tasks.py:323).

### Пересчёт и история

`complete_recount` читает **текущий** остаток по SKU/локации, сохраняет результаты в `metadata.recount_results` и переводит родительскую задачу в `pending_approval`. Он не создаёт корректировки и не обновляет quantity_actual родительских task_items. [Код](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:736).

Ограничения:

- Нет фиксированного начального снимка.
- Нет контроля области и полного покрытия строк.
- Остаток для сравнения суммируется без фильтров статуса, контейнера и КИЗ.
- Повторное завершение пересчёта перезаписывает JSON-результат.
- Нет неизменяемой истории попыток и решений.
- Переданные результаты не становятся автоматически основанием последующего движения.

[SQL сохранения и чтения](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/tasks.py:360).

Отдельный подтверждённый конфликт: `send_to_recount` устанавливает `waiting_recount`, которого нет в сохранённом CHECK статусов. Поскольку действия выполняются отдельно, ошибка такого UPDATE способна оставить уже созданную recount-задачу и изменённого родителя. [Код](/home/skurbick/PROJECTS/WMSService/app/core/services/task_service.py:702), [DDL](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:2494). Актуальный runtime constraint необходимо проверить.

`quantity_actual=NULL` и `0` в модели различаются, но completion создаёт movement и для нулевого факта, тогда как MovementCreate требует `quantity >= 1`. Значит, наличие nullable actual само по себе не делает текущую модель пригодной для нулевого пересчёта.

### Сравнение вариантов

| Вариант | Оценка |
|---|---|
| Полностью использовать существующие tasks и endpoints | Не соответствует требованиям: другая семантика, нет атомарного approval, scope, snapshot, идемпотентности и истории |
| Расширить tasks | Возможно, но потребуются новые таблицы scope/count history, отдельные переходы, новые transaction boundaries и специальные ограничения. Получится отдельный процесс внутри модели с риском изменить обычные складские задачи |
| Отдельный модуль инвентаризации | **Рекомендуется.** Собственный документ и история; общие inventory, movements, locations, products и инфраструктура транзакций |

Переиспользовать стоит инфраструктуру и проверенные приёмы. Связь с tasks можно добавить позднее как назначение работы, но tasks не должны быть источником истины для согласования инвентаризации.

## 4. Snapshot и область инвентаризации

### Существующий snapshot

**Код и DDL:**

- Один `INSERT … SELECT` копирует все `available` строки.
- Включает контейнеры.
- Не сохраняет batch.
- Не принимает warehouse/location/SKU scope.
- Не связан с документом.
- `snapshot_id` идентифицирует отдельную строку, а не весь запуск.
- Повторный вызов за ту же дату добавляет новые строки.
- Переданная прошлая `snapshot_date` только маркирует текущие остатки прошлой датой; историческое состояние не восстанавливается.

Источники: [запрос](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/system.py:172), [DDL](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1105).

Сам `INSERT … SELECT` получает согласованный statement snapshot. Однако последующий запрос статистики выполняется отдельно и считает все строки за дату: [репозиторий](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/system_repository.py:71). Это не атомарный запуск документа.

В текущем коде не найден потребитель этой таблицы, обеспечивающий инвентаризационный процесс, и не найден встроенный планировщик создания snapshots. Дневная история остатков читает movements.

**Рекомендация:** сохранить старую таблицу в её текущем назначении. Начальный snapshot нового документа хранить в его строках и детализации измерений.

### Как отделить обычную россыпь от КИЗ

`container_code IS NULL` исключает контейнер, но не active loose КИЗ. Текущий read endpoint россыпи даже не ограничивает статус: [GET_LOOSE_INVENTORY](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/inventory.py:111).

В существующем КИЗ-контракте:

- Active КИЗ привязаны к loose `location_id` либо контейнеру.
- Один active loose КИЗ занимает одну единицу physical stock без партии.
- Registered КИЗ без holder не занимают physical capacity.
- Activation переводит единицу из unidentified в identified, не создавая movement.

Это подтверждается [запросами activation](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/kiz_activation.py:47) и [проверкой capacity](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_activation_service.py:92).

Для технического определения немаркированной части текущая модель даёт:

`unidentified = available loose physical − active loose KIZ count`

Вычитать КИЗ нужно из соответствующего **безпартийного** scope, а не из каждой batch-строки и не после размножающего строки JOIN.

Но есть существенная граница: **unidentified в БД не доказывает физическое отсутствие маркировки**. Registered КИЗ могут относиться к физически существующим товарам, но ещё не иметь адресной привязки. В этой модели невозможно надёжно определить все такие единицы только SQL-фильтром.

Поэтому для точного ТЗ нужны:

- Проверка фактического КИЗ-контракта БД.
- Способ, которым сотрудник отличает и исключает маркированные единицы при подсчёте.
- Обработка смешанной строки SKU/локация без исключения всей немаркированной части.
- Контроль lifecycle-событий во время пересчёта.

Если классификация неоднозначна, строку нужно помечать как требующую разбирательства/пересчёта, а не автоматически корректировать physical inventory.

### Фиксация области

**Подтверждено:** отдельного `warehouse_id` у locations нет; принадлежность складу определяется деревом. `path` строится по ID предков. Поиск descendants сейчас исключает сам выбранный root: [SQL](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/locations.py:90), [DDL path](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:197).

**Рекомендация для запуска:**

1. Заблокировать документ, проверить состояние draft.
2. Проверить выбранный корень склада.
3. Развернуть зоны в список конкретных location IDs, включая саму зону, если на ней допускается хранение.
4. Применить явные location- и SKU-фильтры с пересечением условий.
5. При выборе только SKU зафиксировать соответствующую область локаций выбранного склада, **не ограничиваясь адресами, где сейчас есть положительный остаток**.
6. Сохранить область отдельно от автоматически созданных положительных строк.
7. Одним согласованным чтением сохранить исходные количества, batch-детализацию и необходимый КИЗ-контекст.
8. Зафиксировать всё одной транзакцией.

Пустые локации должны оставаться в scope: иначе невозможно правильно проверять пересечения и добавлять найденный товар.

Отсутствие SKU-фильтра должно храниться как «все SKU», а не как список товаров, найденных при запуске. Иначе документ неправомерно запретит добавление неожиданного SKU.

### Изменение дерева

Триггер обновляет path изменяемой строки, но не путей потомков. Текущий HTTP UPDATE не меняет parent, однако изменение через SQL остаётся возможным: [триггер](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:197), [UPDATE_LOCATION](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/locations.py:148).

После запуска область должна читаться по сохранённым ID, без повторного разворачивания текущего LTREE. Новые дочерние адреса автоматически в документ не входят.

На запуске нужна согласованная версия дерева. Для переезда/удаления уже охваченного адреса потребуется ограничение или явный протокол конфликта; нельзя молча менять область документа.

### Запрет пересекающихся активных документов

**Рекомендация:** отдельная таблица активного владения локациями с уникальным `location_id` и ссылкой на документ.

При запуске вставляются все location claims в одной transaction. Уникальность БД разрешает гонку двух запусков: один получает область, второй — конфликт; частично захваченной области не остаётся.

- Claims не зависят от SKU.
- Удерживаются при counting, review и recount.
- Освобождаются только при окончательном утверждении или закрытии без применения.
- Черновики могут иметь совпадающие selections до запуска.
- Исторический scope хранится отдельно и не удаляется.

Это **не блокировка обычных движений**, а исключение второй активной инвентаризации. Предварительного `SELECT` на пересечение недостаточно.

## 5. Параллельные движения и ФБС

### Что можно и нельзя восстановить сейчас

По movement известны SKU, обе локации, количество, batch/container, created_at и иногда source-связи. Для обычного physical ledger этого достаточно для направленной дельты конкретной пары.

Однако:

- `created_at DEFAULT now()` — время начала transaction, не коммита.
- ID sequence не задаёт порядок commit.
- Inventory `updated_at` перезаписывается и не является историей.
- В task items нет надёжной границы физического подсчёта.
- `shipment_date` приходит необязательным полем; нет гарантии, что оно обозначает момент изъятия товара из ячейки.
- Нет общего протокола фиксации движения относительно счёта сотрудника.

Источники: [movement timestamps](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1391), [inventory timestamp](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:753), [FBS payload](/home/skurbick/PROJECTS/WMSService/app/core/schemas/write_off_fbs.py:21). Семантика `now()` подтверждается [PostgreSQL Date/Time Functions](https://www.postgresql.org/docs/17/functions-datetime.html).

Поэтому условие `movement.created_at > counted_at` не обеспечивает правильного восстановления. Аналогично ненадёжен `movement_id > last_seen_id`: транзакция с меньшим ID может завершиться позже.

`movement_registry` решает идентичность связи, но не порядок коммита. Текущий код ищет registry по паре `(movement_id, created_at)`: [репозиторий](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_kiz_repository.py:345). Наличие, ограничения и полноту registry в рабочей БД нужно проверить; для этого уже существует [preflight](/home/skurbick/PROJECTS/WMSService/scripts/migrations/20261002_fbs_kiz_stage1_preflight.sql:1).

### Пример 10 → фактически 8 → приход 3

При доказанном порядке событий:

| Момент | Учётное количество | Фактическое количество |
|---|---:|---:|
| Запуск | 10 | Пока неизвестно |
| Завершён пересчёт | 10 | 8 |
| После пересчёта поступило 3 | 13 | 11 |
| После правильной корректировки −2 | 11 | 11 |

Ошибка — сравнить сохранённый факт `8` с текущим учётом `13` и списать `5`.

Для корректного расчёта нужны разные величины:

- `E0` — неизменяемый учётный остаток при запуске.
- `Ec` — учётный остаток, приведённый к достоверной границе конкретного пересчёта.
- `A` — фактически пересчитанное количество на этой границе.
- `Dafter` — доказанное изменение учитываемого остатка после неё.

Тогда:

`discrepancy = A − Ec`

`actual_at_approval = A + Dafter`

`adjustment = actual_at_approval − current_expected`

При полной и согласованной истории последнее равно `A − Ec`.

Если приход 3 произошёл **до** подсчёта, `Ec=13`, и результат `A=8` означает расхождение `−5`. Если приход произошёл **во время** подсчёта и неизвестно, попал ли он в 8, автоматический расчёт недостоверен.

Начальный snapshot не следует перезаписывать при актуализации: надо сохранять исходную и приведённую базы сравнения.

### Отдельная проблема запаздывающего ФБС

Возможен другой порядок:

1. В WMS числится 10.
2. Физически отгрузили 2, сообщение ещё не обработано.
3. Сотрудник насчитал 8.
4. Consumer записал movement `−2`, учёт стал 8.

Если признать этот movement физическим событием **после пересчёта**, система может ошибочно уменьшить результат ещё на 2.

Таким образом, даже идеальный порядок коммитов не устанавливает порядок физических действий.

**Вывод:** без остановки всей очереди ФБС работать можно, но автоматически учитывать каждое списание только по времени обработки нельзя. Нужны достоверная привязка физического события к границе подсчёта либо явное разрешение неоднозначности ответственным; при невозможности — повторный пересчёт строки после завершения затронутых операций.

### Транзакционность и повторная обработка ФБС

Подтверждено текущим кодом:

- Standard/external используют общий consumer и handler: [consumer](/home/skurbick/PROJECTS/WMSService/app/consumer.py:33).
- Raw shipment сохраняется, затем сообщение ACK-ается, затем выполняется business processing.
- Items создаются отдельной transaction.
- Каждая product group списывается отдельной transaction.
- В successful group атомарны movements, inventory trigger, `assembly_task.is_shipped`, результаты items и пересчёт shipment status: [legacy](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:119), [task-level](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:403).
- Одно сообщение может создавать несколько movements по товарам.
- В КИЗ-ветке одна product group может создавать несколько movements по локациям; active loose КИЗ списывается из текущего holder: [выбор источника](/home/skurbick/PROJECTS/WMSService/app/core/services/fbs_kiz_shipment_service.py:45).
- Retry первоначально выбирает pending items без `SKIP LOCKED`, но затем обработчик блокирует items; task-level повторно проверяет допустимый статус после блокировки: [выбор retry](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/repositories/fbs_shipment_repository.py:650), [проверка](/home/skurbick/PROJECTS/WMSService/app/handlers/write_off_fbs_handler.py:425).

Идемпотентность ФБС основана преимущественно на СЗ и подтверждённых movement-связях, а не на RabbitMQ message ID.

По умолчанию конфигурация использует `FBS_TASK_PROCESSING_MODE="legacy"`. Нельзя считать task-level/КИЗ-поведение включённым в production без проверки настроек: [defaults](/home/skurbick/PROJECTS/WMSService/app/shared/config.py:42).

Дополнительный риск доставки: после ACK возможен сбой до создания items или назначения pending_retry. Worker выбирает pending_retry, поэтому одного сохранённого raw shipment недостаточно для гарантии автоматического восстановления такого сообщения.

Для инвентаризационного ledger нельзя складывать одновременно movement quantity и effect_quantity всех FBS results: это удвоит физический эффект. Results нужны для происхождения и классификации, movement — для физической дельты.

### КИЗ-события меняют учитываемую величину

Для немаркированной части `U=P−I`, где `P` — physical loose, `I` — active loose КИЗ:

| Событие | ΔP | ΔI | ΔU |
|---|---:|---:|---:|
| Обычное списание немаркированной единицы | −1 | 0 | −1 |
| Отгрузка active КИЗ | −1 | −1 | 0 |
| Activation registered КИЗ | 0 | +1 | −1 |
| FBS shipment registered КИЗ из ФБС-зоны | −1 | 0 | −1 |

Последний вариант разрешён текущим кодом. Поэтому нельзя просто исключить все movements с `source_type='kiz_operation'`. Внутри них может быть смешанный эффект. [Проверки physical/identified и создание movements](/home/skurbick/PROJECTS/WMSService/app/core/services/fbs_kiz_shipment_service.py:108).

### Варианты решения

| Вариант | Преимущества | Ограничения |
|---|---|---|
| Только timestamps/ID существующего movements | Минимум изменений | Не обеспечивает порядок commit и физических действий; пропускает изменения без movement. **Не рекомендуется** |
| Движения разрешены; контрольные границы и версии затронутого остатка; актуализация с подтверждением | Соответствует непрерывной работе WMS; можно пересчитывать отдельные строки | Нужен общий протокол writers и классификация запаздывающих событий |
| Временно ограничивать конкретную локацию на время физического счёта | Упрощает получение достоверного факта | Требует реального ограничения действий, обработки in-flight операций и отложенного retry; одной DB-блокировки недостаточно |
| Блокировать область от запуска до approval | Простая база сравнения | Длительно мешает работе, особенно ФБС; плохо соответствует требованию продолжения операций |
| CDC/WAL и порядок commit | Может дать точный порядок изменений БД | Значительно сложнее; не сообщает, когда товар физически переместили; избыточно для первого этапа |

### Рекомендуемый алгоритм MVP

**Рекомендация:** движения продолжаются; применяется контроль каждой попытки пересчёта и короткая сериализация на фиксации границ/утверждении. Неоднозначные строки возвращаются на пересчёт. Локальное окно без физических операций — возможный способ разрешения устойчивых конфликтов, не глобальная блокировка WMS.

Необходимые элементы:

1. **Постоянное состояние версии scope.**
   Версия должна существовать независимо от inventory row: нулевые строки удаляются, а найденный товар может ещё не иметь строки. Для MVP возможен уровень локации с более подробными событиями по SKU.

2. **Участие всех writers.**
   Любое изменение учитываемого остатка должно транзакционно участвовать в общей сериализации и менять версию: movement flows, КИЗ lifecycle, восстановление inventory. У текущих kit/FBS/activation разные advisory namespaces; они не обеспечивают такую общую границу.

3. **Версия не из sequence.**
   Нужен транзакционно обновляемый счётчик под общей блокировкой scope или эквивалентный протокол. Простое выделение следующего ID не даёт порядка commit.

4. **Попытка пересчёта с началом и завершением.**
   Сохраняются обе границы, ожидаемое количество, факт, исполнитель и события между границами. DB transaction не удерживается всё время, пока человек считает.

5. **Проверка валидности факта.**
   Если во время счёта произошло влияющее или неоднозначное событие, результат сохраняется в истории, но не становится пригодным для approval автоматически. Даже равенство начального и конечного количества недостаточно: приход и расход могли взаимно компенсироваться.

6. **Актуализация после счёта.**
   Доказанные события после границы применяются к вычисляемому актуальному факту; исходный ввод сотрудника неизменяем. Ответственный видит список событий и подтверждает конкретную ревизию расчёта.

7. **Атомарное утверждение.**
   Заблокировать документ и необходимые scope в определённом порядке; проверить статус, полноту, версии и подтверждённую актуализацию; создать все adjustments; записать результат approval и освободить claims в одной transaction.

8. **Изменение после просмотра.**
   Если версия изменилась между просмотром и approval, корректировки не выполняются. Возвращается новая актуализация либо требование пересчёта затронутых строк.

9. **Неизвестные изменения.**
   Maintenance, пропуск журнала, несогласованность КИЗ, неизвестный физический порядок делают строки непригодными для автоматического approval.

Все writers должны соблюдать совместимый порядок блокировок. Простое добавление lock в один endpoint не закрывает гонку с trigger, consumer или activation; многолокационные операции требуют отдельной проверки deadlock-сценариев.

### READ COMMITTED, REPEATABLE READ и TOCTOU

- При `READ COMMITTED` соседние запросы могут видеть разные состояния. Проверка движений и последующий adjust без общего lock оставляют TOCTOU.
- `REPEATABLE READ` подходит для согласованного snapshot, но не останавливает writers и не делает старый факт актуальным.
- `SELECT FOR UPDATE` по inventory не блокирует отсутствующую строку и не гарантирует защиту от новой batch-строки.
- Advisory lock действует только на участников того же протокола.
- `SERIALIZABLE` не восстанавливает физический порядок и не заменяет участие остальных write paths.

Семантика изоляции: [PostgreSQL Transaction Isolation](https://www.postgresql.org/docs/17/transaction-iso.html); блокировки: [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/17/explicit-locking.html).

**Граница гарантии:** без достоверного сопоставления физических операций и пересчёта нельзя обещать полностью автоматическое восстановление любой занятой локации. Безопасное поведение — сохранить документ, остановить применение корректировок и пересчитать только затронутые позиции.

## 6. Предлагаемая архитектура

Это предварительная рекомендация по результатам аудита, не готовый проект миграций.

### Существующие таблицы

Использовать:

- `wms.inventory` — текущую физическую проекцию.
- `wms.movements` — единственный обычный механизм применения корректировок.
- `wms.locations` — адреса и исходное дерево.
- `public.products` — SKU.
- `wms.kiz`, события и movement links — для исключения identified stock и обнаружения изменений классификации, после проверки runtime.
- `wms.movement_registry` — для точных связей, если его наличие и целостность подтверждены.

Не использовать текущие `tasks` и `inventory_snapshots` как основную модель документа.

### Минимальные новые сущности

Названия ниже условные.

| Сущность | Что хранит |
|---|---|
| Документ инвентаризации | Склад, вид, состояние, исходный selection, авторы/время, версия документа, текущий цикл согласования |
| Зафиксированные локации документа | Location IDs на запуске; при необходимости исторические code/path для отображения |
| SKU-область | Режим «все» либо явный неизменяемый список SKU |
| Активные claims локаций | Уникальный location ID → активный документ |
| Строки документа | Уникальная пара document/product/location, начальный expected, текущая действительная попытка пересчёта, состояние конфликта |
| Детали исходного остатка | Batch-разбивка, physical/identified составляющие и контрольные версии |
| Попытки пересчёта | Неизменяемые вводы actual, начало/конец, expected на границе, исполнитель, причина, номер попытки |
| Решения и актуализации | Подтверждение/отклонение/возврат, показанная пользователю ревизия, принятые события и расчёт |
| Связи корректировок | Строка/approval/batch → точная идентичность movement, величина и направление |
| Состояние и события изменения scope | Транзакционная версия, источник и характер изменения; обнаружение неизвестных или неколичественных изменений |

Часть истории может храниться в общем append-only журнале модуля. Но данные, необходимые для уникальности, FK и контроля количества, не стоит прятать только в JSON.

### Состояния

Предлагаемый основной цикл:

`draft → counting → pending_approval → approved`

При отклонении:

- Закрытие без движений → отдельное terminal состояние.
- Возврат → `counting` с новым циклом согласования.

Конфликт отдельной строки — её состояние, а не автоматическая отмена документа. Остальные результаты остаются действительными, если не затронуты событиями.

«Не пересчитано» хранится отсутствием принятой попытки/nullable actual, а не числом 0. Нулевой факт должен быть полноценным сохранённым результатом.

### Предлагаемые API

Все перечисленные маршруты **новые**, сейчас отсутствуют:

| API | Назначение |
|---|---|
| `POST /api/inventorizations` | Создать draft |
| `PATCH /api/inventorizations/{id}` | Изменить область до запуска |
| `GET /api/inventorizations` и `GET /{id}` | Список и карточка |
| `POST /{id}/start` | Атомарно зафиксировать scope, claims и snapshot |
| `GET /{id}/items` | Строки, expected, факты, состояние пересчёта |
| `POST /{id}/items` | Добавить найденный товар с исходным expected 0 в допустимой области |
| `POST /{id}/items/{item_id}/count-attempts` | Начать новую попытку |
| `PUT /{id}/items/{item_id}/count-attempts/{attempt_id}` | Сохранить/завершить конкретную попытку с защитой от повтора |
| `POST /{id}/submit` | Отправить весь документ после полного действительного пересчёта |
| `POST /{id}/reconciliation` | Подготовить версию актуализации и список событий |
| `POST /{id}/approve` | Подтвердить документ целиком и атомарно применить adjustments |
| `POST /{id}/reject` | Закрыть без применения либо вернуть на пересчёт |
| `GET /{id}/history` | Попытки, события, решения и movements |

Найденный товар с `expected_start=0`, но с поступлением после запуска, нельзя автоматически считать излишком: его база на момент подсчёта должна учитывать это поступление.

### Атомарность

В одной transaction должны выполняться:

- Запуск: статус + фиксированный scope + все claims + snapshot.
- Сохранение принятой попытки: проверка версии строки + история + текущий указатель результата.
- Submit: полнота и валидность всех строк + смена состояния.
- Approval: повторная валидация + все movements + связи + решение + terminal status + освобождение claims.
- Reject/return: решение + переход состояния + корректное удержание/освобождение claims.

Массовое approval выполняется через одно DB connection. Разбиение документа на независимо коммитящиеся batches недопустимо.

### Идемпотентность

Рекомендуется сочетание:

1. Row lock документа и допустимых переходов.
2. Idempotency key для команд с fingerprint запроса.
3. Сохранённый ответ успешной команды.
4. Уникальность применения корректировки в отдельной таблице связей.
5. Точная ссылка на movement через registry либо подтверждённую пару `(movement_id, created_at)`.

Повтор approval после успешного commit должен вернуть прежний результат. Тот же ключ с другим содержимым — конфликт.

Одних `source_type/source_id/source_item_id` недостаточно: существующие поля не обеспечивают уникальность применения. При этом публичный `CREATE_MOVEMENT` их вообще не заполняет: [SQL](/home/skurbick/PROJECTS/WMSService/app/infrastructure/database/queries/movements.py:5).

Полезный существующий образец fingerprint + saved result — [KizActivationService](/home/skurbick/PROJECTS/WMSService/app/core/services/kiz_activation_service.py:25). Для нового модуля нужна собственная реализация этого принципа.

## 7. Риски и открытые технические вопросы

### Блокирующие безопасную реализацию

| Риск | Что необходимо установить или обеспечить |
|---|---|
| Неизвестный актуальный DB-контракт | Сверить runtime функции inventory, guards КИЗ, movement registry, constraints, partitions и применённые миграции. Текущая история миграций неполна |
| Недостоверный расходный `adjust` | Проверить реальную функцию; исключить movement без физического эффекта при отсутствии строки и неоднозначное сопоставление batch |
| Нет общей защиты count/approval от writers | Определить и внедрить общий протокол, включая отсутствующие inventory rows, ФБС, КИЗ и maintenance |
| Нет доказанного физического порядка ФБС/пересчёта | Установить, когда товар физически покидает адрес относительно сообщения, обработки и retry; предусмотреть отказ от автоматического восстановления |
| Нельзя однозначно исключить все физические КИЗ только по inventory | Проверить mixed stock, registered без holder и возможность распознавания маркированных единиц при подсчёте |
| Полнота writers не подтверждена | Проверить актуальные функции/триггеры и внешних writers; разрешённые обходы должны участвовать в контроле версий или инвалидировать строки |

### Требующие решения перед детальным проектированием

- **Партии:** есть ли ненулевые batch, пустые строки и смешанные batch/null остатки; как распределять агрегированную недостачу и куда относить излишек.
- **Точность:** существуют ли дробные остатки; как сохранить их без округления при integer-контракте публичных movements.
- **ФБС deployment:** активен legacy или task-level; включены ли Stage 1 и нужные migrations; как восстанавливаются ACK-нутые, но не обработанные shipments.
- **КИЗ migration baseline:** какие изменения ветки `feature/kiz` уже присутствуют в БД, несмотря на отсутствие в текущем дереве.
- **Системный recalculate:** допускается ли он при активных инвентаризациях; как регистрируется изменение проекции и взаимодействие с КИЗ guards.
- **Дерево:** есть ли расхождения parent/path, переносы между складами и остатки на неактивных адресах. Такие остатки нельзя молча исключить из полной инвентаризации.
- **Масштаб:** максимальное число адресов/строк, время атомарного approval, допустимое ожидание локальных блокировок.
- **Физическая граница пересчёта:** какой подтверждаемый сигнал позволяет отличить событие до, во время и после подсчёта; как закрываются in-flight операции.
- **Неожиданные товары:** как выявлять уже учтённые поступления после старта, чтобы expected 0 не приводил к повторному оприходованию.

Это технические вопросы реализации согласованных правил; пересматривать полную/частичную инвентаризацию, пересечение фильтров, согласование целого документа или отсутствие ролей для MVP не требуется.

### Некритичный технический долг

- Разделение прав на создание, счёт и согласование — согласно согласованному требованию.
- Приведение документации к фактическому состоянию: встречаются устаревшие утверждения об отсутствии тестов/миграций.
- Исправление legacy tasks/recount, если они не используются новым модулем.
- Развитие старых daily snapshots, если они не участвуют в новом процессе.
- Унификация enum API и DB: например, Python содержит `write_off`, а сохранённый DB CHECK — нет; DB содержит `pick/putaway`, которых нет в write enum. [Enum](/home/skurbick/PROJECTS/WMSService/app/core/enums.py:19), [DDL](/home/skurbick/PROJECTS/WMSService/docs/archive/runtime/2026-08-08-production/runtime_wms_schema.sql:1409).
- Автоматизация учёта применённых migrations и обслуживания будущих partitions.

Наличие валидированных ограничений для новых данных не доказывает чистоту всей истории: аудит 08.08.2026 нашёл один movement с неположительным quantity, а проверки quantity/direction были `NOT VALID`. Это исторический факт, не текущая оценка данных: [результаты](/home/skurbick/PROJECTS/WMSService/docs/database/production_schema_audit_2026-08-08.md:17).

## 8. Предлагаемые этапы реализации

### Этап 1. Подтвердить исходный контракт

Провести актуальный read-only аудит БД:

- Функции/триггеры всех физических writers.
- Movement registry и покрытие связей.
- Целостность inventory ↔ movements.
- Batch/null/empty-string и дробные остатки.
- Active/registered/container КИЗ и смешанные scopes.
- Parent/path и принадлежность складам.
- ФБС mode, pending/new backlog и фактический процесс изъятия товара.

Результат — согласованный перечень реальных гарантий и точная базовая схема для stage.

### Этап 2. Обеспечить корректировки и конкурентный протокол

До пользовательского approval:

- Устранить неоднозначность расходного adjust.
- Обеспечить точные source/movement связи и идемпотентность.
- Ввести общий контроль изменения scope.
- Подключить HTTP, RabbitMQ, retry, DB functions, КИЗ и maintenance.
- Проверить порядок блокировок, deadlock/retry и нулевые остатки.

Это наиболее чувствительный к обратной совместимости этап.

### Этап 3. Документ, область и запуск

Реализовать draft, selection, фиксированный scope, уникальные location claims и начальный snapshot.

Проверить конкурентные запуски, пустые локации, пересечения зон, SKU-only и изменения дерева.

### Этап 4. Последовательный пересчёт

Реализовать отдельные попытки, различие NULL/0, найденные товары, историю и контроль полноты.

Сначала можно проверять весь процесс без разрешения применения корректировок.

### Этап 5. Актуализация и конфликты

Добавить контрольные границы, события после счёта, подтверждение ответственного и возврат отдельных строк на пересчёт.

Особенно проверить delayed FBS, activation без movement и распаковку контейнера в россыпь.

### Этап 6. Атомарное согласование

Реализовать единый approval, повторяемый результат, отклонение с двумя действиями и корректное освобождение claims.

Не вводить частичное применение документа как способ обхода конфликтов.

### Этап 7. Stage-проверки и ограниченное включение

Начать с небольшой области, проверить сверку movements/inventory и нагрузку. Затем включить ФБС-локации после подтверждения физических границ и retry-поведения. Интеграцию с 1С в этот этап не добавлять.

### Какие существующие тесты полезны

| Тесты | Что можно использовать | Ограничение |
|---|---|---|
| [FBS atomicity](/home/skurbick/PROJECTS/WMSService/tests/test_fbs_atomicity.py:127) | Ошибка после movement/обновления СЗ и откат связанных изменений | Используется модель состояния в Python, не реальные PG triggers |
| [FBS task-level](/home/skurbick/PROJECTS/WMSService/tests/test_fbs_task_level_processing.py:104) | Дубли, mixed results, повтор после конкурентного завершения | Не заменяет настоящие конкурентные транзакции |
| [FBS KIZ Stage 1](/home/skurbick/PROJECTS/WMSService/tests/test_fbs_kiz_stage1.py:149) | Несколько физических источников, registered/active КИЗ, нехватка одного источника | В основном проверка сервиса через подмены |
| [Kit operations](/home/skurbick/PROJECTS/WMSService/tests/test_kit_operations.py:50), [re-sorting](/home/skurbick/PROJECTS/WMSService/tests/test_re_sorting_operations.py:42) | Source-связи, направления, loose restrictions, SQL locking contract | Не полное доказательство конкуренции БД |
| [System integrity](/home/skurbick/PROJECTS/WMSService/tests/test_system_integrity.py:201) | Разделение batch/container, пересборка available, отрицательный рассчитанный остаток | Проверки SQL/модели и mock transaction |
| [KIZ activation PostgreSQL](/home/skurbick/PROJECTS/WMSService/tests/test_kiz_activation_postgres.py:372) | Настоящие конкурентные activation, последняя unidentified единица, guards | Использует отдельную упрощённую fixture-схему; её триггер отличается от production dump |
| [KIZ import PostgreSQL](/home/skurbick/PROJECTS/WMSService/tests/test_kiz_import_b2_postgres.py:413) | Конкурентность, replay, отсутствие physical effect, registered/active различия | Требует disposable PostgreSQL |
| [Inventory history](/home/skurbick/PROJECTS/WMSService/tests/test_inventory_history.py:88) | Две стороны transfer, интервалы, агрегация | Дневная история, не физический пересчёт |

В текущем дереве не найдены отдельные полноценные тесты бизнес-процесса `TaskService.approve_discrepancy/complete_recount` и специализированная интеграционная suite положительного/отрицательного `adjust`.

### Обязательные новые сценарии

1. Все согласованные combinations scope, включая SKU-only, пустые адреса и пересечение фильтров.
2. Одновременный запуск пересекающихся документов, в том числе с разными SKU.
3. Фиксация состава descendants; новые/перемещённые адреса после запуска.
4. Не пересчитано, факт 0, положительный факт; запрет submit неполного документа.
5. Найденный SKU: допустимость области, expected 0, поступление после старта.
6. Повторные попытки и решения без потери истории.
7. Одновременные approval, потеря HTTP-ответа после commit, повтор с другим payload.
8. Ошибка последней корректировки: ни одного движения и изменения остатка во всём документе.
9. Расходный adjust из отсутствующей строки, недостаток, batch/null/empty-string и дробные количества.
10. Приход до/во время/после счёта, включая пример `10 → 8 → +3`.
11. Movement с меньшим ID или более ранним created_at, закоммиченный позже.
12. Два встречных движения с нулевой суммарной дельтой во время счёта.
13. ФБС standard/external/HTTP/retry, duplicate delivery, несколько movements и запаздывающая физическая отгрузка.
14. Active/registered КИЗ, mixed stock, activation без movement, shipment active КИЗ с нулевой дельтой unidentified.
15. Распаковка в россыпь, комплектация и пересортица.
16. Maintenance без movement, неизвестный writer и инвалидирование результата.
17. Новая строка inventory и новая batch-строка между проверкой и approval.
18. Deadlock/serialization retry и большой документ на фактической целевой схеме.

Для concurrency и trigger-проверок нужна отдельная PostgreSQL среда с подтверждённым полным DDL. Текущие KIZ fixtures не являются заменой такого стенда.

**Итог выполнения аудита:** во время исследования файлы, endpoints, миграции и тесты не изменялись. Для будущего модуля миграции потребуются. Основные оставшиеся риски — фактический DB-контракт, корректность `adjust`, исключение КИЗ и доказуемая граница между физическим пересчётом и параллельными операциями. До их разрешения можно проектировать документ и интерфейс, но обещать безопасное автоматическое применение расхождений нельзя.
