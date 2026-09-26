"""Точка входа FastAPI приложения"""

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import partial
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool, close_db_pool
from app.api.v1.router import api_router
from app.middleware.error_handler import add_exception_handlers
from app.middleware.logging import add_logging_middleware
from app.consumer import (
    start_consumer,
    start_external_fbs_consumer,
    start_kiz_import_consumer,
    start_stock_reservation_consumer,
)
from app.retry_worker import start_retry_worker

# Настройка логирования
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def _log_background_task_completion(task: asyncio.Task, *, task_name: str) -> None:
    """Сразу зафиксировать неожиданное завершение фоновой задачи."""
    if task.cancelled():
        logger.info("Фоновая задача остановлена | task=%s", task_name)
        return

    exception = task.exception()
    if exception is None:
        logger.error("Фоновая задача неожиданно завершилась | task=%s", task_name)
        return

    logger.error(
        "Фоновая задача завершилась с ошибкой | task=%s | error=%s",
        task_name,
        exception,
        exc_info=(type(exception), exception, exception.__traceback__),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Запуск WMS Service...")
    logger.info(f"📊 Подключение к БД: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    await get_db_pool()
    logger.info("✅ База данных подключена")

    consumer_task = None
    external_fbs_consumer_task = None
    reservation_consumer_task = None
    kiz_import_consumer_task = None
    retry_task = None

    if settings.CONSUMER_ENABLED:
        consumer_task = asyncio.create_task(start_consumer())
        logger.info("✅ FBS RabbitMQ consumer запущен")

    if settings.EXTERNAL_FBS_CONSUMER_ENABLED:
        external_fbs_consumer_task = asyncio.create_task(start_external_fbs_consumer())
        logger.info("External FBS RabbitMQ consumer запущен")

    if settings.CONSUMER_ENABLED or settings.EXTERNAL_FBS_CONSUMER_ENABLED:
        retry_task = asyncio.create_task(start_retry_worker())
        logger.info("Retry worker запущен")

    if settings.RESERVATION_CONSUMER_ENABLED:
        task_name = "stock-reservation-consumer"
        reservation_consumer_task = asyncio.create_task(
            start_stock_reservation_consumer(),
            name=task_name,
        )
        reservation_consumer_task.add_done_callback(
            partial(_log_background_task_completion, task_name=task_name)
        )
        logger.info("Stock reservation RabbitMQ consumer запланирован | task=%s", task_name)

    if settings.KIZ_IMPORT_CONSUMER_ENABLED:
        task_name = "kiz-import-consumer"
        kiz_import_consumer_task = asyncio.create_task(
            start_kiz_import_consumer(),
            name=task_name,
        )
        kiz_import_consumer_task.add_done_callback(
            partial(_log_background_task_completion, task_name=task_name)
        )
        logger.info("KIZ import RabbitMQ consumer запланирован | task=%s", task_name)

    yield

    logger.info("🛑 Остановка WMS Service...")
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    if external_fbs_consumer_task:
        external_fbs_consumer_task.cancel()
        try:
            await external_fbs_consumer_task
        except asyncio.CancelledError:
            pass
    if reservation_consumer_task:
        reservation_consumer_task.cancel()
        try:
            await reservation_consumer_task
        except asyncio.CancelledError:
            pass
    if kiz_import_consumer_task:
        kiz_import_consumer_task.cancel()
        try:
            await kiz_import_consumer_task
        except asyncio.CancelledError:
            pass
    if retry_task:
        retry_task.cancel()
        try:
            await retry_task
        except asyncio.CancelledError:
            pass
    await close_db_pool()
    logger.info("✅ База данных отключена")


# Метаданные тегов для Swagger UI
tags_metadata = [
    {
        "name": "История WMS",
        "description": "Read-only API для дневной истории остатков, общего журнала складских операций, деталей бизнес-операций и истории документов поступления.",
    },
    {
        "name": "FBS Shipments",
        "description": "Журнал отгрузок из ФБС зоны. Просмотр, статистика и переобработка записей, полученных из RabbitMQ.",
    },
    {
        "name": "KIZ Import",
        "description": "Read-only raw inbox сообщений импорта КИЗ из RabbitMQ.",
    },
]

# Создание FastAPI приложения
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    openapi_tags=tags_metadata,
    description="""
# WMS (Warehouse Management System) API

Микросервис для управления складом с адресным хранением.

## Основные возможности:

* **Локации** - управление иерархией склада (зоны, стеллажи, ячейки)
* **Контейнеры** - учёт паллет, коробок, QR-кодов
* **Остатки** - отслеживание inventory по локациям
* **Перемещения** - Event Sourcing через movements
* **Отчёты** - аналитика, ABC-анализ, оборачиваемость

## История WMS

* дневные остатки;
* единый журнал операций;
* детали операции;
* история документов поступления.

## Технологии:

* FastAPI + asyncpg
* PostgreSQL 16 с LTREE
* Event Sourcing
""",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware
add_logging_middleware(app)
add_exception_handlers(app)

# Routes
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["Системные"])
async def health_check():
    """
    Проверка здоровья сервиса

    Возвращает статус работоспособности API.
    """
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@app.get("/", tags=["Системные"])
async def root():
    """
    Корневой endpoint

    Информация о сервисе и ссылки на документацию.
    """
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8010,
        reload=settings.DEBUG,
        log_level="info" if settings.DEBUG else "warning",
    )
