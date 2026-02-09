"""Сервис для работы с инвентарём (остатками)"""

from typing import List, Optional
from app.core.schemas.inventory import (
    InventoryItemResponse,
    InventoryInLocationResponse,
    InventorySummaryResponse,
    InventoryInContainerResponse,
    LooseInventoryResponse,
    InventorySearchResult,
)
from app.infrastructure.database.repositories.inventory_repository import InventoryRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.container_repository import ContainerRepository
from app.core.exceptions import (
    InventoryNotFoundError,
    LocationNotFoundError,
    ContainerNotFoundError,
)


class InventoryService:
    """Сервис для работы с инвентарём"""

    def __init__(
        self,
        inventory_repository: InventoryRepository,
        location_repository: LocationRepository,
        container_repository: ContainerRepository,
    ):
        self.inventory_repo = inventory_repository
        self.location_repo = location_repository
        self.container_repo = container_repository

    async def get_inventory_by_product(self, product_id: str) -> List[InventoryItemResponse]:
        """
        Получить остатки товара по всем локациям

        Возвращает все записи остатков для указанного товара
        с разбивкой по локациям, партиям и контейнерам.
        """
        results = await self.inventory_repo.get_by_product(product_id)
        if not results:
            raise InventoryNotFoundError(f"Остатки товара '{product_id}' не найдены")
        return [InventoryItemResponse.model_validate(dict(r)) for r in results]

    async def get_inventory_by_location(self, location_id: int) -> List[InventoryInLocationResponse]:
        """
        Получить все остатки в локации

        Возвращает все товары в указанной локации.
        """
        # Проверка существования локации
        location = await self.location_repo.get_by_id(location_id)
        if not location:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        results = await self.inventory_repo.get_by_location(location_id)
        return [InventoryInLocationResponse.model_validate(dict(r)) for r in results]

    async def get_inventory_summary(
        self, category: Optional[str] = None
    ) -> List[InventorySummaryResponse]:
        """
        Получить агрегированные остатки

        Возвращает суммарные остатки по всем товарам
        с возможностью фильтрации по категории.
        """
        results = await self.inventory_repo.get_summary(category)
        return [InventorySummaryResponse.model_validate(dict(r)) for r in results]

    async def get_inventory_in_container(
        self, qr_code: str
    ) -> List[InventoryInContainerResponse]:
        """
        Получить остатки в контейнере

        Возвращает все товары в указанном контейнере.
        """
        # Проверка существования контейнера
        if not await self.container_repo.exists(qr_code):
            raise ContainerNotFoundError(f"Контейнер с QR-кодом '{qr_code}' не найден")

        results = await self.inventory_repo.get_in_container(qr_code)
        return [InventoryInContainerResponse.model_validate(dict(r)) for r in results]

    async def get_loose_inventory(self, location_id: int) -> List[LooseInventoryResponse]:
        """
        Получить россыпь в локации

        Возвращает товары без контейнера (россыпью) в указанной локации.
        """
        # Проверка существования локации
        location = await self.location_repo.get_by_id(location_id)
        if not location:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        results = await self.inventory_repo.get_loose(location_id)
        return [LooseInventoryResponse.model_validate(dict(r)) for r in results]

    async def search_inventory(self, query: str) -> List[InventorySearchResult]:
        """
        Поиск товара на складе

        Ищет по product_id, названию товара, номеру партии или коду контейнера.
        """
        if not query or len(query) < 2:
            raise ValueError("Поисковый запрос должен содержать минимум 2 символа")

        results = await self.inventory_repo.search(query)
        return [InventorySearchResult.model_validate(dict(r)) for r in results]
