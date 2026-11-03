"""DB-exempt actual JWT + API-scope + route checks, service/ORM boundaries mocked."""

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import connector_accounts as endpoints
from app.core.api_scope import ApiScopeMiddleware, _is_personal_connector_path
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import User
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.social import connector_authorizations as accounts
from app.utils.token import create_access_token, create_refresh_token

ROOT = "/api/v1/connector-accounts"
METADATA = {
    "authorization_id": 12,
    "provider_id": "telegram",
    "auth_method": "mtproto",
    "subject_key": "90001",
    "revision": 2,
    "state": "revoked",
}


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in DB-exempt HTTP tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def api(monkeypatch):
    user = SimpleNamespace(id=7, is_active=True, has_admin_access=lambda: False)
    users = SimpleNamespace()
    users.prefetch_related = lambda *args: users
    users.get = AsyncMock(side_effect=lambda *, id: user if id == user.id else None)
    monkeypatch.setattr(User, "objects", users)
    contexts = []

    async def revoke(**arguments):
        contexts.append((current_tenant_id(), is_bypass(), get_current_user().id))
        return METADATA

    listing = AsyncMock(return_value=[METADATA])
    revocation = AsyncMock(side_effect=revoke)
    monkeypatch.setattr(accounts.connector_authorizations, "list_personal", listing)
    monkeypatch.setattr(accounts.connector_authorizations, "revoke_personal", revocation)
    resolver = AsyncMock(return_value=(None, "No workspace access", None))
    monkeypatch.setattr(ApiScopeMiddleware, "_resolve_tenant", resolver)
    app = FastAPI()
    app.include_router(endpoints.router, prefix=ROOT)
    app.add_middleware(ApiScopeMiddleware)
    token = create_access_token(subject="7")[0]
    return SimpleNamespace(
        app=app,
        user=user,
        listing=listing,
        revoke=revocation,
        resolver=resolver,
        contexts=contexts,
        headers={"Authorization": "Bearer " + token},
    )


@pytest.mark.asyncio
async def test_real_jwt_reaches_only_account_self_service_with_no_workspace(api):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        listing = await client.get(ROOT + "/personal", headers=api.headers)
        revoke = await client.post(ROOT + "/12/revoke", json={"expected_revision": 1}, headers=api.headers)
    assert listing.status_code == 200 and listing.json() == [METADATA]
    assert revoke.status_code == 200 and revoke.json() == METADATA
    api.listing.assert_awaited_once_with(actor_id=7)
    api.revoke.assert_awaited_once_with(actor_id=7, authorization_id=12, expected_revision=1)
    api.resolver.assert_not_awaited()
    assert api.contexts == [(None, False, 7)]


@pytest.mark.asyncio
async def test_spoofed_tenant_headers_do_not_select_or_expand_any_workspace(api):
    headers = {**api.headers, "X-Tenant-Id": "999", "X-Tenant-Slug": "foreign"}
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/12/revoke", json={"expected_revision": 1}, headers=headers)
    assert response.status_code == 200 and api.contexts == [(None, False, 7)]
    api.resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Cookie": "session=synthetic"}, {"Authorization": "Bearer invalid"}])
async def test_bearer_jwt_is_required_not_cookie_authentication(api, headers):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/12/revoke", json={"expected_revision": 1}, headers=headers)
    assert response.status_code == 401
    api.revoke.assert_not_awaited()
    api.listing.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_account_is_denied_before_personal_routes(api):
    api.user.is_active = False
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.get(ROOT + "/personal", headers=api.headers)
    assert response.status_code == 401
    api.listing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"expected_revision": 0},
        {"expected_revision": 1, "owner_user_id": 8},
        {"expected_revision": 1, "tenant_id": 999},
        {"expected_revision": 1, "session": "SYNTHETIC-PRIVATE-INPUT"},
    ],
)
async def test_private_validation_never_echoes_forged_scope_or_secret_input(api, payload):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/12/revoke", json=payload, headers=api.headers)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_account_selection"}
    assert "SYNTHETIC-PRIVATE-INPUT" not in response.text
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_or_stale_selection_has_fixed_opaque_error(api):
    api.revoke.side_effect = ConnectionDenied()
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/12/revoke", json={"expected_revision": 1}, headers=api.headers)
    assert response.status_code == 404 and response.json() == {"detail": "connection_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", ROOT),
        ("GET", ROOT + "/grants"),
        ("POST", ROOT + "/12/attach"),
        ("POST", ROOT + "/grants/12/detach"),
        ("POST", ROOT + "/telegram/login"),
        ("POST", ROOT + "/personal/12/revoke"),
        ("POST", ROOT + "/12/revoke/extra"),
        ("GET", ROOT + "/12/revoke"),
    ],
)
async def test_workspace_operations_and_prefix_lookalikes_still_require_membership(api, method, path):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.request(method, path, headers=api.headers)
    assert response.status_code == 403
    api.resolver.assert_awaited_once()
    api.revoke.assert_not_awaited()
    api.listing.assert_not_awaited()


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", ROOT + "/personal", True),
        ("GET", ROOT + "/personal/", True),
        ("POST", ROOT + "/12/revoke", True),
        ("POST", ROOT + "/12/revoke/", True),
        ("POST", ROOT + "/12/revoke-extra", False),
        ("DELETE", ROOT + "/12/revoke", False),
        ("POST", ROOT + "/telegram/login/revoke", False),
        ("POST", ROOT + "/personal", False),
    ],
)
def test_account_scope_exemption_is_exact_and_method_bounded(method, path, expected):
    assert _is_personal_connector_path(path, method) is expected


@pytest.mark.asyncio
async def test_refresh_token_is_not_an_account_access_token(api):
    token = create_refresh_token(subject="7")[0]
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(
            ROOT + "/12/revoke", json={"expected_revision": 1}, headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == 401
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_admin_still_receives_only_non_bypass_account_scope(api):
    api.user.has_admin_access = lambda: True
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/12/revoke", json={"expected_revision": 1}, headers=api.headers)
    assert response.status_code == 200 and api.contexts == [(None, False, 7)]
    api.resolver.assert_not_awaited()
