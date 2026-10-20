"""Explicit idempotent operations for ordinary containers."""

from fastapi import APIRouter, Body, Depends, status

from app.api.v1.dependencies import (
    get_container_extract_service,
    get_container_fill_service,
    get_container_move_service,
    get_container_unpack_all_service,
)
from app.core.schemas.container_operations import (
    ContainerExtractRequest,
    ContainerExtractResponse,
    ContainerFillRequest,
    ContainerFillResponse,
    ContainerMoveRequest,
    ContainerMoveResponse,
    ContainerUnpackAllRequest,
    ContainerUnpackAllResponse,
)
from app.core.services.container_extract_service import ContainerExtractService
from app.core.services.container_fill_service import ContainerFillService
from app.core.services.container_move_service import ContainerMoveService
from app.core.services.container_unpack_all_service import ContainerUnpackAllService

router = APIRouter(prefix="/container-operations", tags=["Контейнеры"])

FILL_REQUEST_EXAMPLES = {
    "fill": {
        "summary": "Поместить учтённую россыпь в пустой или открытый контейнер",
        "value": {
            "source_system": "manual",
            "external_operation_id": "fill-001",
            "author": "operator",
            "container_id": 123,
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "wild123",
                    "quantity": "4.00",
                    "batch_number": None,
                }
            ],
        },
    }
}

FILL_RESPONSE_EXAMPLE = {
    "operation_id": 1,
    "operation_type": "fill",
    "source_system": "manual",
    "external_operation_id": "fill-001",
    "container_id": 123,
    "container_qr_code": "C-001",
    "items": [
        {
            "external_line_id": "1",
            "product_id": "wild123",
            "batch_number": None,
            "quantity": "4.00",
            "movement_refs": [16001, 16002],
        }
    ],
}


@router.post(
    "/fill",
    response_model=ContainerFillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Поместить россыпь в контейнер",
    description=(
        "Атомарно переносит available loose stock в flat container на той же "
        "локации. Не является приёмкой и не изменяет суммарное physical quantity."
    ),
    responses={201: {"content": {"application/json": {"example": FILL_RESPONSE_EXAMPLE}}}},
)
async def fill_container(
    data: ContainerFillRequest = Body(..., openapi_examples=FILL_REQUEST_EXAMPLES),
    service: ContainerFillService = Depends(get_container_fill_service),
):
    return await service.fill(data)


EXTRACT_REQUEST_EXAMPLES = {
    "extract": {
        "summary": "Извлечь товар из открытого контейнера в россыпь",
        "value": {
            "source_system": "manual",
            "external_operation_id": "extract-001",
            "author": "operator",
            "container_id": 123,
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "wild123",
                    "quantity": "2.00",
                    "batch_number": None,
                }
            ],
        },
    }
}

EXTRACT_RESPONSE_EXAMPLE = {
    "operation_id": 2,
    "operation_type": "extract",
    "source_system": "manual",
    "external_operation_id": "extract-001",
    "container_id": 123,
    "container_qr_code": "C-001",
    "container_status": "open",
    "items": [
        {
            "external_line_id": "1",
            "product_id": "wild123",
            "batch_number": None,
            "quantity": "2.00",
            "movement_refs": [16010, 16011],
        }
    ],
}


@router.post(
    "/extract",
    response_model=ContainerExtractResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Извлечь товар из контейнера",
    description=(
        "Атомарно переносит available stock из open flat container в россыпь "
        "на direct location контейнера без изменения суммарного physical quantity."
    ),
    responses={201: {"content": {"application/json": {"example": EXTRACT_RESPONSE_EXAMPLE}}}},
)
async def extract_from_container(
    data: ContainerExtractRequest = Body(..., openapi_examples=EXTRACT_REQUEST_EXAMPLES),
    service: ContainerExtractService = Depends(get_container_extract_service),
):
    return await service.extract(data)


MOVE_REQUEST_EXAMPLES = {
    "move": {
        "summary": "Переместить контейнер внутри одного склада",
        "value": {
            "source_system": "manual",
            "external_operation_id": "container-move-001",
            "author": "operator",
            "container_id": 123,
            "to_location_code": "B-01",
        },
    }
}

MOVE_RESPONSE_EXAMPLE = {
    "operation_id": 3,
    "operation_type": "move",
    "source_system": "manual",
    "external_operation_id": "container-move-001",
    "container_id": 123,
    "container_qr_code": "C-001",
    "container_status": "open",
    "from_location_code": "A-01",
    "to_location_code": "B-01",
    "items": [
        {
            "product_id": "wild123",
            "batch_number": None,
            "quantity": "2.00",
            "movement_refs": [16020],
        }
    ],
}


@router.post(
    "/move",
    response_model=ContainerMoveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Переместить контейнер",
    description=(
        "Атомарно перемещает flat container и весь его contained stock между "
        "локациями одного warehouse. Empty container перемещается без fake movements."
    ),
    responses={201: {"content": {"application/json": {"example": MOVE_RESPONSE_EXAMPLE}}}},
)
async def move_container(
    data: ContainerMoveRequest = Body(..., openapi_examples=MOVE_REQUEST_EXAMPLES),
    service: ContainerMoveService = Depends(get_container_move_service),
):
    return await service.move(data)


UNPACK_ALL_REQUEST_EXAMPLES = {
    "unpack_all": {
        "summary": "Полностью распаковать открытый контейнер",
        "value": {
            "source_system": "manual",
            "external_operation_id": "unpack-all-001",
            "author": "operator",
            "container_id": 123,
        },
    }
}

UNPACK_ALL_RESPONSE_EXAMPLE = {
    "operation_id": 4,
    "operation_type": "unpack_all",
    "source_system": "manual",
    "external_operation_id": "unpack-all-001",
    "container_id": 123,
    "container_qr_code": "C-001",
    "container_status": "empty",
    "location_code": "B-01",
    "items": [
        {
            "product_id": "wild123",
            "batch_number": None,
            "quantity": "2.00",
            "movement_refs": [16021, 16022],
        }
    ],
}


@router.post(
    "/unpack-all",
    response_model=ContainerUnpackAllResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Полностью распаковать контейнер",
    description=(
        "Атомарно переносит все active contents открытого flat container в loose "
        "stock на его direct location и переводит контейнер в empty."
    ),
    responses={201: {"content": {"application/json": {"example": UNPACK_ALL_RESPONSE_EXAMPLE}}}},
)
async def unpack_all_container(
    data: ContainerUnpackAllRequest = Body(..., openapi_examples=UNPACK_ALL_REQUEST_EXAMPLES),
    service: ContainerUnpackAllService = Depends(get_container_unpack_all_service),
):
    return await service.unpack_all(data)
