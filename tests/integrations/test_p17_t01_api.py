"""ASGI API wiring with explicit synthetic authorization and service mocks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.api.v1.endpoints import onec
from app.services.user.auth import get_authenticated_user


def app_for_test():
    app = FastAPI()
    app.include_router(onec.router, prefix="/api/v1/integrations/1c")
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(id=7)

    @app.middleware("http")
    async def scope(request, call_next):
        request.state.tenant_id = 11
        return await call_next(request)

    return app


def request(method, path, body=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for_test()), base_url="http://test"
        ) as client:
            return await client.request(method, "/api/v1/integrations/1c" + path, json=body)

    return asyncio.run(send())


def test_connect_scopes_actor_and_never_returns_credentials(monkeypatch):
    result = {
        "id": 5,
        "label": "ERP",
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "state": "unverified",
        "check_code": None,
        "checked_at": None,
        "is_active": True,
    }
    create = AsyncMock(return_value=result)
    monkeypatch.setattr(onec.OneCConnectionService, "create", create)
    response = request(
        "POST",
        "/connect",
        {
            "label": "ERP",
            "base_url": "https://erp.example.com/base",
            "interface": "odata_standard",
            "login": "login-synthetic",
            "password": "password-synthetic",
        },
    )
    assert response.status_code == 201
    assert "password-synthetic" not in response.text and "login-synthetic" not in response.text
    assert create.await_args.kwargs["tenant_id"] == 11
    assert create.await_args.kwargs["actor_id"] == 7


def test_invalid_request_does_not_echo_raw_secrets():
    response = request(
        "POST",
        "/connect",
        {
            "label": "ERP",
            "base_url": "https://example.com",
            "interface": "wrong",
            "login": "private-login",
            "password": "private-password",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert "private" not in response.text


def test_host_admin_denial_is_opaque(monkeypatch):
    monkeypatch.setattr(onec.WorkspaceOutboundHostManager, "approve", AsyncMock(side_effect=onec.ConnectionDenied()))
    response = request("POST", "/hosts", {"host": "erp.example.com"})
    assert response.status_code == 404 and response.json() == {"detail": "connection_unavailable"}


def test_health_delegates_ids_to_authorized_service(monkeypatch):
    check = AsyncMock(
        return_value={
            "id": 5,
            "label": "ERP",
            "host": "erp.example.com",
            "port": 443,
            "base_path": "/base",
            "interface": "odata_standard",
            "state": "verified",
            "check_code": "healthy",
            "checked_at": None,
            "is_active": True,
        }
    )
    monkeypatch.setattr(onec.OneCHealthService, "check", check)
    response = request("POST", "/connections/5/health-check")
    assert response.status_code == 200
    check.assert_awaited_once_with(tenant_id=11, actor_id=7, connection_id=5)


def test_replace_credentials_delegates_scope_without_echo(monkeypatch):
    result = {
        "id": 5,
        "label": "ERP",
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "state": "unverified",
        "check_code": None,
        "checked_at": None,
        "is_active": True,
    }
    replace = AsyncMock(return_value=result)
    monkeypatch.setattr(onec.OneCConnectionService, "replace_credentials", replace)
    response = request("PATCH", "/connections/5/credentials", {"login": "test-login", "password": "test-password"})
    assert response.status_code == 200
    assert "test-login" not in response.text and "test-password" not in response.text
    replace.assert_awaited_once_with(
        tenant_id=11, actor_id=7, connection_id=5, login="test-login", password="test-password"
    )


def test_replace_validation_never_echoes_private_input():
    response = request(
        "PATCH", "/connections/5/credentials", {"login": "test-login", "password": "private-password", "tenant_id": 22}
    )
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    assert "private-password" not in response.text
