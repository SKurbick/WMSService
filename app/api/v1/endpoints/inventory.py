"""API endpoints для инвентаря (остатков)"""

from fastapi import APIRouter, Depends, Query, Path
from typing import List, Optional

from app.core.schemas.inventory import (
    InventoryItemResponse,
    InventoryInLocationResponse,
    InventorySummaryResponse,
    InventoryInContainerResponse,
    LooseInventoryResponse,
    InventorySearchResult,
)
from app.core.services.inventory_service import InventoryService
from app.api.v1.dependencies import get_inventory_service

router = APIRouter(prefix="/inventory", tags=["Остатки"])


@router.get("/product/{product_id}", response_model=List[InventoryItemResponse])
async def get_inventory_by_product(
    product_id: str = Path(..., description="ID товара"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить остатки товара

    Возвращает все остатки конкретного товара по локациям,
    с разбивкой по партиям и контейнерам.

    **Параметры:**
    - **product_id**: ID товара

    **Возвращает:**
    - Список остатков товара с детализацией
    """
    return await service.get_inventory_by_product(product_id)


@router.get("/location/{location_id}", response_model=List[InventoryInLocationResponse])
async def get_inventory_by_location(
    location_id: int = Path(..., description="ID локации"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить остатки в локации

    Возвращает все товары в указанной локации.

    **Параметры:**
    - **location_id**: ID локации

    **Возвращает:**
    - Список товаров в локации
    """
    return await service.get_inventory_by_location(location_id)


@router.get("/summary", response_model=List[InventorySummaryResponse])
async def get_inventory_summary(
    category: Optional[str] = Query(None, description="Фильтр по категории"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить агрегированные остатки

    Возвращает суммарные остатки по всем товарам
    с разбивкой на количество в контейнерах и россыпью.

    **Параметры:**
    - **category**: Фильтр по категории товаров (опционально)

    **Возвращает:**
    - Агрегированные остатки по товарам
    """
    return await service.get_inventory_summary(category)


@router.get("/container/{qr_code}", response_model=List[InventoryInContainerResponse])
async def get_inventory_in_container(
    qr_code: str = Path(..., description="QR-код контейнера"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить остатки в контейнере

    Возвращает все товары в указанном контейнере.

    **Параметры:**
    - **qr_code**: QR-код контейнера

    **Возвращает:**
    - Список товаров в контейнере
    """
    return await service.get_inventory_in_container(qr_code)


@router.get("/location/{location_id}/loose", response_model=List[LooseInventoryResponse])
async def get_loose_inventory(
    location_id: int = Path(..., description="ID локации"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить россыпь в локации

    Возвращает только товары россыпью (без контейнера) в указанной локации.

    **Параметры:**
    - **location_id**: ID локации

    **Возвращает:**
    - Список товаров россыпью
    """
    return await service.get_loose_inventory(location_id)


@router.get("/search", response_model=List[InventorySearchResult])
async def search_inventory(
    query: str = Query(..., min_length=2, description="Поисковый запрос"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Поиск товара на складе

    Ищет товар по product_id, названию, номеру партии или коду контейнера.
    Результаты отсортированы по релевантности.

    **Параметры:**
    - **query**: Поисковый запрос (минимум 2 символа)

    **Возвращает:**
    - Список найденных товаров с локациями (максимум 50 результатов)
    """
    return await service.search_inventory(query)
