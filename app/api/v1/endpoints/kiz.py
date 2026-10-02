"""Explicit physical KIZ operations."""

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import get_kiz_activation_service
from app.core.schemas.kiz_activation import (
    KizActivationRequest,
    KizActivationResponse,
)
from app.core.services.kiz_activation_service import KizActivationService


router = APIRouter(prefix="/kiz", tags=["KIZ"])


@router.post(
    "/activate",
    response_model=KizActivationResponse,
    summary="Активировать зарегистрированные КИЗ в loose-локации",
)
async def activate_kiz(
    request: KizActivationRequest,
    service: KizActivationService = Depends(get_kiz_activation_service),
) -> KizActivationResponse:
    return await service.activate(request)
