"""Read-only unified history of one KIZ."""

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_kiz_history_service
from app.api.v1.openapi_kiz_history import OPERATION
from app.core.schemas.kiz_history import KizHistory
from app.core.services.kiz_history_service import KizHistoryService

router = APIRouter(prefix="/kiz-history", tags=["КИЗ"])


@router.get("", response_model=KizHistory, **OPERATION)
async def get_kiz_history(
    kiz_code: str = Query(
        ...,
        min_length=1,
        description="Полный регистрозависимый код КИЗ; безопасно передаётся query-параметром.",
        examples=["KIZ-001"],
    ),
    service: KizHistoryService = Depends(get_kiz_history_service),
):
    return await service.get(kiz_code)
