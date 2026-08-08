# WMS Service

FastAPI-сервис складского учета с адресным хранением: локации, остатки, движения, контейнеры, задания, FBS, комплекты, пересортица и read-only история операций.

## Стек и устройство

- Python 3.11+ (Docker image использует Python 3.12), FastAPI, uvicorn;
- PostgreSQL, `asyncpg`, основная схема `wms` и зависимости от объектов `public`;
- RabbitMQ, `aio-pika`;
- прямой SQL без ORM; часть инвариантов и обновлений обеспечивает БД.

## Локальный запуск

1. Установить зависимости через `poetry install` либо создать `.venv` и выполнить `.venv/bin/pip install -r requirements.txt`.
2. Создать `.env` на основе `.env.example`. Обязательны `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.
3. Если RabbitMQ недоступен, установить `CONSUMER_ENABLED=false`, `EXTERNAL_FBS_CONSUMER_ENABLED=false`, `RESERVATION_CONSUMER_ENABLED=false`.
4. Запустить:

   ```bash
   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
   ```

Swagger UI доступен по `/docs`, ReDoc — `/redoc`, health check — `/health`, API prefix по умолчанию — `/api`.

## Docker

```bash
docker compose up --build
```

Compose публикует сервис на `localhost:8310`, но не поднимает PostgreSQL или RabbitMQ и ожидает внешнюю сеть `vector_db_default`. Текущий compose-файл также не передает переменные окружения в контейнер.

## Миграции

Alembic не используется. SQL находится в `scripts/migrations.sql` и `scripts/migrations/`. Порядок и ограничения описаны в [`scripts/migrations/README.md`](scripts/migrations/README.md). `docs/archive/snapshots/wms_schema.sql` — снимок схемы, а не замена миграций.

## Проверки

```bash
.venv/bin/pytest -q
ruff check app
```

Часть тестов использует mocks и не проверяет PostgreSQL functions, triggers и конкурентные сценарии на реальной БД.

## Документация

Начинать изучение следует с [`docs/README.md`](docs/README.md), где указаны порядок чтения и статус каждого материала.

Для изменений остатков, движений, контейнеров и локаций необходимо отдельно проверять транзакционные границы и конкурентный доступ по коду и целевой схеме PostgreSQL.
