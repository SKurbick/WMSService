"""Сервис для работы с движениями товаров (бизнес-логика)"""

from typing import List, Optional, Set
from datetime import date
from app.core.schemas.movement import (
    MovementCreate,
    MovementBulkCreateResponse,
    MovementCreateResponse,
    MovementResponse,
)
from app.infrastructure.database.repositories.movement_repository import MovementRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.queries import movements as queries
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

    async def create_movement(
        self,
        data: List[MovementCreate],
    ) -> MovementBulkCreateResponse:
        """
        Создать movements атомарно в своей транзакции.

        Публичный API для обычных случаев.
        Сам открывает и управляет транзакцией.
        """
        pool = self.movement_repo.pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                created = await self.create_movement_in_transaction(conn, data)
        return MovementBulkCreateResponse(created=created, total=len(created))

    async def create_movement_in_transaction(
        self,
        conn,
        data: List[MovementCreate],
    ) -> List[MovementCreateResponse]:
        """
        Создать movements внутри существующей транзакции.

        Используется когда movements должны быть частью более широкой
        атомарной операции (например, с validate_assembly_tasks).

        Args:
            conn: Активное соединение с открытой транзакцией
            data: Список movements для создания

        Returns:
            Список созданных movements

        Raises:
            InvalidMovementError: Если валидация не прошла
            LocationNotFoundError: Если локация не найдена
        """
        # 1. Валидация базовых правил
        for idx, movement in enumerate(data):
            if not movement.from_location_code and not movement.to_location_code:
                raise InvalidMovementError(
                    f"Movement #{idx + 1}: Должна быть указана хотя бы одна локация"
                )

        # 2. Батч-валидация локаций
        all_location_codes: Set[str] = set()
        for movement in data:
            if movement.from_location_code:
                all_location_codes.add(movement.from_location_code)
            if movement.to_location_code:
                all_location_codes.add(movement.to_location_code)

        for location_code in all_location_codes:
            location = await self.location_repo.get_by_code_in_transaction(
                conn, location_code
            )
            if not location:
                raise LocationNotFoundError(f"Локация '{location_code}' не найдена")

        # 3. Создание movements (используем переданный conn)
        created_movements = []
        for movement in data:
            movement_data = movement.model_dump()
            result = await conn.fetchrow(
                queries.CREATE_MOVEMENT,
                movement.movement_type.value,
                movement_data["product_id"],
                movement_data.get("from_location_code"),
                movement_data.get("to_location_code"),
                movement_data["quantity"],
                movement_data.get("batch_number"),
                movement_data.get("container_code"),
                movement_data.get("user_name"),
                movement_data.get("reason"),
            )
            created_movements.append(
                MovementCreateResponse.model_validate(dict(result))
            )

        return created_movements

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
