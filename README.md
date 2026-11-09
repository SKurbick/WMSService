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

Compose публикует сервис на порту хоста `8310` (все интерфейсы), передаёт
переменные из `.env` через `env_file` и ожидает внешнюю сеть
`vector_db_default`. PostgreSQL и RabbitMQ запускаются отдельно.

Для боевого хоста подготовлен локальный `.env.production.host` с текущими
значениями подключения (файл с секретами исключён из Git). Перенесите его
содержимое в `.env` рядом с compose-файлом. Рабочий локальный `.env` автоматически
не изменяется. В production-профиле:
- `DEBUG=false`, RabbitMQ AMQP port `5672`;
- основной FBS, резервы и KIZ import включены;
- external FBS выключен: при проверке 2026-11-09 его очередь отсутствовала;
- `FBS_TASK_PROCESSING_MODE=task_level` и
  `FBS_VALIDATE_ASSEMBLY_TASKS=false` сохранены из текущего окружения.

**Этот профиль предназначен для запуска после восстановления БД и данных.**
До запуска восстановить требуемые таблицы `public`, применить необходимую цепочку
миграций, проверить DB permissions и наличие локаций `FBS_LOCATION_CODE` и
`KIZ_IMPORT_RECEIPT_LOCATION_CODE`. Включённые consumers сразу начнут получать
сообщения, включая накопленные резервы. Если восстановление ещё идёт, оставить
все четыре `*_CONSUMER_ENABLED`/`CONSUMER_ENABLED` равными `false`.

После подготовки `.env` и внешней сети:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 wms_service_api
```

После изменения `.env` требуется пересоздание контейнера:
`docker compose up -d --force-recreate`; одного `restart` недостаточно.
Не запускать несколько экземпляров/uvicorn workers: каждый запускает собственные
consumers и retry worker, у которого пока нет конкурентного claim.

Образ копирует только `app/`, работает от пользователя `wms` и не содержит
`.env`. В Compose добавлена только передача переменных через `env_file`.
`/health` подтверждает только доступность HTTP, а не работоспособность БД/очередей.

В `requirements.txt` пока присутствуют dev-зависимости, а `aio-pika` не закреплён
по версии: сборка не полностью воспроизводима. Их разделение и фиксация версий
требуют отдельной проверки зависимостей.

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
