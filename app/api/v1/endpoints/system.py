"""API endpoints для системных операций"""

from fastapi import APIRouter, Depends, status
from typing import List
from app.api.v1.dependencies import get_kiz_service
from app.core.schemas.kiz import KizIntegrityViolation
from app.core.services.kiz_service import KizService
from app.api.v1.openapi_kiz import CONFLICT_RESPONSE, INTEGRITY_EXAMPLE, success

from app.core.schemas.system import (
    RecalculateInventoryRequest,
    RecalculateInventoryResponse,
    CreateSnapshotRequest,
    CreateSnapshotResponse,
    RefreshViewsResponse,
    IntegrityCheckResult,
    AuditSummaryResponse,
)
from app.core.services.system_service import SystemService
from app.api.v1.dependencies import get_system_service

router = APIRouter(prefix="/system", tags=["Системные"])


@router.post(
    "/validate-integrity",
    response_model=List[IntegrityCheckResult],
    summary="Проверить целостность остатков",
    description="Сравнивает текущие inventory с расчётом из wms.movements и возвращает расхождения.",
)
async def validate_integrity(
    service: SystemService = Depends(get_system_service),
):
    """
    Проверить целостность данных

    Сравнивает рассчитанные остатки из movements с текущими в inventory.
    Возвращает список расхождений. Пустой список = данные целостны.

    **Возвращает:**
    - Список расхождений между inventory и movements
    """
    return await service.validate_integrity()


@router.get(
    "/audit-summary",
    response_model=AuditSummaryResponse,
    summary="Получить сводку аудита данных",
    description="Проверки известных рисков качества данных только на чтение через агрегированные SELECT-запросы.",
)
async def get_audit_summary(
    service: SystemService = Depends(get_system_service),
):
    """
    Получить агрегированные проверки известных рисков качества данных.

    Endpoint read-only: выполняет только SELECT COUNT запросы,
    не пересчитывает inventory, не создает snapshot и не меняет данные.
    """
    return await service.get_audit_summary()


@router.post(
    "/recalculate-inventory",
    response_model=RecalculateInventoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Пересчитать остатки из движений",
    description=(
        "Восстанавливает available-остатки из полного журнала движений. product_id ограничивает "
        "пересчёт одним товаром; null — все товары. from_date должен быть null: частичный период запрещён. "
        "Обновляет рассчитанные строки и удаляет устаревшие в одной транзакции; остальные статусы не меняет. "
        "Операция берёт блокировки таблиц и может задерживать запись даже при фильтре одного товара. "
        "Если рассчитанного количества недостаточно для активных КИЗ — 409 и полный откат. "
        "При CONCURRENT_WRITE_CONFLICT повторите операцию целиком. Для проверки без записи "
        "используйте validate-integrity и kiz-integrity."
    ),
    responses={200: success({"inventory_records": 1, "total_units": "10.00", "products_count": 1}),
               409: CONFLICT_RESPONSE},
)
async def recalculate_inventory(
    data: RecalculateInventoryRequest = RecalculateInventoryRequest(),
    service: SystemService = Depends(get_system_service),
):
    """
    Пересчитать остатки

    **ВНИМАНИЕ:** Операция обновляет available inventory из movements через UPSERT,
    затем удаляет obsolete строки. Проверяет KIZ-инвариант до и после записи.

    Используйте для:
    - Исправления расхождений после сбоев
    - Восстановления данных из бэкапа
    - Проверки целостности Event Sourcing

    **Параметры:**
    - **product_id**: ID товара (опционально, если None - все товары)
    - **from_date**: временно запрещен; разрешен только полный пересчет available

    **Возвращает:**
    - Статистику пересчёта
    """
    return await service.recalculate_inventory(data)


@router.post(
    "/create-snapshot",
    response_model=CreateSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать снимок остатков",
    description="Сохраняет текущее состояние inventory в таблицу снимков.",
)
async def create_snapshot(
    data: CreateSnapshotRequest = CreateSnapshotRequest(),
    service: SystemService = Depends(get_system_service),
):
    """
    Создать снимок остатков

    Сохраняет текущее состояние inventory в таблицу snapshots.
    Обычно запускается по cron в конце дня для истории остатков.

    **Параметры:**
    - **snapshot_date**: Дата снимка (по умолчанию сегодня)

    **Возвращает:**
    - Статистику созданного снимка
    """
    return await service.create_snapshot(data)


@router.post(
    "/refresh-materialized-views",
    response_model=RefreshViewsResponse,
    summary="Обновить материализованные представления",
    description="Обновляет агрегированные materialized views, включая mv_product_stock.",
)
async def refresh_materialized_views(
    service: SystemService = Depends(get_system_service),
):
    """
    Обновить материализованные представления

    Обновляет mv_product_stock CONCURRENTLY (без блокировки чтения).
    Рекомендуется запускать периодически для актуализации агрегатов.

    **Возвращает:**
    - Статистику обновлённого представления
    """
    return await service.refresh_materialized_views()




@router.get(
    '/kiz-integrity', response_model=list[KizIntegrityViolation],
    summary="Проверить соответствие КИЗ физическим остаткам",
    description=("Только чтение. Возвращает нарушения holder shape и превышение active КИЗ "
                 "над exact available batch-less physical quantity для loose/container scopes, "
                 "а также container contents/location/link batch anomalies. Пустой массив означает "
                 "отсутствие нарушений; данные endpoint не исправляет."),
    responses={200: {"description": "Нарушения учёта КИЗ или пустой массив.", "content": {"application/json": {
        "examples": {"ok": {"summary": "Нарушений нет", "value": []},
                     "shortage": {"summary": "Четыре КИЗ при трёх единицах", "value": [INTEGRITY_EXAMPLE]}}
    }}}},
)
async def kiz_integrity(service: KizService = Depends(get_kiz_service)):
    """Проверка КИЗ на точных адресах без изменения данных."""
    return await service.integrity()
