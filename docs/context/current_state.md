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
