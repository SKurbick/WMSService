"""Глобальная обработка исключений"""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
    TaskNotFoundError,
    TaskForbiddenError,
    TaskInvalidStatusError,
    TaskPermissionDeniedError,
    TaskItemNotFoundError,
    NotificationNotFoundError,
    ProductNotFoundError,
    KitOperationNotFoundError,
    KitOperationValidationError,
    KitOperationConflictError,
    ReSortingOperationNotFoundError,
    ReSortingOperationValidationError,
    ReSortingOperationConflictError,
    InventoryHistoryValidationError,
    OperationsHistoryEventIdError,
    OperationsHistoryNotFoundError,
    OperationsHistoryValidationError,
    ReceiptHistoryNotFoundError,
    ReceiptHistoryValidationError,
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
            content={"detail": str(exc), "message": str(exc), "error_code": "LOCATION_NOT_FOUND"},
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

    @app.exception_handler(InventoryHistoryValidationError)
    async def inventory_history_validation_handler(
        request: Request, exc: InventoryHistoryValidationError
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": str(exc),
                "message": str(exc),
                "error_code": "INVENTORY_HISTORY_VALIDATION_ERROR",
            },
        )

    @app.exception_handler(OperationsHistoryValidationError)
    async def operations_history_validation_handler(
        request: Request, exc: OperationsHistoryValidationError
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": str(exc),
                "message": str(exc),
                "error_code": "OPERATIONS_HISTORY_VALIDATION_ERROR",
            },
        )

    @app.exception_handler(OperationsHistoryEventIdError)
    async def operations_history_event_id_handler(
        request: Request, exc: OperationsHistoryEventIdError
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "INVALID_OPERATION_EVENT_ID"},
        )

    @app.exception_handler(OperationsHistoryNotFoundError)
    async def operations_history_not_found_handler(
        request: Request, exc: OperationsHistoryNotFoundError
    ):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "OPERATION_HISTORY_NOT_FOUND"},
        )

    @app.exception_handler(ReceiptHistoryValidationError)
    async def receipt_history_validation_handler(
        request: Request, exc: ReceiptHistoryValidationError
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "RECEIPT_HISTORY_VALIDATION_ERROR"},
        )

    @app.exception_handler(ReceiptHistoryNotFoundError)
    async def receipt_history_not_found_handler(request: Request, exc: ReceiptHistoryNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "RECEIPT_HISTORY_NOT_FOUND"},
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

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found_handler(request: Request, exc: TaskNotFoundError):
        logger.warning(f"Заявка не найдена: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "TASK_NOT_FOUND"},
        )

    @app.exception_handler(TaskForbiddenError)
    async def task_forbidden_handler(request: Request, exc: TaskForbiddenError):
        logger.warning(f"Доступ к заявке запрещён: {exc}")
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc), "error_code": "TASK_FORBIDDEN"},
        )

    @app.exception_handler(TaskInvalidStatusError)
    async def task_invalid_status_handler(request: Request, exc: TaskInvalidStatusError):
        logger.warning(f"Недопустимый статус заявки: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "TASK_INVALID_STATUS"},
        )

    @app.exception_handler(TaskPermissionDeniedError)
    async def task_permission_denied_handler(request: Request, exc: TaskPermissionDeniedError):
        logger.warning(f"Нет прав на операцию с заявкой: {exc}")
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc), "error_code": "TASK_PERMISSION_DENIED"},
        )

    @app.exception_handler(TaskItemNotFoundError)
    async def task_item_not_found_handler(request: Request, exc: TaskItemNotFoundError):
        logger.warning(f"Позиция заявки не найдена: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "TASK_ITEM_NOT_FOUND"},
        )

    @app.exception_handler(NotificationNotFoundError)
    async def notification_not_found_handler(request: Request, exc: NotificationNotFoundError):
        logger.warning(f"Уведомление не найдено: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "NOTIFICATION_NOT_FOUND"},
        )

    @app.exception_handler(ProductNotFoundError)
    async def product_not_found_handler(request: Request, exc: ProductNotFoundError):
        logger.warning(f"Товар не найден: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "message": str(exc), "error_code": "PRODUCT_NOT_FOUND"},
        )

    @app.exception_handler(KitOperationNotFoundError)
    async def kit_operation_not_found_handler(request: Request, exc: KitOperationNotFoundError):
        logger.warning(f"Операция комплекта не найдена: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_code": "KIT_OPERATION_NOT_FOUND"},
        )

    @app.exception_handler(KitOperationValidationError)
    async def kit_operation_validation_handler(request: Request, exc: KitOperationValidationError):
        logger.warning(f"Некорректная операция комплекта: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_code": "KIT_OPERATION_VALIDATION_ERROR"},
        )

    @app.exception_handler(KitOperationConflictError)
    async def kit_operation_conflict_handler(request: Request, exc: KitOperationConflictError):
        logger.warning(f"Конфликт операции комплекта: {exc}")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "error_code": "KIT_OPERATION_CONFLICT"},
        )

    @app.exception_handler(ReSortingOperationNotFoundError)
    async def re_sorting_not_found_handler(request: Request, exc: ReSortingOperationNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "detail": str(exc),
                "message": str(exc),
                "error_code": "RE_SORTING_OPERATION_NOT_FOUND",
            },
        )

    @app.exception_handler(ReSortingOperationValidationError)
    async def re_sorting_validation_handler(
        request: Request, exc: ReSortingOperationValidationError
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": str(exc),
                "message": str(exc),
                "error_code": "RE_SORTING_OPERATION_VALIDATION_ERROR",
            },
        )

    @app.exception_handler(ReSortingOperationConflictError)
    async def re_sorting_conflict_handler(request: Request, exc: ReSortingOperationConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": str(exc),
                "message": str(exc),
                "error_code": "RE_SORTING_OPERATION_CONFLICT",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        details = jsonable_encoder(exc.errors())
        if request.url.path.startswith("/api/re-sorting-operations"):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "detail": details,
                    "message": "Проверьте обязательные поля и формат данных запроса",
                    "error_code": "RE_SORTING_REQUEST_VALIDATION_ERROR",
                },
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": details},
        )

    @app.exception_handler(DomainException)
    async def domain_exception_handler(request: Request, exc: DomainException):
        logger.error(f"Доменная ошибка: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "message": str(exc), "error_code": "DOMAIN_ERROR"},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Необработанная ошибка: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Внутренняя ошибка сервера",
                "message": "Внутренняя ошибка сервера",
                "error_code": "INTERNAL_ERROR",
            },
        )
