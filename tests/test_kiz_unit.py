"""Input validation and stable application error mapping without PostgreSQL."""
import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.kiz_errors import KizConflictError, KizGuardError, KizNotFoundError
from app.core.schemas.kiz import KizAssignment, KizTerminalRequest
from app.middleware.error_handler import add_exception_handlers


@pytest.mark.parametrize("code", ["", " ", " code", "code\n", "\tcode", "code\x00"])
def test_reject_malformed_code(code):
    with pytest.raises(ValidationError):
        KizAssignment(kiz_code=code, product_id="sku", location_code="LOC", author="tester")


@pytest.mark.parametrize("reason", ["", " ", "\n", "\x00"])
def test_terminal_requires_reason(reason):
    with pytest.raises(ValidationError):
        KizTerminalRequest(author="tester", reason=reason)


def test_code_not_normalized_and_origin_is_server_owned():
    data = dict(kiz_code="Code/ABC+123", product_id="sku", location_code="LOC", author="tester")
    assert KizAssignment(**data).kiz_code == "Code/ABC+123"
    with pytest.raises(ValidationError):
        KizAssignment(**data, origin_type="receipt")


@pytest.mark.parametrize("exc,expected_status,code", [
    (KizGuardError("guard"), 409, "KIZ_CONFLICT"),
    (KizConflictError("duplicate"), 409, "KIZ_CONFLICT"),
    (asyncpg.SerializationError("stale snapshot"), 409, "CONCURRENT_WRITE_CONFLICT"),
    (asyncpg.DeadlockDetectedError("deadlock"), 409, "CONCURRENT_WRITE_CONFLICT"),
    (KizNotFoundError("missing"), 404, "KIZ_NOT_FOUND"),
])
async def test_http_exception_mapping(exc, expected_status, code):
    app = FastAPI()
    add_exception_handlers(app)
    @app.post("/write")
    async def write():
        raise exc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/write")
    assert response.status_code == expected_status
    assert response.json()["error_code"] == code


@pytest.mark.parametrize("code", ["stock-summary", "alpha/events", "/events", "x/y/events", ".", "..", "a/../b", "a\nb"])
def test_assignment_rejects_unaddressable_code(code):
    with pytest.raises(ValidationError) as exc:
        KizAssignment(kiz_code=code, product_id="sku", location_code="LOC", author="tester")
    assert exc.value.errors()[0]["loc"] == ("kiz_code",)


@pytest.mark.parametrize("metadata", [
    {"note": "bad\x00value"}, {"bad\x00key": "value"},
    {"nested": [{"note": "bad\x00value"}]}, {"note": "\ud800"},
    {"nested": [{"\udfff": "value"}]},
])
@pytest.mark.parametrize("model", [KizAssignment, KizTerminalRequest])
def test_metadata_rejects_jsonb_unsupported_unicode(model, metadata):
    data = dict(author="tester", metadata=metadata)
    if model is KizAssignment:
        data.update(kiz_code="normal", product_id="sku", location_code="LOC")
    else:
        data["reason"] = "Закрытие"
    with pytest.raises(ValidationError) as exc:
        model(**data)
    assert exc.value.errors()[0]["loc"] == ("metadata",)


@pytest.mark.parametrize("code", ["Stock-summary", "alpha/Events", "events", "assign", "КИЗ/ABC+123"])
def test_safe_codes_and_unicode_metadata_are_preserved(code):
    metadata = {"комментарий": ["Товар 😀", "\\u0000", None, 1, True]}
    data = KizAssignment(kiz_code=code, product_id="sku", location_code="LOC", author="tester", metadata=metadata)
    assert data.kiz_code == code and data.metadata == metadata


@pytest.mark.parametrize("operation", ["assign", "mark-error", "deactivate"])
async def test_invalid_unicode_metadata_is_http_422_before_service(operation):
    import json
    from app.api.v1.endpoints.kiz import router
    from app.api.v1.dependencies import get_kiz_service
    app = FastAPI()
    app.include_router(router, prefix="/api")
    add_exception_handlers(app)
    # An attempted service call fails: validation must stop before invoking it.
    app.dependency_overrides[get_kiz_service] = lambda: object()
    data = {"author": "tester", "metadata": {"nested": [{"note": "\ud800"}]}}
    if operation == "assign":
        data.update(kiz_code="normal", product_id="sku", location_code="LOC")
    else:
        data["reason"] = "Закрытие"
    path = "/api/kiz/assign" if operation == "assign" else f"/api/kiz/normal/{operation}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=json.dumps(data), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "metadata"]
