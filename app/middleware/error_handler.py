"""Глобальная обработка исключений"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from app.core.exceptions import (
    DomainException,
    LocationNotFoundError,
    ContainerNotFoundError,
    InventoryNotFoundError,
    ParentLocationInactiveError,
    ContainerAlreadyExistsError,
    InsufficientInventoryError,
    InsufficientContainerQuantityError,
)
import logging

logger = logging.getLogger(__name__)


def add_exception_handlers(app: FastAPI):
    """Добавить обработчики исключений в приложение"""

    @app.exception_handler(LocationNotFoundError)
    async def location_not_found_handler(request: Request, exc: LocationNotFoundError):
        logger.warning(f"Локация не найдена: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "LOCATION_NOT_FOUND"},
        )

    @app.exception_handler(ContainerNotFoundError)
    async def container_not_found_handler(request: Request, exc: ContainerNotFoundError):
        logger.warning(f"Контейнер не найден: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "CONTAINER_NOT_FOUND"},
        )

    @app.exception_handler(InventoryNotFoundError)
    async def inventory_not_found_handler(request: Request, exc: InventoryNotFoundError):
        logger.warning(f"Остатки не найдены: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "INVENTORY_NOT_FOUND"},
        )

    @app.exception_handler(ParentLocationInactiveError)
    async def parent_location_inactive_handler(request: Request, exc: ParentLocationInactiveError):
        logger.warning(f"Родительская локация неактивна: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "PARENT_LOCATION_INACTIVE"},
        )

    @app.exception_handler(ContainerAlreadyExistsError)
    async def container_already_exists_handler(request: Request, exc: ContainerAlreadyExistsError):
        logger.warning(f"Контейнер уже существует: {exc}")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "error_code": "CONTAINER_ALREADY_EXISTS"},
        )

    @app.exception_handler(InsufficientInventoryError)
    async def insufficient_inventory_handler(request: Request, exc: InsufficientInventoryError):
        logger.warning(f"Недостаточно товара: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "INSUFFICIENT_INVENTORY"},
        )

    @app.exception_handler(InsufficientContainerQuantityError)
    async def insufficient_container_quantity_handler(
        request: Request, exc: InsufficientContainerQuantityError
    ):
        logger.warning(f"Недостаточно товара в контейнере: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "INSUFFICIENT_CONTAINER_QUANTITY"},
        )

    @app.exception_handler(DomainException)
    async def domain_exception_handler(request: Request, exc: DomainException):
        logger.error(f"Доменная ошибка: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "DOMAIN_ERROR"},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Необработанная ошибка: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Внутренняя ошибка сервера", "error_code": "INTERNAL_ERROR"},
        )
