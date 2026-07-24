"""Dependency Injection для FastAPI"""

from fastapi import Depends
from asyncpg import Pool

from app.infrastructure.database.connection import get_db_pool

# Repositories
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.container_repository import ContainerRepository
from app.infrastructure.database.repositories.inventory_repository import InventoryRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.report_repository import ReportRepository
from app.infrastructure.database.repositories.system_repository import SystemRepository
from app.infrastructure.database.repositories.task_repository import TaskRepository
from app.infrastructure.database.repositories.notification_repository import NotificationRepository
from app.infrastructure.database.repositories.stock_reservation_repository import (
    StockReservationRepository,
)
from app.infrastructure.database.repositories.kit_operation_repository import KitOperationRepository
from app.infrastructure.database.repositories.re_sorting_operation_repository import (
    ReSortingOperationRepository,
)
from app.infrastructure.database.repositories.inventory_history_repository import (
    InventoryHistoryRepository,
)
from app.infrastructure.database.repositories.operations_history_repository import (
    OperationsHistoryRepository,
)
from app.infrastructure.database.repositories.receipt_history_repository import (
    ReceiptHistoryRepository,
)

# Services
from app.core.services.location_service import LocationService
from app.core.services.container_service import ContainerService
from app.core.services.inventory_service import InventoryService
from app.core.services.movement_service import MovementService
from app.core.services.report_service import ReportService
from app.core.services.system_service import SystemService
from app.core.services.task_service import TaskService
from app.core.services.notification_service import NotificationService
from app.core.services.stock_reservation_service import StockReservationService
from app.core.services.kit_operation_service import KitOperationService
from app.core.services.re_sorting_operation_service import ReSortingOperationService
from app.core.services.inventory_history_service import InventoryHistoryService
from app.core.services.operations_history_service import OperationsHistoryService
from app.core.services.receipt_history_service import ReceiptHistoryService


# === Repositories ===


def get_location_repository(pool: Pool = Depends(get_db_pool)) -> LocationRepository:
    """DI для LocationRepository"""
    return LocationRepository(pool)


def get_container_repository(pool: Pool = Depends(get_db_pool)) -> ContainerRepository:
    """DI для ContainerRepository"""
    return ContainerRepository(pool)


def get_inventory_repository(pool: Pool = Depends(get_db_pool)) -> InventoryRepository:
    """DI для InventoryRepository"""
    return InventoryRepository(pool)


def get_movement_repository(pool: Pool = Depends(get_db_pool)) -> MovementRepository:
    """DI для MovementRepository"""
    return MovementRepository(pool)


def get_inventory_history_repository(
    pool: Pool = Depends(get_db_pool),
) -> InventoryHistoryRepository:
    return InventoryHistoryRepository(pool)


def get_operations_history_repository(
    pool: Pool = Depends(get_db_pool),
) -> OperationsHistoryRepository:
    return OperationsHistoryRepository(pool)


def get_receipt_history_repository(pool: Pool = Depends(get_db_pool)) -> ReceiptHistoryRepository:
    return ReceiptHistoryRepository(pool)


def get_report_repository(pool: Pool = Depends(get_db_pool)) -> ReportRepository:
    """DI для ReportRepository"""
    return ReportRepository(pool)


def get_system_repository(pool: Pool = Depends(get_db_pool)) -> SystemRepository:
    """DI для SystemRepository"""
    return SystemRepository(pool)


# === Services ===


def get_location_service(
    repository: LocationRepository = Depends(get_location_repository),
) -> LocationService:
    """DI для LocationService"""
    return LocationService(repository)


def get_container_service(
    container_repository: ContainerRepository = Depends(get_container_repository),
    location_repository: LocationRepository = Depends(get_location_repository),
) -> ContainerService:
    """DI для ContainerService"""
    return ContainerService(container_repository, location_repository)


def get_inventory_service(
    inventory_repository: InventoryRepository = Depends(get_inventory_repository),
    location_repository: LocationRepository = Depends(get_location_repository),
    container_repository: ContainerRepository = Depends(get_container_repository),
) -> InventoryService:
    """DI для InventoryService"""
    return InventoryService(inventory_repository, location_repository, container_repository)


def get_movement_service(
    movement_repository: MovementRepository = Depends(get_movement_repository),
    location_repository: LocationRepository = Depends(get_location_repository),
) -> MovementService:
    """DI для MovementService"""
    return MovementService(movement_repository, location_repository)


def get_inventory_history_service(
    repository: InventoryHistoryRepository = Depends(get_inventory_history_repository),
) -> InventoryHistoryService:
    return InventoryHistoryService(repository)


def get_operations_history_service(
    repository: OperationsHistoryRepository = Depends(get_operations_history_repository),
) -> OperationsHistoryService:
    return OperationsHistoryService(repository)


def get_receipt_history_service(
    repository: ReceiptHistoryRepository = Depends(get_receipt_history_repository),
) -> ReceiptHistoryService:
    return ReceiptHistoryService(repository)


def get_report_service(
    report_repository: ReportRepository = Depends(get_report_repository),
) -> ReportService:
    """DI для ReportService"""
    return ReportService(report_repository)


def get_system_service(
    system_repository: SystemRepository = Depends(get_system_repository),
) -> SystemService:
    """DI для SystemService"""
    return SystemService(system_repository)


def get_task_repository(pool: Pool = Depends(get_db_pool)) -> TaskRepository:
    """DI для TaskRepository"""
    return TaskRepository(pool)


def get_notification_repository(pool: Pool = Depends(get_db_pool)) -> NotificationRepository:
    """DI для NotificationRepository"""
    return NotificationRepository(pool)


def get_stock_reservation_repository(
    pool: Pool = Depends(get_db_pool),
) -> StockReservationRepository:
    """DI для StockReservationRepository"""
    return StockReservationRepository(pool)


def get_kit_operation_repository(pool: Pool = Depends(get_db_pool)) -> KitOperationRepository:
    """DI для KitOperationRepository"""
    return KitOperationRepository(pool)


def get_re_sorting_operation_repository(
    pool: Pool = Depends(get_db_pool),
) -> ReSortingOperationRepository:
    return ReSortingOperationRepository(pool)


def get_re_sorting_operation_service(
    repository: ReSortingOperationRepository = Depends(get_re_sorting_operation_repository),
) -> ReSortingOperationService:
    return ReSortingOperationService(repository)


def get_notification_service(
    repository: NotificationRepository = Depends(get_notification_repository),
) -> NotificationService:
    """DI для NotificationService"""
    return NotificationService(repository)


def get_stock_reservation_service(
    repository: StockReservationRepository = Depends(get_stock_reservation_repository),
    location_repository: LocationRepository = Depends(get_location_repository),
) -> StockReservationService:
    """DI для StockReservationService"""
    return StockReservationService(repository, location_repository)


def get_kit_operation_service(
    repository: KitOperationRepository = Depends(get_kit_operation_repository),
) -> KitOperationService:
    """DI для KitOperationService"""
    return KitOperationService(repository)


def get_task_service(
    task_repo: TaskRepository = Depends(get_task_repository),
    notification_repo: NotificationRepository = Depends(get_notification_repository),
    movement_service: MovementService = Depends(get_movement_service),
    notification_service: NotificationService = Depends(get_notification_service),
) -> TaskService:
    """DI для TaskService"""
    return TaskService(task_repo, notification_repo, movement_service, notification_service)
