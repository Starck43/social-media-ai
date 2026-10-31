"""Focused contracts for the shared admin operator-identity resolver."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

import app.models
import app.services.user.auth
from app.admin import auth as admin_auth
from app.admin.identity import resolve_operator_identity
from app.admin.scope import AdminScopeMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_access", [True, False])
async def test_resolver_reloads_role_and_can_require_admin_access(monkeypatch, admin_access):
    authenticated = SimpleNamespace(id=17)
    fresh_user = SimpleNamespace(id=17, is_active=True, has_admin_access=lambda: admin_access)
    authenticate = AsyncMock(return_value=authenticated)

    class Query:
        def __init__(self):
            self.relations = None
            self.criteria = None

        def prefetch_related(self, *relations):
            self.relations = relations
            return self

        async def get(self, **criteria):
            self.criteria = criteria
            return fresh_user

    query = Query()
    monkeypatch.setattr(app.services.user.auth, "get_authenticated_user", authenticate)
    monkeypatch.setattr(app.models.User, "objects", query)

    resolved = await resolve_operator_identity("session-token", require_admin_access=True)

    assert (resolved is fresh_user) is admin_access
    authenticate.assert_awaited_once_with(token="session-token")
    assert query.relations == ("role.permissions",)
    assert query.criteria == {"id": 17}


@pytest.mark.asyncio
async def test_resolver_preserves_explicit_token_type_and_skips_reload_for_unknown_token(monkeypatch):
    authenticate = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.user.auth, "get_authenticated_user", authenticate)
    monkeypatch.setattr(
        app.models.User,
        "objects",
        SimpleNamespace(prefetch_related=AsyncMock(side_effect=AssertionError("unexpected database lookup"))),
    )

    assert await resolve_operator_identity("access-token", token_type="access") is None
    authenticate.assert_awaited_once_with(token="access-token", token_type="access")


@pytest.mark.asyncio
async def test_auth_backend_uses_the_resolved_identity_and_refreshes_session(monkeypatch):
    user = SimpleNamespace(id=17, username="operator", is_active=True, has_admin_access=lambda: True)
    resolve_identity = AsyncMock(return_value=user)
    monkeypatch.setattr(admin_auth, "resolve_operator_identity", resolve_identity)
    request = Request({"type": "http", "method": "GET", "path": "/admin", "headers": []})
    request.scope["session"] = {"token": "session-token", admin_auth.SESSION_ID_KEY: "bound-session"}

    assert await admin_auth.AdminAuthBackend(secret_key="unit-test-secret").authenticate(request) is True

    resolve_identity.assert_awaited_once_with("session-token", require_admin_access=True)
    assert request.state.admin_user is user
    assert request.session["user_id"] == "17"
    assert request.session["username"] == "operator"


@pytest.mark.asyncio
@pytest.mark.parametrize("active,expected", [(True, True), (False, False)])
async def test_scope_session_user_uses_the_shared_resolver_and_rejects_inactive(monkeypatch, active, expected):
    user = SimpleNamespace(id=17, is_active=active)
    resolve_identity = AsyncMock(return_value=user)
    monkeypatch.setattr("app.admin.scope.resolve_operator_identity", resolve_identity)
    request = Request({"type": "http", "method": "GET", "path": "/admin", "headers": []})
    request.scope["session"] = {"token": "session-token"}

    resolved = await AdminScopeMiddleware._session_user(request)

    assert (resolved is user) is expected
    resolve_identity.assert_awaited_once_with("session-token")
