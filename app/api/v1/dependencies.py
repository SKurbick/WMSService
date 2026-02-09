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

# Services
from app.core.services.location_service import LocationService
from app.core.services.container_service import ContainerService
from app.core.services.inventory_service import InventoryService
from app.core.services.movement_service import MovementService
from app.core.services.report_service import ReportService
from app.core.services.system_service import SystemService


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
