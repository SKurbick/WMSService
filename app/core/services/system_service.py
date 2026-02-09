"""Сервис для системных операций (бизнес-логика)"""

from typing import List, Optional
from datetime import date
from app.core.schemas.system import (
    RecalculateInventoryRequest,
    RecalculateInventoryResponse,
    CreateSnapshotRequest,
    CreateSnapshotResponse,
    RefreshViewsResponse,
    IntegrityCheckResult,
)
from app.infrastructure.database.repositories.system_repository import SystemRepository


class SystemService:
    """Сервис для системных операций"""

    def __init__(self, system_repository: SystemRepository):
        self.system_repo = system_repository

    async def validate_integrity(self) -> List[IntegrityCheckResult]:
        """
        Проверить целостность данных

        Сравнивает рассчитанные остатки из movements с текущими в inventory.
        Возвращает список расхождений (если есть).
        """
        results = await self.system_repo.validate_integrity()
        return [IntegrityCheckResult.model_validate(dict(r)) for r in results]

    async def recalculate_inventory(
        self, data: RecalculateInventoryRequest
    ) -> RecalculateInventoryResponse:
        """
        Пересчитать остатки из movements

        ВНИМАНИЕ: Эта операция удаляет текущие записи inventory
        и пересчитывает их заново из событий movements.

        Используйте для исправления расхождений или после восстановления БД.
        """
        result = await self.system_repo.recalculate_inventory(
            product_id=data.product_id,
            from_date=data.from_date,
        )
        return RecalculateInventoryResponse.model_validate(dict(result))

    async def create_snapshot(
        self, data: CreateSnapshotRequest
    ) -> CreateSnapshotResponse:
        """
        Создать снимок остатков

        Сохраняет текущее состояние inventory в таблицу snapshots.
        Обычно запускается по расписанию (cron) в конце дня.
        """
        result = await self.system_repo.create_snapshot(data.snapshot_date)
        return CreateSnapshotResponse.model_validate(dict(result))

    async def refresh_materialized_views(self) -> RefreshViewsResponse:
        """
        Обновить материализованные представления

        Обновляет mv_product_stock CONCURRENTLY (без блокировки чтения).
        Рекомендуется запускать периодически для актуализации агрегатов.
        """
        result = await self.system_repo.refresh_materialized_views()
        return RefreshViewsResponse.model_validate(dict(result))
