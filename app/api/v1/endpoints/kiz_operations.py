"""API атомарных перемещений и отгрузок с явно выбранными КИЗ."""

from fastapi import APIRouter, Body, Depends

from app.api.v1.dependencies import get_kiz_ship_service, get_kiz_transfer_service
from app.api.v1.openapi_kiz import (
    OPERATION_CONFLICT_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
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
    "/transfer",
    response_model=KizTransferResponse,
    status_code=201,
    summary="Явно переместить товар и выбранные КИЗ",
    description=(
        "Атомарно переносит доступную россыпь между разными точными адресами без партии и "
        "контейнера. Товар и адреса должны существовать; дочерние адреса не учитываются. "
        "Каждый выбранный КИЗ должен быть active, принадлежать товару и находиться "
        "непосредственно на исходном адресе. КИЗ внутри контейнера этим запросом не перемещается.\n\n"
        "quantity — полное целое количество строки, включая выбранные КИЗ. Разница между "
        "quantity и числом kiz_codes расходуется из неидентифицированного остатка. "
        "Пустой kiz_codes допустим: тогда весь расход неидентифицированный. КИЗ автоматически "
        "не выбираются. Например, quantity=5 и два КИЗ перемещают две идентифицированные "
        "и три неидентифицированные единицы.\n\n"
        "На каждую строку создаётся movement с movement_ref, выбранным КИЗ назначается "
        "целевой адрес, состояние active сохраняется. Любая ошибка откатывает все строки, "
        "движения и изменения КИЗ. Один КИЗ нельзя указать дважды, external_line_id должны "
        "быть уникальны.\n\n"
        "Ключ повтора: source_system + тип операции transfer + external_operation_id. "
        "Те же строки и КИЗ с тем же ключом возвращают сохранённый результат без новых "
        "движений; порядок строк и кодов не важен. Изменение товара, адресов, количества, "
        "кодов или external_line_id с тем же ключом даёт 409 KIZ_IDEMPOTENCY_CONFLICT. "
        "Автор записывается при первом выполнении и не меняет физический смысл повтора."
    ),
    responses={
        201: {
            "description": "Перемещение выполнено либо возвращён сохранённый результат повторной команды.",
            "content": {"application/json": {"example": TRANSFER_RESPONSE_EXAMPLE}},
        },
        404: {
            "description": "Товар или локация не найдены: PRODUCT_NOT_FOUND или LOCATION_NOT_FOUND. Неизвестный выбранный КИЗ вызывает 409."
        },
        409: OPERATION_CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def transfer(
    data: KizTransferRequest = Body(
        ...,
        description="Команда перемещения с ключом повтора и непустым массивом строк.",
        openapi_examples=TRANSFER_REQUEST_EXAMPLES,
    ),
    service: KizTransferService = Depends(get_kiz_transfer_service),
):
    return await service.transfer(data)


@router.post(
    "/ship",
    response_model=KizShipResponse,
    status_code=201,
    summary="Явно отгрузить товар и выбранные КИЗ",
    description=(
        "Атомарно списывает доступную россыпь с точных исходных адресов без партии и "
        "контейнера. Товары и адреса должны существовать. Выбранные КИЗ должны быть active, "
        "принадлежать указанному товару и находиться непосредственно на исходном адресе.\n\n"
        "quantity — полное целое количество, включая явно выбранные kiz_codes. "
        "Разница списывается из неидентифицированного остатка; пустой массив кодов разрешён. "
        "Автоматического выбора КИЗ нет. КИЗ в контейнере этим запросом не отгружаются.\n\n"
        "На строку создаётся расходное движение ship с movement_ref. Выбранные КИЗ переходят "
        "в shipped, их location_id/container_id очищаются, сохраняются ссылки на движения "
        "и события shipped. Остальные КИЗ остаются на месте. Ошибка любой строки откатывает "
        "всю команду, включая физический расход и события.\n\n"
        "Ключ повтора: source_system + тип операции ship + external_operation_id. "
        "Те же строки с тем же ключом возвращают сохранённый результат без повторного "
        "списания; порядок строк и кодов не важен. Изменение товара, адресов, количества, "
        "кодов или external_line_id при том же ключе вызывает 409 KIZ_IDEMPOTENCY_CONFLICT. "
        "Изменение автора само по себе не создаёт новую физическую операцию."
    ),
    responses={
        201: {
            "description": "Отгрузка выполнена либо возвращён сохранённый результат повторной команды.",
            "content": {"application/json": {"example": SHIP_RESPONSE_EXAMPLE}},
        },
        404: {
            "description": "Товар или локация не найдены: PRODUCT_NOT_FOUND или LOCATION_NOT_FOUND. Неизвестный выбранный КИЗ вызывает 409."
        },
        409: OPERATION_CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def ship(
    data: KizShipRequest = Body(
        ...,
        description="Команда отгрузки с ключом повтора и непустым массивом строк.",
        openapi_examples=SHIP_REQUEST_EXAMPLES,
    ),
    service: KizShipService = Depends(get_kiz_ship_service),
):
    return await service.ship(data)
