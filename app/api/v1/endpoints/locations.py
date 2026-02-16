"""API endpoints для локаций"""

from fastapi import APIRouter, Depends, status, Query
from typing import List

from app.core.schemas.location import (
    LocationCreate,
    LocationUpdate,
    LocationResponse,
    LocationChildResponse,
    LocationDeactivateResponse,
    ZoneResponse,
    LocationTreeNode,
)
from app.core.services.location_service import LocationService
from app.api.v1.dependencies import get_location_service

router = APIRouter(prefix="/locations", tags=["Локации"])


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
        data: LocationCreate, service: LocationService = Depends(get_location_service)
):
    """
    Создать новую локацию
    
    Создаёт новую локацию в иерархии склада.
    location_code и path генерируются автоматически триггерами.
    
    **Параметры:**
    - **parent_location_id**: ID родительской локации (опционально)
    - **name**: Название локации (например: "Стеллаж 01")
    - **zone_type**: Тип зоны (receiving, storage, picking, packing, shipping, quarantine)
    - **level**: Уровень в иерархии (1-5)
    - **max_weight**: Максимальный вес (кг)
    - **max_volume**: Максимальный объём (м³)
    - **is_active**: Активна ли локация
    - **is_pickable**: Можно ли комплектовать из этой локации
    - **metadata**: Дополнительные данные в формате JSON
    
    **Возвращает:**
    - Созданную локацию с автосгенерированным location_code и path
    """
    return await service.create_location(data)


@router.get("/zones", response_model=List[ZoneResponse])
async def get_zones(
        service: LocationService = Depends(get_location_service)
):
    """
    Получить список зон склада

    Возвращает все активные зоны (level = 1) с информацией о складе-родителе.

    **Возвращает:**
    - Список активных зон с кодом и названием склада
    """
    return await service.get_zones()


@router.get("/zones/tree", response_model=List[LocationTreeNode])
async def get_zones_tree(
        max_level: int = Query(5, ge=0, le=5, description="Максимальный уровень вложенности (0-5)"),
        service: LocationService = Depends(get_location_service)
):
    """
    Получить иерархическое дерево зон

    Возвращает вложенную структуру локаций с ограничением по уровню.

    **Параметры:**
    - **max_level**:
      - 0 - только склады
      - 1 - склады + зоны
      - 2 - склады + зоны + стеллажи
      - 3 - + секции
      - 4 - + ярусы
      - 5 - полная иерархия до ячеек

    **Возвращает:**
    - Дерево локаций с вложенными children
    """
    return await service.get_zones_tree(max_level)


@router.get("/{location_id}", response_model=LocationResponse)
async def get_location(
        location_id: int, service: LocationService = Depends(get_location_service)
):
    """
    Получить локацию по ID

    Возвращает детальную информацию о локации: название, код, путь,
    родителя, тип зоны, вместимость.

    **Параметры:**
    - **location_id**: ID локации

    **Возвращает:**
    - Полную информацию о локации
    """
    return await service.get_location_by_id(location_id)


@router.get("/by-code/{location_code}", response_model=LocationResponse)
async def get_location_by_code(
        location_code: str, service: LocationService = Depends(get_location_service)
):
    """
    Получить локацию по коду
    
    Возвращает локацию по человекочитаемому коду (например: PUSHKINO-A-01-S05-L02-B).
    
    **Параметры:**
    - **location_code**: Код локации
    
    **Возвращает:**
    - Информацию о локации
    """
    return await service.get_location_by_code(location_code)


@router.get("/{location_id}/children", response_model=List[LocationChildResponse])
async def get_location_children(
        location_id: int,
        recursive: bool = Query(
            True, description="Включить все уровни вложенности (рекурсивно через LTREE)"
        ),
        service: LocationService = Depends(get_location_service),
):
    """
    Получить дочерние локации
    
    Возвращает все дочерние локации (например, для стеллажа вернёт все секции,
    ярусы и ячейки).
    
    **Параметры:**
    - **location_id**: ID родительской локации
    - **recursive**: Если True - все потомки (через LTREE), если False - только прямые дети
    
    **Возвращает:**
    - Список дочерних локаций с указанием глубины вложенности
    """
    return await service.get_children(location_id, recursive)


@router.put("/{location_id}", response_model=LocationResponse)
async def update_location(
        location_id: int,
        data: LocationUpdate,
        service: LocationService = Depends(get_location_service),
):
    """
    Обновить локацию
    
    Обновляет параметры локации. Можно обновить только часть полей.
    
    **Параметры:**
    - **location_id**: ID локации
    - **name**: Новое название (опционально)
    - **zone_type**: Новый тип зоны (опционально)
    - **max_weight**: Новый максимальный вес (опционально)
    - **max_volume**: Новый максимальный объём (опционально)
    - **is_active**: Новый статус активности (опционально)
    - **is_pickable**: Можно ли комплектовать (опционально)
    - **metadata**: Новые метаданные (опционально)
    
    **Возвращает:**
    - Обновлённую локацию
    """
    return await service.update_location(location_id, data)


@router.patch("/{location_id}/deactivate", response_model=LocationDeactivateResponse)
async def deactivate_location(
        location_id: int, service: LocationService = Depends(get_location_service)
):
    """
    Деактивировать локацию
    
    Помечает локацию как неактивную (is_active = false).
    Нельзя размещать товары в неактивной локации.
    
    **Параметры:**
    - **location_id**: ID локации
    
    **Возвращает:**
    - Информацию о деактивированной локации
    """
    return await service.deactivate_location(location_id)


@router.get("/find-available", response_model=dict)
async def find_available_location(
        product_id: str = Query(..., description="ID товара"),
        quantity: int = Query(..., ge=1, description="Количество товара"),
        zone_type: str = Query("storage", description="Тип зоны для поиска"),
        service: LocationService = Depends(get_location_service),
):
    """
    Найти свободную ячейку
    
    Находит оптимальную свободную ячейку для размещения товара с учётом
    веса, объёма и текущей загруженности.
    
    Использует PostgreSQL функцию wms.find_available_location().
    
    **Параметры:**
    - **product_id**: ID товара
    - **quantity**: Количество товара
    - **zone_type**: Тип зоны (по умолчанию: storage)
    
    **Возвращает:**
    - Информацию о найденной свободной ячейке
    """
    return await service.find_available_location(product_id, quantity, zone_type)
