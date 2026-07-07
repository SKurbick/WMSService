"""API endpoints для инвентаря (остатков)"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Path
from typing import List, Optional

from app.core.schemas.inventory import (
    InventoryItemResponse,
    InventoryInLocationResponse,
    InventoryLocationSummaryResponse,
    InventorySummaryResponse,
    InventoryInContainerResponse,
    LooseInventoryResponse,
    InventorySearchResult,
)
from app.core.schemas.stock_reservation import (
    ProductAvailabilityResponse,
    ProductAvailabilityTotalsResponse,
    StockReservationEventResponse,
    StockReservationOrderResponse,
)
from app.core.services.inventory_service import InventoryService
from app.core.services.stock_reservation_service import StockReservationService
from app.api.v1.dependencies import get_inventory_service, get_stock_reservation_service

router = APIRouter(prefix="/inventory", tags=["Остатки"])


@router.get(
    "/availability",
    response_model=List[ProductAvailabilityResponse],
    summary="Получить доступность товаров",
    description="Возвращает физический остаток, мягкий резерв, свободный остаток и нехватку по товарам.",
)
async def list_product_availability(
    product_id: Optional[str] = Query(None, description="ID товара"),
    only_shortage: Optional[bool] = Query(None, description="Только товары с нехваткой"),
    only_reserved: Optional[bool] = Query(None, description="Только товары с активным резервом"),
    limit: int = Query(100, ge=1, le=5000, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить список доступности товаров с учетом мягких резервов."""
    return await service.list_product_availability(
        product_id=product_id,
        only_shortage=only_shortage,
        only_reserved=only_reserved,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/availability/totals",
    response_model=ProductAvailabilityTotalsResponse,
    summary="Получить итоги доступности",
    description="Возвращает агрегированные totals по физическим остаткам, резервам и нехватке.",
)
async def get_availability_totals(
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить агрегаты доступности по всем товарам."""
    return await service.get_availability_totals()


@router.get(
    "/product/{product_id}/availability",
    response_model=ProductAvailabilityResponse,
    summary="Получить доступность товара",
    description="Возвращает availability для одного product_id с учетом мягких резервов.",
)
async def get_product_availability(
    product_id: str = Path(..., description="ID товара"),
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить доступность товара с учетом мягкого резерва."""
    return await service.get_product_availability(product_id)


@router.get(
    "/reservations",
    response_model=List[StockReservationOrderResponse],
    summary="Получить мягкие резервы",
    description="Возвращает текущее состояние мягких резервов товаров с фильтрами.",
)
async def list_reservations(
    product_id: Optional[str] = Query(None, description="ID товара"),
    external_order_id: Optional[int] = Query(None, description="ID внешнего заказа"),
    is_reserved: Optional[bool] = Query(None, description="Активен ли резерв"),
    external_status: Optional[str] = Query(None, description="Внешний статус заказа"),
    source_type: Optional[str] = Query(None, description="Источник резерва"),
    older_than_hours: Optional[int] = Query(
        None, ge=0, description="Старше N часов по last_event_at"
    ),
    limit: int = Query(100, ge=1, le=500, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить текущие мягкие резервы с фильтрами."""
    return await service.list_reservations(
        product_id=product_id,
        external_order_id=external_order_id,
        is_reserved=is_reserved,
        external_status=external_status,
        source_type=source_type,
        older_than_hours=older_than_hours,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/reservation-events",
    response_model=List[StockReservationEventResponse],
    summary="Получить события резервов",
    description="Возвращает audit входящих событий мягких резервов и результатов их обработки.",
)
async def list_reservation_events(
    product_id: Optional[str] = Query(None, description="ID товара"),
    external_order_id: Optional[int] = Query(None, description="ID внешнего заказа"),
    external_status: Optional[str] = Query(None, description="Внешний статус заказа"),
    processing_result: Optional[str] = Query(None, description="Результат обработки"),
    source_type: Optional[str] = Query(None, description="Источник резерва"),
    date_from: Optional[datetime] = Query(None, description="Дата события от"),
    date_to: Optional[datetime] = Query(None, description="Дата события до"),
    limit: int = Query(100, ge=1, le=500, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить audit входящих событий резервов."""
    return await service.list_reservation_events(
        product_id=product_id,
        external_order_id=external_order_id,
        external_status=external_status,
        processing_result=processing_result,
        source_type=source_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/product/{product_id}",
    response_model=List[InventoryItemResponse],
    summary="Получить остатки товара",
    description="Возвращает остатки product_id по локациям, партиям, контейнерам и статусам.",
)
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


@router.get(
    "/location/{location_id}",
    response_model=List[InventoryInLocationResponse],
    summary="Получить остатки в локации",
    description="Возвращает все остатки, лежащие непосредственно в указанной WMS-локации.",
)
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


@router.get(
    "/location/{location_id}/availability",
    response_model=List[ProductAvailabilityResponse],
    summary="Получить доступность по subtree локации",
    description="Считает physical quantity внутри subtree локации и сопоставляет с глобальными мягкими резервами.",
)
async def get_location_subtree_availability(
    location_id: int = Path(..., description="ID локации"),
    service: StockReservationService = Depends(get_stock_reservation_service),
):
    """Получить доступность товаров по subtree локации с глобальным резервом."""
    return await service.get_location_subtree_availability(location_id)


@router.get(
    "/location/{location_id}/recursive-summary",
    response_model=List[InventoryLocationSummaryResponse],
    summary="Получить сводку остатков по subtree",
    description="Возвращает агрегированные остатки по локации и всем дочерним адресам.",
)
async def get_location_recursive_summary(
    location_id: int = Path(..., description="ID локации"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить сводку остатков в локации и дочерних локациях

    Возвращает агрегированные остатки по товарам для указанной локации
    и всех её дочерних локаций ниже по дереву.

    **Параметры:**
    - **location_id**: ID локации

    **Возвращает:**
    - Список агрегированных остатков по товарам
    """
    return await service.get_location_recursive_summary(location_id)


@router.get(
    "/location/by-code/{location_code}",
    response_model=List[InventoryInLocationResponse],
    summary="Получить остатки в локации по коду",
    description="Возвращает остатки WMS-локации, найденной по location_code.",
)
async def get_inventory_by_location_code(
    location_code: str = Path(..., description="Код локации"),
    service: InventoryService = Depends(get_inventory_service),
):
    """
    Получить остатки в локации по коду

    Возвращает все товары в указанной локации.

    **Параметры:**
    - **location_code**: Код локации (например, `PUSHKINO-A-01-S05-L02-B`)

    **Возвращает:**
    - Список товаров в локации
    """
    return await service.get_inventory_by_location_code(location_code)


@router.get(
    "/summary",
    response_model=List[InventorySummaryResponse],
    summary="Получить сводку остатков",
    description="Возвращает агрегированные остатки по всем товарам через представление склада.",
)
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


@router.get(
    "/container/{qr_code}",
    response_model=List[InventoryInContainerResponse],
    summary="Получить остатки в контейнере",
    description="Возвращает товары и партии, связанные с QR-кодом контейнера.",
)
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


@router.get(
    "/location/{location_id}/loose",
    response_model=List[LooseInventoryResponse],
    summary="Получить россыпь в локации",
    description="Возвращает только остатки без container_code в указанной WMS-локации.",
)
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


@router.get(
    "/search",
    response_model=List[InventorySearchResult],
    summary="Найти остатки на складе",
    description="Ищет остатки по product_id, названию товара, номеру партии или коду контейнера.",
)
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
