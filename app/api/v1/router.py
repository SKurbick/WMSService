"""Главный роутер API v1"""

from fastapi import APIRouter
from app.api.v1.endpoints import (
    locations,
    containers,
    inventory,
    movements,
    reports,
    system,
    tasks,
    notifications,
    fbs_shipments,
)

api_router = APIRouter()

# Подключаем роутеры модулей
api_router.include_router(locations.router)
api_router.include_router(containers.router)
api_router.include_router(inventory.router)
api_router.include_router(movements.router)
api_router.include_router(reports.router)
api_router.include_router(system.router)
api_router.include_router(tasks.router)
api_router.include_router(notifications.router)
api_router.include_router(fbs_shipments.router, prefix="/fbs-shipments")
