# Current State

## Назначение

Проект является FastAPI-сервисом WMS для адресного хранения. Основной код находится в `app/`.

Сервис покрывает:

- иерархию складских локаций;
- контейнеры;
- остатки;
- движения товаров;
- заявки на складские операции;
- отчеты;
- системные операции сверки/пересчета;
- журнал и обработку ФБС-отгрузок из RabbitMQ;
- уведомления.

## Технологический стек

- Python, FastAPI.
- `asyncpg` и PostgreSQL.
- Схема БД `wms`, также используются таблицы `public.products`, `public.assembly_task`, `public.user_permissions`.
- Подключение к БД через глобальный pool `asyncpg.create_pool`.
- `search_path` для соединений: `wms,public`.
- RabbitMQ consumer через `aio_pika`.

## Структура приложения

- `app/main.py` - создание FastAPI-приложения, lifespan, middleware, подключение API router, запуск consumer/retry worker.
- `app/api/v1/endpoints/` - HTTP endpoints.
- `app/core/services/` - бизнес-логика.
- `app/core/schemas/` - Pydantic-схемы запросов/ответов.
- `app/core/enums.py` - доменные enum-типы.
- `app/infrastructure/database/repositories/` - репозитории поверх `asyncpg`.
- `app/infrastructure/database/queries/` - SQL-запросы.
- `app/handlers/write_off_fbs_handler.py` - обработка ФБС-списаний.
- `app/consumer.py` - RabbitMQ consumer.
- `app/retry_worker.py` - фоновая переобработка pending retry ФБС-позиций.

## Миграции и тесты

В репозитории не обнаружены миграции (`alembic`, `migrations`) и тесты. Карта БД составлена по SQL-запросам, схемам ответов и вызовам PostgreSQL-функций.

## Важное текущее состояние

- ORM-моделей нет: проект работает напрямую с SQL через `asyncpg`.
- Остатки считаются производными от `wms.movements`; в коде есть системная сверка и пересчет `wms.inventory` из движений.
- Создание batch movements в публичном endpoint выполняется в транзакции.
- Часть операций атомарности делегирована PostgreSQL-функциям и триггерам:
  - `wms.register_container`;
  - `wms.unpack_from_container`;
  - `wms.find_available_location`;
  - триггер обновления inventory при вставке в `wms.movements`;
  - триггер генерации `location_code` и `path` для `wms.locations`.
- Конкурентный доступ явно обработан не везде; где нет явных блокировок в Python/SQL, это вынесено в открытые вопросы.

## Мягкие резервы товаров

Добавлен механизм мягкого резерва по `product_id + external_order_id` для сообщений RabbitMQ формата `wild/orders`. Резервы хранятся отдельно от физических остатков: код не пишет резервы в `wms.inventory` и не создает `wms.movements`.

Основные компоненты:

- `app/core/services/stock_reservation_service.py` - бизнес-логика статусов резервов и audit входящих событий;
- `app/infrastructure/database/repositories/stock_reservation_repository.py` - доступ к таблицам резервов и view доступности;
- `app/infrastructure/database/queries/stock_reservations.py` - SQL для резервов;
- `app/consumer.py` - содержит отдельные RabbitMQ consumers для FBS write-off и stock reservations.

## RabbitMQ consumers

RabbitMQ обработка разделена по очередям:

- FBS write-off consumer запускается через `settings.CONSUMER_ENABLED` и слушает `settings.RABBITMQ_QUEUE`;
- stock reservation consumer запускается через `settings.RESERVATION_CONSUMER_ENABLED` и слушает `settings.STOCK_RESERVATION_QUEUE`;
- пустая очередь резервов не создает записей в БД: consumer просто ожидает новое сообщение от RabbitMQ;
- ошибки БД при обработке резервов логируются с `exc_info=True` и приводят к `nack(requeue=True)`, чтобы отсутствие таблиц/view резервов не маскировалось.
