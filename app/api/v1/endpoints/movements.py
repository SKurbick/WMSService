"""API endpoints для движений товаров"""

from fastapi import APIRouter, Depends, status, Query, Path, Body
from typing import List, Optional
from datetime import date

from app.core.schemas.movement import (
    MovementCreate,
    MovementBulkCreateResponse,
    MovementResponse,
)
from app.core.services.movement_service import MovementService
from app.api.v1.dependencies import get_movement_service

router = APIRouter(prefix="/movements", tags=["Движения"])


MOVEMENT_TYPES_DESCRIPTION = """
Типы движения:

- `receive` — приход товара на склад. Обычно используется с `to_location_code`. Увеличивает остаток в локации-получателе.
- `putaway` — размещение товара после приемки. Обычно используется как перемещение между локациями: `from_location_code` + `to_location_code`.
- `transfer` — перемещение товара между локациями. Используйте `from_location_code` + `to_location_code`.
- `pick` — отбор товара. Обычно используется с `from_location_code`, если операция должна уменьшить остаток в локации отбора.
- `ship` — отгрузка/списание товара со склада. Используйте `from_location_code`. Уменьшает остаток.
- `unpack` — распаковка товара из контейнера в россыпь. Обычно создается специализированной контейнерной операцией, не рекомендуется фронту создавать вручную без отдельного сценария.
- `adjust` — ручная корректировка остатка: увеличение — `to_location_code` заполнен и `from_location_code = null`; уменьшение — `from_location_code` заполнен и `to_location_code = null`.
- `write_off` — списание по Python enum. Перед ручным использованием проверьте, что значение разрешено constraint целевой БД; для обычной отгрузки/списания используйте `ship`.
- `kit_assembly` — служебное движение комплектации. Создается через `POST /api/kit-operations`, фронту не нужно создавать вручную через `POST /api/movements`.
- `kit_disassembly` — служебное движение разукомплектации. Создается через `POST /api/kit-operations`, фронту не нужно создавать вручную через `POST /api/movements`.
"""

CREATE_MOVEMENTS_DESCRIPTION = """
Создает одно или несколько движений товара. Endpoint принимает массив движений и выполняет batch атомарно.

Остатки в `wms.inventory` не меняются напрямую. Остаток изменяется через создание movement:

- `to_location_code` увеличивает остаток;
- `from_location_code` уменьшает остаток;
- если заполнены обе стороны, происходит расход из `from_location_code` и приход в `to_location_code`.

Для ручной корректировки остатков используйте `movement_type="adjust"`:

- увеличение остатка: укажите `to_location_code` и не указывайте `from_location_code`;
- уменьшение остатка: укажите `from_location_code` и не указывайте `to_location_code`.

`quantity` всегда должен быть положительным числом. Не передавайте отрицательные `quantity` для списаний.

Важно для фронта:

1. Не меняйте `wms.inventory` напрямую.
2. Не передавайте отрицательный `quantity`. Для списания используйте `from_location_code`.
3. Для увеличения остатка через `adjust` используйте только `to_location_code`.
4. Для уменьшения остатка через `adjust` используйте только `from_location_code`.
5. Для `transfer` используйте обе стороны: `from_location_code` и `to_location_code`.
6. Для container stock передавайте `container_code` только если операция действительно относится к существующему контейнеру.
7. Для обычной россыпи `container_code` должен быть `null`.
8. `reason` желательно заполнять всегда, особенно для `adjust`.

""" + MOVEMENT_TYPES_DESCRIPTION

MOVEMENT_REQUEST_EXAMPLES = {
    "adjust_increase": {
        "summary": "Корректировка в плюс",
        "description": "Остаток товара wild1825 увеличится на 10 шт в локации RECEIVING-001.",
        "value": [
            {
                "movement_type": "adjust",
                "product_id": "wild1825",
                "from_location_code": None,
                "to_location_code": "RECEIVING-001",
                "quantity": 10,
                "batch_number": None,
                "container_code": None,
                "user_name": "admin",
                "reason": "Ручная корректировка: добавление 10 шт после пересчета",
            }
        ],
    },
    "adjust_decrease": {
        "summary": "Корректировка в минус",
        "description": "Остаток товара wild1825 уменьшится на 3 шт в локации RECEIVING-001.",
        "value": [
            {
                "movement_type": "adjust",
                "product_id": "wild1825",
                "from_location_code": "RECEIVING-001",
                "to_location_code": None,
                "quantity": 3,
                "batch_number": None,
                "container_code": None,
                "user_name": "admin",
                "reason": "Ручная корректировка: списание 3 шт после пересчета",
            }
        ],
    },
    "transfer": {
        "summary": "Перемещение между локациями",
        "description": "Из from_location_code спишется 5 шт, в to_location_code добавится 5 шт.",
        "value": [
            {
                "movement_type": "transfer",
                "product_id": "wild1825",
                "from_location_code": "RECEIVING-001",
                "to_location_code": "STORAGE-A-01",
                "quantity": 5,
                "batch_number": None,
                "container_code": None,
                "user_name": "operator",
                "reason": "Перемещение из зоны приемки в хранение",
            }
        ],
    },
    "ship": {
        "summary": "Отгрузка или списание",
        "description": "Остаток уменьшится в from_location_code.",
        "value": [
            {
                "movement_type": "ship",
                "product_id": "wild1825",
                "from_location_code": "FBS-001",
                "to_location_code": None,
                "quantity": 1,
                "batch_number": None,
                "container_code": None,
                "user_name": "operator",
                "reason": "Ручное списание/отгрузка",
            }
        ],
    },
    "receive": {
        "summary": "Приход",
        "description": "Остаток увеличится в to_location_code.",
        "value": [
            {
                "movement_type": "receive",
                "product_id": "wild1825",
                "from_location_code": None,
                "to_location_code": "RECEIVING-001",
                "quantity": 20,
                "batch_number": None,
                "container_code": None,
                "user_name": "operator",
                "reason": "Приход товара",
            }
        ],
    },
}


