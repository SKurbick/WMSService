"""Сервис для работы с контейнерами (бизнес-логика)"""

from typing import List
from app.core.schemas.container import (
    ContainerRegister,
    ContainerRegisterResponse,
    ContainerResponse,
    ContainerStatusUpdate,
    ContainerStatusUpdateResponse,
    ContainerHistoryItem,
    ContainerInLocation,
)
from app.infrastructure.database.repositories.container_repository import ContainerRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.core.exceptions import (
    ContainerNotFoundError,
    ContainerAlreadyExistsError,
    ContainerBlockedError,
    ContainerContentsNotAllowedError,
    LocationNotFoundError,
)


class ContainerService:
    """Сервис для работы с контейнерами"""

    def __init__(
        self,
        container_repository: ContainerRepository,
        location_repository: LocationRepository,
    ):
        self.container_repo = container_repository
        self.location_repo = location_repository

    async def register_container(self, data: ContainerRegister) -> ContainerRegisterResponse:
        """
        Зарегистрировать контейнер

        Создать пустой контейнер. Товар добавляется только через controlled fill.
        """
        if data.contents:
            raise ContainerContentsNotAllowedError(
                "Создание контейнера с товаром запрещено; создайте пустой контейнер "
                "и используйте POST /api/container-operations/fill"
            )

        # Проверка: контейнер с таким QR уже существует?
        if await self.container_repo.exists(data.qr_code):
            raise ContainerAlreadyExistsError(
                f"Контейнер с QR-кодом '{data.qr_code}' уже существует"
            )

        # Проверка: локация существует?
        location = await self.location_repo.get_by_code(data.location_code)
        if not location:
            raise LocationNotFoundError(
                f"Локация с кодом '{data.location_code}' не найдена"
            )

        # Регистрация через репозиторий
        result = await self.container_repo.register(
            qr_code=data.qr_code,
            container_type=data.container_type.value,
            location_code=data.location_code,
            contents=[],
        )

        return ContainerRegisterResponse.model_validate(dict(result))

    async def get_container_by_qr(self, qr_code: str) -> ContainerResponse:
        """Получить контейнер по QR-коду"""
        container = await self.container_repo.get_by_qr_code(qr_code)
        if not container:
            raise ContainerNotFoundError(f"Контейнер с QR-кодом '{qr_code}' не найден")
        return ContainerResponse.model_validate(dict(container))

    async def update_container_status(
        self, container_id: int, data: ContainerStatusUpdate
    ) -> ContainerStatusUpdateResponse:
        """Обновить статус контейнера"""
        # Проверка: контейнер существует?
        container = await self.container_repo.get_by_id(container_id)
        if not container:
            raise ContainerNotFoundError(f"Контейнер с ID {container_id} не найден")

        # Обновление статуса
        result = await self.container_repo.update_status(container_id, data.status.value)
        if not result:
            raise ContainerBlockedError(
                "Невозможно изменить статус заблокированного контейнера"
            )

        return ContainerStatusUpdateResponse.model_validate(dict(result))

    async def get_container_history(self, qr_code: str) -> List[ContainerHistoryItem]:
        """Получить историю контейнера"""
        # Проверка: контейнер существует?
        if not await self.container_repo.exists(qr_code):
            raise ContainerNotFoundError(f"Контейнер с QR-кодом '{qr_code}' не найден")

        # Получение истории
        history = await self.container_repo.get_history(qr_code)
        return [ContainerHistoryItem.model_validate(dict(item)) for item in history]

    async def get_containers_in_location(
        self,
        location_id: int,
        status: str = None,
        container_type: str = None,
    ) -> List[ContainerInLocation]:
        """Получить контейнеры в локации"""
        # Проверка: локация существует?
        location = await self.location_repo.get_by_id(location_id)
        if not location:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        # Получение контейнеров
        containers = await self.container_repo.get_containers_in_location(
            location_id, status, container_type
        )
        return [ContainerInLocation.model_validate(dict(c)) for c in containers]
