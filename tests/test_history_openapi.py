from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.api.v1.openapi_history import (
    DAILY_EXAMPLE,
    DETAIL_EXAMPLES,
    OPERATIONS_EXAMPLES,
    RECEIPT_EXAMPLES,
)
from app.core.schemas.inventory_history import DailyBalancesResponse
from app.core.schemas.operations_history import OperationsHistoryResponse
from app.core.schemas.operations_history_detail import OperationDetailResponse
from app.core.schemas.receipt_history import ReceiptHistoryResponse
from app.main import app


PATHS = {
    "/api/inventory-history/daily-balances": "get_inventory_daily_balances",
    "/api/operations-history": "list_operations_history",
    "/api/operations-history/{event_id}": "get_operation_history_detail",
    "/api/receipts/{guid}/history": "get_receipt_history",
}


def test_openapi_http_and_history_routes_are_fully_documented():
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operation_ids = []
    for path, expected_id in PATHS.items():
        operation = schema["paths"][path]["get"]
        assert operation["operationId"] == expected_id
        assert operation["summary"]
        assert operation["description"]
        assert operation["tags"] == ["История WMS"]
        assert {"200", "400", "404", "422", "500"} <= operation["responses"].keys()
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert all(parameter.get("description") for parameter in operation["parameters"])
        operation_ids.append(operation["operationId"])
    assert len(operation_ids) == len(set(operation_ids))


def test_detail_is_discriminated_one_of_and_has_all_event_id_examples():
    schema = app.openapi()
    operation = schema["paths"]["/api/operations-history/{event_id}"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert len(response_schema["oneOf"]) == 4
    assert response_schema["discriminator"]["propertyName"] == "source_type"
    parameter = next(item for item in operation["parameters"] if item["name"] == "event_id")
    examples = parameter["schema"]["examples"]
    assert examples == [
        "kit_operation:42",
        "re_sorting:7",
        "fbs_shipment:156",
        "movement:29530:1784198400451000",
    ]


def test_descriptions_expose_required_business_rules():
    schema = app.openapi()
    daily = schema["paths"]["/api/inventory-history/daily-balances"]["get"]["description"]
    operations = schema["paths"]["/api/operations-history"]["get"]
    receipt = schema["paths"]["/api/receipts/{guid}/history"]["get"]
    assert "Europe/Moscow" in daily and "closing_quantity" in daily
    assert all(
        value in operations["parameters"][2]["description"]
        for value in ("kit_operation", "re_sorting_operation", "fbs_shipment", "movement")
    )
    assert "не обязательно UUID" in receipt["description"]


def test_all_success_examples_validate_with_runtime_response_models():
    DailyBalancesResponse.model_validate(DAILY_EXAMPLE)
    for example in OPERATIONS_EXAMPLES.values():
        OperationsHistoryResponse.model_validate(example["value"])
    adapter = TypeAdapter(OperationDetailResponse)
    for example in DETAIL_EXAMPLES.values():
        adapter.validate_python(example["value"])
    for example in RECEIPT_EXAMPLES.values():
        ReceiptHistoryResponse.model_validate(example["value"])


def test_docs_ui_is_available_without_starting_lifespan():
    response = TestClient(app).get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text
