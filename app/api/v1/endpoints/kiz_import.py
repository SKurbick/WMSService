"""API журнала сообщений и регистрации КИЗ поступлений."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.dependencies import get_kiz_import_service
from app.api.v1.openapi_kiz_import import (
    DETAIL_DOCUMENTATION,
    INTEGRITY_DOCUMENTATION,
    LIST_DOCUMENTATION,
    PROCESS_DOCUMENTATION,
)
from app.core.schemas.kiz_import import (
    KizImportIntegrityResponse,
    KizImportMessageDetail,
    KizImportMessageListItem,
    KizImportProcessResult,
)
from app.core.services.kiz_import_service import KizImportService
from app.shared.config import settings

router = APIRouter(prefix="/kiz-import", tags=["Импорт КИЗ"])


@router.get(
    "/integrity",
    response_model=KizImportIntegrityResponse,
    **INTEGRITY_DOCUMENTATION,
)
async def get_kiz_import_integrity(
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportIntegrityResponse:
    return await service.get_integrity()


@router.get(
    "/messages",
    response_model=list[KizImportMessageListItem],
    **LIST_DOCUMENTATION,
)
async def list_kiz_import_messages(
    limit: int = Query(
        100,
        ge=1,
        le=200,
        description="Количество сообщений на странице: от 1 до 200.",
        examples=[100],
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Количество пропускаемых сообщений, начиная с самых новых.",
        examples=[0],
    ),
    service: KizImportService = Depends(get_kiz_import_service),
) -> list[KizImportMessageListItem]:
    return await service.list_messages(limit, offset)


@router.post(
    "/messages/{message_id}/process",
    response_model=KizImportProcessResult,
    **PROCESS_DOCUMENTATION,
)
async def process_kiz_import_message(
    message_id: int = Path(
        ...,
        ge=1,
        description="Идентификатор сообщения из журнала импорта КИЗ; целое число от 1.",
        examples=[123],
    ),
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportProcessResult:
    return await service.process_message(
        message_id,
        settings.KIZ_IMPORT_RECEIPT_LOCATION_CODE,
    )


@router.get(
    "/messages/{message_id}",
    response_model=KizImportMessageDetail,
    **DETAIL_DOCUMENTATION,
)
async def get_kiz_import_message(
    message_id: int = Path(
        ...,
        ge=1,
        description="Идентификатор сохранённой доставки сообщения; целое число от 1.",
        examples=[123],
    ),
    service: KizImportService = Depends(get_kiz_import_service),
) -> KizImportMessageDetail:
    message = await service.get_message(message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"KIZ import message {message_id} не найдено",
        )
    return message
