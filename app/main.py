"""Точка входа FastAPI приложения"""

import asyncio
import logging
from contextlib import asynccontextmanager
from html import escape
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool, close_db_pool
from app.api.v1.router import api_router
from app.api.v1.openapi_kiz import preserve_response_examples
from app.middleware.error_handler import add_exception_handlers
from app.middleware.logging import add_logging_middleware
from app.consumer import (
    start_consumer,
    start_external_fbs_consumer,
    start_stock_reservation_consumer,
)
from app.retry_worker import start_retry_worker

# Настройка логирования
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Запуск WMS Service...")
    logger.info(f"📊 Подключение к БД: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    await get_db_pool()
    logger.info("✅ База данных подключена")

    consumer_task = None
    external_fbs_consumer_task = None
    reservation_consumer_task = None
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
        reservation_consumer_task = asyncio.create_task(start_stock_reservation_consumer())
        logger.info("✅ Stock reservation RabbitMQ consumer запущен")

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
        "name": 'Локации',
        "description": 'Иерархия складов, зон и адресов: поиск, параметры локаций и QR-коды.',
    },
    {
        "name": 'Контейнеры',
        "description": 'Регистрация, содержимое, перемещение и распаковка контейнеров.',
    },
    {
        "name": 'Остатки',
        "description": 'Физические остатки по товарам и адресам; отдельно — доступность с учётом мягких резервов. Эти запросы не назначают КИЗ.',
    },
    {
        "name": 'Движения',
        "description": 'Приход, расход, перемещение и ручная корректировка. Направление задают исходная и целевая локации.',
    },
    {
        "name": 'КИЗ',
        "description": 'Назначение кода существующей единице, карточки и история. Только точный available-остаток без партии и контейнера; физической отгрузки КИЗ в v1 нет.',
    },
    {
        "name": 'ФБС-отгрузки',
        "description": 'Приём отгрузок, журнал результатов и повторные попытки. Отдельные группы товаров обрабатываются атомарно.',
    },
    {
        "name": 'Операции комплектов',
        "description": 'Сборка и разборка комплектов в разрешённых локациях; состав берётся из карточки товара.',
    },
    {
        "name": 'Операции пересортицы',
        "description": 'Исправление учёта между двумя товарами на одном разрешённом адресе с одинаковым количеством расхода и прихода.',
    },
    {
        "name": 'История WMS',
        "description": 'Чтение дневной истории остатков, общего журнала складских операций и ревизий поступлений.',
    },
    {
        "name": 'Заявки',
        "description": 'Жизненный цикл складских заявок: назначение, исполнение, расхождения и пересчёт.',
    },
    {
        "name": 'Отчёты',
        "description": 'Аналитика зон, активности товаров, партий и оборачиваемости.',
    },
    {
        "name": 'Уведомления',
        "description": 'Просмотр непрочитанных уведомлений и отметка прочтения.',
    },
    {
        "name": 'Системные',
        "description": 'Проверки состояния и целостности, снимки и обслуживание остатков. Пересчёт изменяет данные и блокирует запись на время выполнения.',
    },
]

# Создание FastAPI приложения
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    openapi_tags=tags_metadata,
    description="""
# API складского сервиса WMS

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

## Как пользоваться документацией

Разверните операцию и выберите пример в **Example Value**. Вкладка **Schema** показывает
типы, обязательность и описания полей. **Try it out → Execute** выполняет настоящий запрос:
для изменения данных используйте подготовленное тестовое окружение.
Коды товаров и адресов в примерах условные — замените их существующими значениями.

## Форматы и ошибки

* Имена JSON-полей и значения статусов сохраняются на английском: это контракт API.
* Даты: `YYYY-MM-DD`; дата со временем: ISO 8601 с часовым поясом.
* Формат количества зависит от схемы: Decimal в ответах КИЗ и пересчёта — строки,
  например `"10.00"`; целочисленные поля — JSON-числа.
* `limit` ограничивает размер страницы, `offset` — число пропущенных записей;
  допустимые значения смотрите у конкретного запроса.
* `404` — объект не найден; `422` — запрос не прошёл проверку;
  `409` — конфликт состояния или конкурентной записи. Тело ошибки зависит от операции.
* Для `CONCURRENT_WRITE_CONFLICT` повторяется вся откатившаяся операция.
  Не повторяйте успешные write-запросы автоматически: универсального ключа идемпотентности нет.

## КИЗ и обычные движения

КИЗ идентифицирует существующую единицу на точном адресе, без партии и контейнера.
Назначение не создаёт приход и не меняет количество. Обычные движения могут расходовать
только неидентифицированную часть. При отсутствии активных КИЗ это ограничение расхода
не возникает. Отгрузка конкретных КИЗ в первой версии отсутствует.

""",
    swagger_ui_parameters={"filter": True, "displayRequestDuration": True,
                           "docExpansion": "none", "defaultModelsExpandDepth": 0},
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


@app.get("/health", tags=["Системные"], summary="Проверить доступность HTTP-сервиса")
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


@app.get(
    "/", tags=["Системные"], summary="Открыть стартовую страницу сервиса",
    responses={200: {"content": {"text/html": {}}, "description": "Страница со ссылками в браузере; JSON для API-клиентов."}},
)
async def root(request: Request):
    """
    Корневой endpoint

    Информация о сервисе и ссылки на документацию.
    """
    if "text/html" in request.headers.get("accept", "").lower():
        service = escape(settings.APP_NAME)
        version = escape(settings.APP_VERSION)
        links = "".join(
            f'<li><a href="{escape(str(request.url_for(route)), quote=True)}">{label}</a></li>'
            for route, label in (
                ("swagger_ui_html", "Swagger — интерактивная документация"),
                ("redoc_html", "ReDoc — документация API"),
                ("health_check", "Проверка состояния сервиса"),
            )
        )
        return HTMLResponse(f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{service}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 680px; margin: 64px auto; padding: 0 24px; color: #1f2937; }}
    li {{ margin: 20px 0; }}
    a {{ color: #1559b7; }}
  </style>
</head>
<body><main>
  <h1>{service}</h1>
  <p>Версия {version}</p>
  <ul>{links}</ul>
</main></body>
</html>""")
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
    }


preserve_response_examples(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8010,
        reload=settings.DEBUG,
        log_level="info" if settings.DEBUG else "warning",
    )
