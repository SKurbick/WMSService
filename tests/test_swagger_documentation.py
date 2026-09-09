"""Контракт документации: русские описания и примеры, пригодные для реальных схем."""
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.api.v1.openapi_kiz import ASSIGNMENT, TERMINAL, INTEGRITY_EXAMPLE
from app.core.schemas.kiz import (
    KizAssignment,
    KizAssignmentResult,
    KizState,
    KizStockSummary,
    KizPage,
    KizEventPage,
    KizTerminalRequest,
    KizIntegrityViolation,
)
from app.core.schemas.movement import MovementBulkCreateResponse, MovementCreate
from app.core.schemas.system import RecalculateInventoryResponse, RecalculateInventoryRequest
from app.main import app


@pytest.mark.parametrize(
    "path,method,code,model",
    [
        ("/api/kiz/assign", "post", "201", KizAssignmentResult),
        ("/api/kiz/stock-summary", "get", "200", KizStockSummary),
        ("/api/kiz", "get", "200", KizPage),
        ("/api/kiz/{kiz_code}", "get", "200", KizState),
        ("/api/kiz/{kiz_code}/events", "get", "200", KizEventPage),
        ("/api/kiz/{kiz_code}/mark-error", "post", "200", KizState),
        ("/api/kiz/{kiz_code}/deactivate", "post", "200", KizState),
        ("/api/movements", "post", "201", MovementBulkCreateResponse),
        ("/api/system/recalculate-inventory", "post", "200", RecalculateInventoryResponse),
    ],
)
def test_documented_success_examples_validate(path, method, code, model):
    operation = app.openapi()["paths"][path][method]
    content = operation["responses"][code]["content"]["application/json"]
    assert content["schema"]
    model.model_validate(content["example"])


def test_request_examples_match_real_models():
    KizAssignment.model_validate(ASSIGNMENT)
    KizTerminalRequest.model_validate(TERMINAL)
    KizIntegrityViolation.model_validate(INTEGRITY_EXAMPLE)
    schema = app.openapi()
    for item in schema["paths"]["/api/movements"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"].values():
        TypeAdapter(list[MovementCreate]).validate_python(item["value"])
    for item in schema["components"]["schemas"]["RecalculateInventoryRequest"]["examples"]:
        RecalculateInventoryRequest.model_validate(item)


def test_all_operation_summaries_are_russian_and_kiz_parameters_explained():
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        for operation in methods.values():
            assert re.search("[А-Яа-я]", operation["summary"]), path
            assert re.search("[А-Яа-я]", operation.get("description", "")), path
            if path.startswith("/api/kiz"):
                for parameter in operation.get("parameters", []):
                    assert re.search("[А-Яа-я]", parameter.get("description", "")), parameter
    for name, model in schema["components"]["schemas"].items():
        if name.startswith("Kiz"):
            for field in model.get("properties", {}).values():
                assert re.search("[А-Яа-я]", field.get("description", "")), (name, field)


def test_conflicts_and_swagger_settings_are_published():
    schema = TestClient(app).get("/openapi.json").json()
    conflict = schema["paths"]["/api/movements"]["post"]["responses"]["409"]["content"][
        "application/json"
    ]
    assert conflict["schema"]["required"] == ["detail", "error_code"]
    assert conflict["examples"]["kiz"]["value"]["error_code"] == "KIZ_CONFLICT"
    assert "filter" in TestClient(app).get("/docs").text
    assert app.swagger_ui_parameters["filter"] is True
