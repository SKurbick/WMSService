# Architecture Notes

## Общая архитектура

Сервис построен слоями:

- API endpoints принимают запросы и вызывают сервисы.
- Services содержат основную бизнес-логику.
- Repositories инкапсулируют SQL-вызовы через `asyncpg`.
- SQL хранится отдельно в `app/infrastructure/database/queries`.

DI находится в `app/api/v1/dependencies.py`.

## База данных

Проект не использует ORM. Все операции идут через `asyncpg.Pool`.

Важная часть бизнес-логики находится в БД:

- генерация `location_code` и LTREE `path`;
- обновление `inventory` из `movements`;
- регистрация контейнера;
- распаковка контейнера;
- поиск доступной ячейки;
- summary позиций заявки.

Без DDL/миграций невозможно полностью подтвердить constraints, индексы, foreign keys и блокировки.

## Event sourcing остатков

В описании FastAPI и системных endpoint'ах явно заявлен Event Sourcing через `movements`.

Фактическая модель:

- `wms.movements` - журнал событий;
- `wms.inventory` - текущая проекция;
- `validate-integrity` сверяет проекцию с журналом;
- `recalculate-inventory` пересоздает проекцию из журнала.

## Транзакции

Явно транзакционные места:

- `MovementService.create_movement` - весь batch movements в одной транзакции.
- `MovementService.create_movement_in_transaction` - вставка movements внутри внешней транзакции.
- `TaskRepository.create` - создание заявки и позиций в одной транзакции.
- `handle_write_off_fbs` - создание shipment_items в транзакции.
- ФБС-списание каждой группы product_id - validate assembly tasks и create movement в одной транзакции.
- Retry worker повторяет ту же транзакционную схему для ФБС.

Потенциально неатомарные сценарии:

- `TaskService.complete_task` обновляет позиции, создает дочернюю заявку, меняет статусы, создает уведомления и movements через несколько repository/service calls без общей транзакции на весь сценарий.
- `TaskService.approve_discrepancy` создает movements и меняет статусы через несколько отдельных операций.
- `ContainerService.update_container_location` полагается на update и триггер БД, но в Python нет отдельной транзакции вокруг проверки статуса и update.

## Фоновые процессы

При `settings.CONSUMER_ENABLED = True` lifespan запускает:

- RabbitMQ consumer;
- retry worker.

Consumer:

- читает `settings.RABBITMQ_QUEUE`;
- сохраняет raw JSON в БД;
- ACK'ает сообщение после записи raw;
- валидирует и запускает ФБС-обработчик.

Retry worker:

- спит 5 минут между итерациями;
- выбирает `pending_retry` items с `next_retry_at <= now()`;
- группирует по shipment и product;
- повторяет списание;
- пересчитывает статус shipment.

## Ошибки

В проекте есть middleware обработки ошибок (`app/middleware/error_handler.py`) и доменные исключения (`app/core/exceptions.py`). В этой первичной карте детали middleware не описаны, так как задача была сфокусирована на домене WMS.

## Документационные ограничения

- Нет миграций и DDL.
- Нет тестов, по которым можно подтвердить expected behavior.
- Нет тел PostgreSQL-функций и триггеров.
- Некоторые критичные инварианты, вероятно, обеспечиваются БД, но это не проверяется из репозитория.
