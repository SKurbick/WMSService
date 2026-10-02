from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_kiz_activation_service
from app.api.v1.endpoints.kiz import router
from app.core.exceptions import KizActivationNotFoundError, KizConflictError
from app.middleware.error_handler import add_exception_handlers


@pytest.fixture
def app():
    application = FastAPI()
    add_exception_handlers(application)
    application.include_router(router, prefix="/api")
    return application


@pytest.mark.asyncio
async def test_duplicate_codes_are_http_422(app):
    service = AsyncMock()
    app.dependency_overrides[get_kiz_activation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/kiz/activate",
            json={
                "kiz_codes": ["K1", "K1"],
                "location_code": "LOC-A",
                "user_name": "operator",
                "idempotency_key": "key",
            },
        )
    assert response.status_code == 422
    service.activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_activation_conflict_is_http_409_kiz_conflict(app):
    service = AsyncMock()
    service.activate.side_effect = KizConflictError("conflict")
    app.dependency_overrides[get_kiz_activation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/kiz/activate",
            json={
                "kiz_codes": ["K1"],
                "location_code": "LOC-A",
                "user_name": "operator",
                "idempotency_key": "key",
            },
        )
    assert response.status_code == 409
    assert response.json()["error_code"] == "KIZ_CONFLICT"


@pytest.mark.asyncio
async def test_unknown_kiz_is_http_404(app):
    service = AsyncMock()
    service.activate.side_effect = KizActivationNotFoundError(
        "missing",
        error_code="KIZ_NOT_FOUND",
    )
    app.dependency_overrides[get_kiz_activation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/kiz/activate",
            json={
                "kiz_codes": ["K1"],
                "location_code": "LOC-A",
                "user_name": "operator",
                "idempotency_key": "key",
            },
        )
    assert response.status_code == 404
    assert response.json()["error_code"] == "KIZ_NOT_FOUND"
