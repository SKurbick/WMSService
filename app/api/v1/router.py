"""Главный роутер API v1"""

from fastapi import APIRouter
from app.api.v1.endpoints import (
    locations,
    containers,
    inventory,
    movements,
    reports,
    system,
)

api_router = APIRouter()

# Подключаем роутеры модулей
api_router.include_router(locations.router)
api_router.include_router(containers.router)
api_router.include_router(inventory.router)
api_router.include_router(movements.router)
api_router.include_router(reports.router)
api_router.include_router(system.router)
