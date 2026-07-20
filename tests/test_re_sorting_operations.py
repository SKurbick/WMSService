from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.api.v1.endpoints import re_sorting_operations as endpoint
from app.core.exceptions import ReSortingOperationConflictError, ReSortingOperationValidationError
from app.core.schemas.re_sorting_operations import ReSortingOperationCreate
from app.core.services.re_sorting_operation_service import ReSortingOperationService
from app.infrastructure.database.queries import re_sorting_operations as queries

VALID = {
    "from_product_id": "wild100",
    "to_product_id": "wild101",
    "quantity": 4,
    "location_code": "DIRECT",
    "reason": "wrong color",
    "author": "operator",
}


def test_request_trims_strings_and_requires_distinct_products():
    data = ReSortingOperationCreate(**{**VALID, "reason": " reason ", "author": " user "})
    assert data.reason == "reason" and data.author == "user"
    with pytest.raises(ReSortingOperationValidationError):
        ReSortingOperationCreate(**{**VALID, "to_product_id": "wild100"})


def test_quantity_is_strict_positive_integer():
    for value in (0, -1, 1.5, 4.0, "4"):
        with pytest.raises(ValidationError):
            ReSortingOperationCreate(**{**VALID, "quantity": value})


def test_routes_put_locations_before_dynamic_detail():
    paths = [r.path for r in endpoint.router.routes]
    assert "/re-sorting-operations" in paths
    assert "/re-sorting-operations/locations" in paths
    assert paths.index("/re-sorting-operations/locations") < paths.index(
        "/re-sorting-operations/{operation_id}"
    )


def test_sql_contract_and_locking():
    assert (
        "operation_code='re_sorting_operations'" in queries.GET_ACTIVE_OPERATION_LOCATION_FOR_SHARE
    )
    assert "scope='direct'" in queries.GET_ACTIVE_OPERATION_LOCATION_FOR_SHARE
    assert "FOR SHARE" in queries.GET_ACTIVE_OPERATION_LOCATION_FOR_SHARE
    assert "FOR UPDATE" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "batch_number IS NULL" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "container_code IS NULL" in queries.GET_LOOSE_INVENTORY_FOR_UPDATE
    assert "'re_sorting'" in queries.CREATE_MOVEMENT
    assert "'re_sorting_operation'" in queries.CREATE_MOVEMENT
    assert "stock_reservation" not in "\n".join(
        v for v in vars(queries).values() if isinstance(v, str)
    )


class FakeRepository:
    def __init__(self, loose=None, non_loose=0):
        self.loose = loose
        self.non_loose = non_loose

    async def get_loose_inventory_for_update(self, c, p, l):
        return None if self.loose is None else {"quantity": self.loose}

    async def get_non_loose_inventory_quantity(self, c, p, l):
        return self.non_loose


@pytest.mark.asyncio
async def test_non_loose_only_stock_is_conflict():
    service = ReSortingOperationService(FakeRepository(None, Decimal("10")))
    with pytest.raises(ReSortingOperationConflictError, match="партии/контейнере"):
        stock = await service.repository.get_loose_inventory_for_update(None, "p", 1)
        if not stock:
            non_loose = await service.repository.get_non_loose_inventory_quantity(None, "p", 1)
            if non_loose > 0:
                raise ReSortingOperationConflictError(
                    "Исходный остаток существует только в партии/контейнере"
                )


@pytest.mark.asyncio
async def test_advisory_key_is_canonical_source_target_pair():
    from app.infrastructure.database.repositories.re_sorting_operation_repository import (
        ReSortingOperationRepository,
    )

    class Connection:
        def __init__(self):
            self.keys = []

        async def execute(self, query, key):
            self.keys.append(key)

    connection = Connection()
    repository = ReSortingOperationRepository(pool=None)
    await repository.lock_operation_scope(connection, 55, "A", "B")
    await repository.lock_operation_scope(connection, 55, "B", "A")
    assert connection.keys == [
        "re_sorting_operations:55:A:B",
        "re_sorting_operations:55:A:B",
    ]


@pytest.mark.asyncio
async def test_re_sorting_error_response_has_russian_frontend_message():
    import json
    from fastapi import FastAPI, Request
    from app.middleware.error_handler import add_exception_handlers

    app = FastAPI()
    add_exception_handlers(app)
    handler = app.exception_handlers[ReSortingOperationConflictError]
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/re-sorting-operations",
            "query_string": b"",
            "headers": [],
        }
    )
    response = await handler(request, ReSortingOperationConflictError("Локация неактивна"))
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body == {
        "detail": "Локация неактивна",
        "message": "Локация неактивна",
        "error_code": "RE_SORTING_OPERATION_CONFLICT",
    }


def test_location_response_decodes_jsonb_metadata_string():
    from datetime import datetime, timezone

    service = ReSortingOperationService(FakeRepository())
    response = service._operation_location_response(
        {
            "operation_location_id": 17,
            "operation_code": "re_sorting_operations",
            "location_id": 305,
            "location_code": "PUSHKINO-ПЕРЕСОРТИЦА",
            "scope": "direct",
            "is_active": True,
            "author": "admin.wms",
            "metadata": '{"comment": "Зона ручной пересортицы склада Пушкино"}',
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
        location_name="Зона пересортицы",
    )
    assert response.metadata == {"comment": "Зона ручной пересортицы склада Пушкино"}
    assert response.location_name == "Зона пересортицы"


def test_operation_payload_decodes_jsonb_metadata_string():
    payload = {"metadata": '{"source": "manual"}'}
    assert ReSortingOperationService._decode_metadata(payload) == {"metadata": {"source": "manual"}}
