"""Точка входа FastAPI приложения"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool, close_db_pool
from app.api.v1.router import api_router
from app.middleware.error_handler import add_exception_handlers
from app.middleware.logging import add_logging_middleware
from app.consumer import start_consumer
from app.retry_worker import start_retry_worker

# Настройка логирования
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle events для FastAPI
    
    Запускается при старте и остановке приложения.
    """
    # Startup
    logger.info("🚀 Запуск WMS Service...")
    logger.info(f"📊 Подключение к БД: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    await get_db_pool()
    logger.info("✅ База данных подключена")

    consumer_task = asyncio.create_task(start_consumer())
    logger.info("✅ RabbitMQ consumer запущен")

    retry_task = asyncio.create_task(start_retry_worker())
    logger.info("✅ Retry worker запущен")

    yield

    # Shutdown
    logger.info("🛑 Остановка WMS Service...")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    retry_task.cancel()
    try:
        await retry_task
    except asyncio.CancelledError:
        pass
    await close_db_pool()
    logger.info("✅ База данных отключена")


# Создание FastAPI приложения
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
# WMS (Warehouse Management System) API

Микросервис для управления складом с адресным хранением.

## Основные возможности:

* **Локации** - управление иерархией склада (зоны, стеллажи, ячейки)
* **Контейнеры** - учёт паллет, коробок, QR-кодов
* **Остатки** - отслеживание inventory по локациям
* **Перемещения** - Event Sourcing через movements
* **Отчёты** - аналитика, ABC-анализ, оборачиваемость

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
