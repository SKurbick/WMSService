"""Explicit idempotent KIZ-aware physical operations."""

from fastapi import APIRouter, Body, Depends

from app.api.v1.dependencies import get_kiz_ship_service, get_kiz_transfer_service
from app.core.schemas.kiz_operations import (
    KizShipRequest,
    KizShipResponse,
    KizTransferRequest,
    KizTransferResponse,
)
from app.core.services.kiz_ship_service import KizShipService
from app.core.services.kiz_transfer_service import KizTransferService

router = APIRouter(prefix="/kiz-operations", tags=["КИЗ"])

TRANSFER_REQUEST_EXAMPLES = {
    "with_kiz": {
        "summary": "Смешанное перемещение с выбранными КИЗ",
        "description": (
            "Перемещает две КИЗ-идентифицированные единицы и три единицы, "
            "не идентифицированные по КИЗ. КИЗ должны быть заранее назначены "
            "товару testwild на исходной локации."
        ),
        "value": {
            "source_system": "manual",
            "external_operation_id": "transfer-testwild-20260911-001",
            "author": "operator",
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "testwild",
                    "from_location_code": "KIZTEST-SOURCE",
                    "to_location_code": "KIZTEST-DESTINATION",
                    "quantity": 5,
                    "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"],
                }
            ],
        },
    },
    "without_kiz": {
        "summary": "Перемещение неидентифицированного по КИЗ остатка",
        "description": "Перемещает три единицы testwild без выбора конкретных КИЗ.",
        "value": {
            "source_system": "manual",
            "external_operation_id": "transfer-testwild-20260911-002",
            "author": "operator",
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "testwild",
                    "from_location_code": "KIZTEST-SOURCE",
                    "to_location_code": "KIZTEST-DESTINATION",
                    "quantity": 3,
                    "kiz_codes": [],
                }
            ],
        },
    },
}

TRANSFER_RESPONSE_EXAMPLE = {
    "operation_id": 15001,
    "operation_type": "transfer",
    "source_system": "manual",
    "external_operation_id": "transfer-testwild-20260911-001",
    "items": [
        {
            "external_line_id": "1",
            "product_id": "testwild",
            "from_location_code": "KIZTEST-SOURCE",
            "to_location_code": "KIZTEST-DESTINATION",
            "quantity": 5,
            "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"],
            "movement_ref": 16001,
        }
    ],
}

SHIP_REQUEST_EXAMPLES = {
    "with_kiz": {
        "summary": "Смешанная отгрузка с выбранными КИЗ",
        "description": (
            "Списывает две КИЗ-идентифицированные единицы и три единицы, "
            "не идентифицированные по КИЗ. КИЗ должны быть active и заранее "
            "назначены товару testwild на исходной локации."
        ),
        "value": {
            "source_system": "manual",
            "external_operation_id": "ship-testwild-20260911-001",
            "author": "operator",
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "testwild",
                    "from_location_code": "KIZTEST-SOURCE",
                    "quantity": 5,
                    "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"],
                }
            ],
        },
    },
    "without_kiz": {
        "summary": "Отгрузка неидентифицированного по КИЗ остатка",
        "description": "Списывает три единицы testwild без выбора конкретных КИЗ.",
        "value": {
            "source_system": "manual",
            "external_operation_id": "ship-testwild-20260911-002",
            "author": "operator",
            "items": [
                {
                    "external_line_id": "1",
                    "product_id": "testwild",
                    "from_location_code": "KIZTEST-SOURCE",
                    "quantity": 3,
                    "kiz_codes": [],
                }
            ],
        },
    },
}

SHIP_RESPONSE_EXAMPLE = {
    "operation_id": 2,
    "operation_type": "ship",
    "source_system": "manual",
    "external_operation_id": "ship-testwild-20260911-001",
    "items": [
        {
            "external_line_id": "1",
            "product_id": "testwild",
            "from_location_code": "KIZTEST-SOURCE",
            "quantity": 5,
            "kiz_codes": ["KIZ-TEST-001", "KIZ-TEST-002"],
            "movement_ref": 16002,
        }
    ],
}


@router.post(
    "/transfer", response_model=KizTransferResponse, status_code=201,
    summary="Явно переместить товар и выбранные КИЗ",
    description=(
        "Атомарно переносит доступный россыпной остаток между точными адресами, "
        "перемещает явно перечисленные active КИЗ и поддерживает точный replay. "
        "Для выполнения примера заранее создайте physical stock testwild на исходном "
        "адресе и назначьте перечисленные КИЗ через /api/kiz/assign."
    ),
    responses={
        201: {
            "description": "Transfer выполнен либо возвращён сохранённый exact replay.",
            "content": {"application/json": {"example": TRANSFER_RESPONSE_EXAMPLE}},
        }
    },
)
async def transfer(
    data: KizTransferRequest = Body(
        ...,
        description="Идемпотентная команда explicit KIZ transfer.",
        openapi_examples=TRANSFER_REQUEST_EXAMPLES,
    ),
    service: KizTransferService = Depends(get_kiz_transfer_service),
):
    return await service.transfer(data)


@router.post(
    "/ship", response_model=KizShipResponse, status_code=201,
    summary="Явно отгрузить товар и выбранные КИЗ",
    description=(
        "Атомарно списывает доступный россыпной остаток с точного адреса, "
        "переводит явно перечисленные active КИЗ в shipped и поддерживает точный replay. "
        "Для выполнения примера заранее создайте physical stock testwild на исходном "
        "адресе и назначьте перечисленные КИЗ через /api/kiz/assign."
    ),
    responses={
        201: {
            "description": "Shipment выполнен либо возвращён сохранённый exact replay.",
            "content": {"application/json": {"example": SHIP_RESPONSE_EXAMPLE}},
        }
    },
)
async def ship(
    data: KizShipRequest = Body(
        ...,
        description="Идемпотентная команда explicit KIZ shipment.",
        openapi_examples=SHIP_REQUEST_EXAMPLES,
    ),
    service: KizShipService = Depends(get_kiz_ship_service),
):
    return await service.ship(data)
