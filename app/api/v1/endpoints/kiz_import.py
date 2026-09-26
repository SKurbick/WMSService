"""API raw inbox и controlled B2 apply импорта КИЗ."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.dependencies import get_kiz_import_service
from app.core.schemas.kiz_import import (
    KizImportIntegrityResponse,
    KizImportMessageDetail,
    KizImportMessageListItem,
    KizImportProcessResult,
)
from app.core.services.kiz_import_service import KizImportService
from app.shared.config import settings

router = APIRouter(prefix="/kiz-import", tags=["KIZ Import"])


@router.get(
    "/integrity",
    response_model=KizImportIntegrityResponse,
    summary="Проверить receipt-origin инварианты KIZ import",
)
async def get_kiz_import_integrity(
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportIntegrityResponse:
    return await service.get_integrity()


@router.get(
    "/messages",
    response_model=list[KizImportMessageListItem],
    summary="Получить raw сообщения импорта КИЗ",
)
async def list_kiz_import_messages(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: KizImportService = Depends(get_kiz_import_service),
) -> list[KizImportMessageListItem]:
    return await service.list_messages(limit, offset)


@router.post(
    "/messages/{message_id}/process",
    response_model=KizImportProcessResult,
    summary="Применить сохранённое сообщение к существующему поступлению",
    description=(
        "Создаёт/связывает KIZ с уже существующими loose units receipt location. "
        "Не создаёт movements, inventory или receipt_items и не вызывается consumer-ом автоматически."
    ),
)
async def process_kiz_import_message(
    message_id: int = Path(..., ge=1),
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportProcessResult:
    return await service.process_message(
        message_id,
        settings.KIZ_IMPORT_RECEIPT_LOCATION_CODE,
    )


@router.get(
    "/messages/{message_id}",
    response_model=KizImportMessageDetail,
    summary="Получить raw сообщение импорта КИЗ",
)
async def get_kiz_import_message(
    message_id: int = Path(..., ge=1),
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportMessageDetail:
    message = await service.get_message(message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"KIZ import message {message_id} не найдено",
        )
    return message
