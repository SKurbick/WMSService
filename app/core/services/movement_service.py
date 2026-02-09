"""Сервис для работы с движениями товаров (бизнес-логика)"""

from typing import List, Optional
from datetime import date
from app.core.schemas.movement import (
    MovementCreate,
    MovementCreateResponse,
    MovementResponse,
)
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.core.exceptions import (
    InvalidMovementError,
    LocationNotFoundError,
)


class MovementService:
    """Сервис для работы с движениями товаров"""

    def __init__(
        self,
        movement_repository: MovementRepository,
        location_repository: LocationRepository,
    ):
        self.movement_repo = movement_repository
        self.location_repo = location_repository

    async def create_movement(self, data: MovementCreate) -> MovementCreateResponse:
        """
        Создать движение товара

        Валидирует бизнес-правила и создаёт движение.
        Триггер в БД автоматически обновит inventory.
        """
        # Валидация: должна быть указана хотя бы одна локация
        if not data.from_location_code and not data.to_location_code:
            raise InvalidMovementError(
                "Должна быть указана хотя бы одна локация (from или to)"
            )

        # Проверка существования локаций
        if data.from_location_code:
            from_loc = await self.location_repo.get_by_code(data.from_location_code)
            if not from_loc:
                raise LocationNotFoundError(
                    f"Локация-источник '{data.from_location_code}' не найдена"
                )

        if data.to_location_code:
            to_loc = await self.location_repo.get_by_code(data.to_location_code)
            if not to_loc:
                raise LocationNotFoundError(
                    f"Локация-назначение '{data.to_location_code}' не найдена"
                )

        # Создание движения
        movement_data = data.model_dump()
        movement_data["movement_type"] = data.movement_type.value
        result = await self.movement_repo.create(movement_data)

        return MovementCreateResponse.model_validate(dict(result))

    async def get_movements(
        self,
        product_id: Optional[str] = None,
        container_code: Optional[str] = None,
        movement_type: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MovementResponse]:
        """
        Получить историю движений с фильтрами

        Возвращает список движений с возможностью фильтрации
        по товару, контейнеру, типу движения и периоду.
        """
        results = await self.movement_repo.get_movements(
            product_id=product_id,
            container_code=container_code,
            movement_type=movement_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
        return [MovementResponse.model_validate(dict(r)) for r in results]

    async def get_movements_by_product(
        self, product_id: str, limit: int = 100
    ) -> List[MovementResponse]:
        """
        Получить историю движений по товару

        Возвращает полную историю движений конкретного товара.
        """
        results = await self.movement_repo.get_by_product(product_id, limit)
        return [MovementResponse.model_validate(dict(r)) for r in results]
