"""Middleware для логирования запросов"""

import time
import logging
from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def add_logging_middleware(app: FastAPI):
    """Добавить middleware для логирования запросов"""

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Логирование входящих запросов и ответов"""
        start_time = time.time()

        # Логируем входящий запрос
        logger.info(f"→ {request.method} {request.url.path}")

        # Выполняем запрос
        response = await call_next(request)

        # Считаем время выполнения
        process_time = time.time() - start_time

        # Логируем ответ
        logger.info(
            f"← {request.method} {request.url.path} "
            f"[{response.status_code}] {process_time:.3f}s"
        )

        # Добавляем заголовок с временем выполнения
        response.headers["X-Process-Time"] = str(process_time)

        return response
