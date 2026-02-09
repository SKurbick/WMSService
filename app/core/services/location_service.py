"""Сервис для работы с локациями (бизнес-логика)"""

from typing import List
from app.core.schemas.location import (
    LocationCreate,
    LocationUpdate,
    LocationResponse,
    LocationChildResponse,
    LocationDeactivateResponse,
)
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.core.exceptions import LocationNotFoundError, ParentLocationInactiveError


class LocationService:
    """Сервис для работы с локациями"""

    def __init__(self, repository: LocationRepository):
        self.repo = repository

    async def create_location(self, data: LocationCreate) -> LocationResponse:
        """
        Создать локацию с валидацией бизнес-правил
        
        Проверяет:
        - Если указан parent_location_id, то родитель должен существовать и быть активным
        """
        # Бизнес-правило: если есть родитель, проверяем что он активен
        if data.parent_location_id:
            parent = await self.repo.get_by_id(data.parent_location_id)
            if not parent:
                raise LocationNotFoundError(
                    f"Родительская локация с ID {data.parent_location_id} не найдена"
                )
            if not parent["is_active"]:
                raise ParentLocationInactiveError(
                    f"Родительская локация '{parent['location_code']}' неактивна. "
                    f"Нельзя создать дочернюю локацию."
                )

        # Создание через репозиторий
        location = await self.repo.create(data.model_dump())

        # Конвертация asyncpg.Record → Pydantic
        return LocationResponse.model_validate(dict(location))

    async def get_location_by_id(self, location_id: int) -> LocationResponse:
        """Получить локацию по ID"""
        location = await self.repo.get_by_id(location_id)
        if not location:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")
        return LocationResponse.model_validate(dict(location))

    async def get_location_by_code(self, location_code: str) -> LocationResponse:
        """Получить локацию по коду"""
        location = await self.repo.get_by_code(location_code)
        if not location:
            raise LocationNotFoundError(f"Локация с кодом '{location_code}' не найдена")
        return LocationResponse.model_validate(dict(location))

    async def get_children(
        self, location_id: int, recursive: bool = True
    ) -> List[LocationChildResponse]:
        """
        Получить дочерние локации
        
        Args:
            location_id: ID родительской локации
            recursive: Если True - все потомки (через LTREE), если False - только прямые дети
        """
        # Проверяем что родитель существует
        parent = await self.repo.get_by_id(location_id)
        if not parent:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        # Получаем дочерние локации
        children = await self.repo.get_children(location_id, recursive)
        return [LocationChildResponse.model_validate(dict(child)) for child in children]

    async def update_location(self, location_id: int, data: LocationUpdate) -> LocationResponse:
        """Обновить локацию"""
        # Проверка существования
        existing = await self.repo.get_by_id(location_id)
        if not existing:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        # Обновление (только переданные поля)
        updated = await self.repo.update(location_id, data.model_dump(exclude_unset=True))
        return LocationResponse.model_validate(dict(updated))

    async def deactivate_location(self, location_id: int) -> LocationDeactivateResponse:
        """Деактивировать локацию"""
        # Проверка существования
        existing = await self.repo.get_by_id(location_id)
        if not existing:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        # Деактивация
        deactivated = await self.repo.deactivate(location_id)
        return LocationDeactivateResponse.model_validate(dict(deactivated))

    async def find_available_location(
        self, product_id: str, quantity: int, zone_type: str = "storage"
    ) -> dict:
        """
        Найти оптимальную свободную ячейку для размещения товара
        
        Использует PostgreSQL функцию wms.find_available_location()
        которая учитывает вес, объём и текущую загруженность
        """
        result = await self.repo.find_available(product_id, quantity, zone_type)
        if not result:
            raise LocationNotFoundError(
                f"Не найдена свободная ячейка для товара '{product_id}' "
                f"(кол-во: {quantity}, зона: {zone_type})"
            )
        return dict(result)