@router.post(
    "",
    response_model=MovementBulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать движения товаров / ручная корректировка остатков",
    description=CREATE_MOVEMENTS_DESCRIPTION,
)
async def create_movement(
    data: List[MovementCreate] = Body(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "Список movements для создания (1-500 элементов). Batch выполняется атомарно: "
            "если один movement не прошёл валидацию или insert, не создаётся ни один."
        ),
        openapi_examples=MOVEMENT_REQUEST_EXAMPLES,
    ),
    service: MovementService = Depends(get_movement_service),
):
    """
    Создать movements (батч операция)

    Регистрирует один или несколько movements атомарно.
    Все movements создаются в одной транзакции (всё или ничего).
    Триггер в БД автоматически обновляет inventory.

    **Параметры:**
    - **data**: Массив movements (минимум 1, максимум 500)

    Каждый movement содержит:
    - **movement_type**: Тип движения
    - **product_id**: ID товара
    - **from_location_code**: Код локации-источника (опционально)
    - **to_location_code**: Код локации-назначения (опционально)
    - **quantity**: Количество
    - **batch_number**: Номер партии (опционально)
    - **container_code**: Код контейнера (опционально)
    - **user_name**: Имя пользователя (опционально)
    - **reason**: Причина/комментарий (опционально)

    **Возвращает:**
    - **created**: Список созданных movements
    - **total**: Количество созданных movements

    **Атомарность:**
    - Если хотя бы один movement не прошёл валидацию → ошибка, ничего не создано
    - Если ошибка при создании любого movement → откат всех
    """
    return await service.create_movement(data)


@router.get(
    "",
    response_model=List[MovementResponse],
    summary="Получить историю движений",
    description="Возвращает журнал движений товаров с фильтрами по товару, контейнеру, типу движения и датам.",
)
async def get_movements(
    product_id: Optional[str] = Query(None, description="Фильтр по ID товара"),
    container_code: Optional[str] = Query(None, description="Фильтр по коду контейнера"),
    movement_type: Optional[str] = Query(None, description="Фильтр по типу движения"),
    from_date: Optional[date] = Query(None, description="Дата начала периода"),
    to_date: Optional[date] = Query(None, description="Дата окончания периода"),
    limit: int = Query(100, ge=1, le=1000, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    service: MovementService = Depends(get_movement_service),
):
    """
    Получить историю движений

    Возвращает историю движений с возможностью фильтрации.
    Включает batch_number для FIFO/FEFO.

    **Параметры:**
    - **product_id**: Фильтр по ID товара (опционально)
    - **container_code**: Фильтр по коду контейнера (опционально)
    - **movement_type**: Фильтр по типу движения (опционально)
    - **from_date**: Дата начала периода (опционально)
    - **to_date**: Дата окончания периода (опционально)
    - **limit**: Лимит записей (по умолчанию 100, максимум 1000)
    - **offset**: Смещение для пагинации

    **Возвращает:**
    - Список движений
    """
    return await service.get_movements(
        product_id=product_id,
        container_code=container_code,
        movement_type=movement_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/product/{product_id}",
    response_model=List[MovementResponse],
    summary="Получить движения товара",
    description="Возвращает последние движения по конкретному product_id.",
)
async def get_movements_by_product(
    product_id: str = Path(..., description="ID товара"),
    limit: int = Query(100, ge=1, le=1000, description="Лимит записей"),
    service: MovementService = Depends(get_movement_service),
):
    """
    Получить движения по товару

    Возвращает полную историю движений конкретного товара.

    **Параметры:**
    - **product_id**: ID товара
    - **limit**: Лимит записей (по умолчанию 100)

    **Возвращает:**
    - История движений товара
    """
    return await service.get_movements_by_product(product_id, limit)
