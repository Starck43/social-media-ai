"""Web form boundary under synthetic ASGI requests; no DB or 1C network."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from starlette.responses import HTMLResponse

from app.web import onec


def app_for_test():
    app = FastAPI()
    app.include_router(onec.router, prefix="/app")

    @app.middleware("http")
    async def scope(request, call_next):
        request.state.tenant_id = 11
        request.state.web_user = SimpleNamespace(id=7, is_active=True)
        return await call_next(request)

    return app


def request(path, content):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for_test()), base_url="http://test"
        ) as client:
            return await client.post(
                "/app/settings/onec" + path,
                content=content,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )

    return asyncio.run(send())


def test_connect_uses_scope_and_does_not_echo_synthetic_password(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: token == "valid")
    monkeypatch.setattr(onec, "add_flash", lambda *args: None)
    create = AsyncMock()
    monkeypatch.setattr(onec.OneCConnectionService, "create", create)
    response = request(
        "/connect",
        "_csrf=valid&label=ERP&base_url=https%3A%2F%2Ferp.example.com%2Fbase&interface=odata_standard&login=test-user&password=test-password",
    )
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert "test-password" not in response.text + response.headers["location"]
    assert create.await_args.kwargs["tenant_id"] == 11
    assert create.await_args.kwargs["actor_id"] == 7


def test_duplicate_form_field_is_rejected_before_service(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: True)
    create = AsyncMock()
    monkeypatch.setattr(onec.OneCConnectionService, "create", create)
    response = request(
        "/connect",
        "_csrf=valid&label=ERP&base_url=https%3A%2F%2Ferp.example.com&interface=odata_standard&login=user&password=private&password=private",
    )
    assert response.status_code == 403 and "private" not in response.text
    create.assert_not_awaited()


def test_csrf_failure_and_permission_denial_are_fail_closed(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: False)
    approve = AsyncMock()
    monkeypatch.setattr(onec.WorkspaceOutboundHostManager, "approve", approve)
    response = request("/hosts", "_csrf=invalid&host=erp.example.com")
    assert response.status_code == 403
    approve.assert_not_awaited()
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: True)
    approve.side_effect = onec.ConnectionDenied()
    response = request("/hosts", "_csrf=valid&host=erp.example.com")
    assert response.status_code == 403
    approve.assert_awaited_once_with(tenant_id=11, actor_id=7, host="erp.example.com")


def test_oversized_body_does_not_call_service(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: True)
    approve = AsyncMock()
    monkeypatch.setattr(onec.WorkspaceOutboundHostManager, "approve", approve)
    response = request("/hosts", "_csrf=valid&host=" + "a" * 65536)
    assert response.status_code == 403
    approve.assert_not_awaited()


def test_web_router_and_settings_card_are_wired():
    from pathlib import Path

    from app.web import web_router

    matches = [route for route in web_router.routes if getattr(route, "original_router", None) is onec.router]
    assert len(matches) == 1
    settings = Path("app/web/templates/web/settings.html").read_text()
    assert 'href="/app/settings/onec"' in settings


def test_replace_form_reactivates_by_id_and_never_echoes_secret(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: token == "valid")
    monkeypatch.setattr(onec, "add_flash", lambda *args: None)
    replace = AsyncMock()
    monkeypatch.setattr(onec.OneCConnectionService, "replace_credentials", replace)
    response = request("/connections/19/credentials", "_csrf=valid&login=synthetic-user&password=synthetic-password")
    assert response.status_code == 303 and response.headers["cache-control"] == "no-store"
    assert "synthetic-password" not in response.text + response.headers["location"]
    replace.assert_awaited_once_with(
        tenant_id=11, actor_id=7, connection_id=19, login="synthetic-user", password="synthetic-password"
    )


def test_replace_form_requires_csrf_before_service(monkeypatch):
    monkeypatch.setattr(onec, "ensure_csrf", lambda request, token: False)
    replace = AsyncMock()
    monkeypatch.setattr(onec.OneCConnectionService, "replace_credentials", replace)
    response = request("/connections/19/credentials", "_csrf=bad&login=user&password=synthetic-secret")
    assert response.status_code == 403 and "synthetic-secret" not in response.text
    replace.assert_not_awaited()
