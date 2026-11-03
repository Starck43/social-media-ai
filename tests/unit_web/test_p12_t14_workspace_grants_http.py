"""DB-exempt browser-session/CSRF checks with fake authorization services."""

import socket
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Dialect
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.admin.csrf import CSRFTokenManager
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.utils.token import create_access_token
from app.web import connector_accounts as page
from app.web import web_router
from app.web.middleware import TenantUIMiddleware

ROOT = "/app/settings/telegram-accounts/workspace"
SECRET = "SYNTHETIC-DO-NOT-SHOW"
ACCOUNT = dict(
    authorization_id=12, provider_id="telegram", auth_method="mtproto", subject_key="90001", revision=2, state="active"
)
GRANT = dict(grant_id=31, authorization_id=12, authorization_revision=2, is_active=True)


class FormParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = attrs.get("action")
            self.forms[self.current] = {}
        elif tag == "input" and self.current and attrs.get("name"):
            self.forms[self.current][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB, crypto and network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Dialect, "connect", forbidden)
    from app.services.social import connector_authorizations, telegram_login

    for key in ("async_session_maker", "encrypt_secret", "decrypt_secret"):
        monkeypatch.setattr(connector_authorizations, key, forbidden)
    monkeypatch.setattr(telegram_login, "telegram_login_step", forbidden)


@pytest.fixture
def env(monkeypatch):
    user = SimpleNamespace(id=7, username="synthetic", is_active=True, role=None, has_admin_access=lambda: False)
    users = SimpleNamespace()
    users.prefetch_related = lambda *args: users
    users.get = AsyncMock(return_value=user)
    monkeypatch.setattr(User, "objects", users)
    members = [
        SimpleNamespace(tenant_id=i, role_id=1, role=SimpleNamespace(codename="owner"), is_owner=True) for i in (1, 2)
    ]
    monkeypatch.setattr(TenantUserManager, "web_memberships", AsyncMock(return_value=members))
    monkeypatch.setattr(TenantUserManager, "member_grants", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(
        tenants, "get", AsyncMock(side_effect=lambda *, id: SimpleNamespace(id=id, name=f"workspace-{id}"))
    )
    contexts = []

    async def listing(**kwargs):
        contexts.append((current_tenant_id(), is_bypass(), get_current_user().id, kwargs))
        return [{**ACCOUNT, "secret_encrypted": SECRET}], True

    mocks = SimpleNamespace(
        list_reusable=AsyncMock(side_effect=listing),
        list_grants=AsyncMock(return_value=[{**GRANT, "secret_encrypted": SECRET}]),
        attach=AsyncMock(return_value=GRANT),
        detach=AsyncMock(return_value={**GRANT, "is_active": False}),
    )
    for name in vars(mocks):
        monkeypatch.setattr(page.connector_authorizations, name, getattr(mocks, name))
    from app.services.social.telegram_source_bindings import telegram_source_bindings

    monkeypatch.setattr(
        telegram_source_bindings,
        "list_sources",
        AsyncMock(return_value={"sources": [], "can_edit": True, "next_after": None}),
    )
    app = FastAPI()
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.csrf_manager = CSRFTokenManager("synthetic-secret")

    @app.get("/__seed")
    async def seed(request: Request, tenant: int = 1):
        request.session["token"] = create_access_token(subject="7")[0]
        request.session["app_tenant_id"] = tenant
        return PlainTextResponse("ok")

    app.add_middleware(TenantUIMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="synthetic-secret", https_only=True)
    return SimpleNamespace(app=app, mocks=mocks, members=members, contexts=contexts)


@asynccontextmanager
async def client_for(env):
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        await client.get("/__seed")
        yield client


async def form(client, suffix):
    response = await client.get(ROOT)
    assert response.status_code == 200
    return FormParser(response.text).forms[ROOT + suffix]


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_list_and_explicit_attach_detach_are_scoped_and_private(env):
    async with client_for(env) as client:
        response = await client.get(ROOT, headers={"X-Tenant-Id": "999"})
        parser = FormParser(response.text)
        attach = await client.post(ROOT + "/accounts/12/attach", data=parser.forms[ROOT + "/accounts/12/attach"])
        detach = await client.post(ROOT + "/grants/31/detach", data=parser.forms[ROOT + "/grants/31/detach"])
    assert response.status_code == 200 and "workspace-1" in response.text
    assert "выбор" in response.text.lower() and "не проверяет доступность сессии" in response.text.lower()
    assert attach.status_code == detach.status_code == 303
    env.mocks.attach.assert_awaited_once_with(tenant_id=1, actor_id=7, authorization_id=12, expected_revision=2)
    env.mocks.detach.assert_awaited_once_with(tenant_id=1, actor_id=7, grant_id=31)
    assert env.contexts == [(1, False, 7, {"tenant_id": 1, "actor_id": 7})]
    for response in (response, attach, detach):
        private(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,kind",
    [
        ("/accounts/12/attach", "workspace"),
        ("/grants/31/detach", "workspace"),
        ("/accounts/12/attach", "session"),
        ("/accounts/12/attach", "expired"),
        ("/accounts/12/attach", "forged"),
    ],
)
async def test_csrf_bound_to_actor_session_surface_and_workspace(env, suffix, kind):
    async with client_for(env) as client:
        data = await form(client, suffix)
        if kind == "workspace":
            await client.get("/__seed", params={"tenant": 2})
        elif kind == "session":
            async with client_for(env) as other:
                data["_csrf"] = (await form(other, suffix))["_csrf"]
        elif kind == "expired":
            env.app.state.csrf_manager.expire_seconds = -1
        else:
            data["_csrf"] = SECRET
        response = await client.post(ROOT + suffix, data=data)
    assert response.status_code == 403
    private(response)
    env.mocks.attach.assert_not_awaited()
    env.mocks.detach.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "2"},
        {"owner_user_id": "9"},
        {"session": SECRET},
        {"expected_revision": "0"},
        {"expected_revision": "02"},
        {"confirm": "yes"},
    ],
)
async def test_forged_scope_revision_and_confirmation_refused(env, change):
    async with client_for(env) as client:
        data = {**await form(client, "/accounts/12/attach"), **change}
        response = await client.post(ROOT + "/accounts/12/attach", data=data)
    assert response.status_code == 422
    private(response)
    env.mocks.attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_fields_and_oversized_body_refused(env):
    async with client_for(env) as client:
        data = await form(client, "/accounts/12/attach")
        encoded = urlencode(data) + "&confirm=attach-workspace"
        duplicate = await client.post(
            ROOT + "/accounts/12/attach", content=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        oversized = await client.post(
            ROOT + "/accounts/12/attach",
            content="x=" + "a" * 4096,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert duplicate.status_code == oversized.status_code == 422
    env.mocks.attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_workspace_and_no_session_do_not_reach_service(env):
    async with client_for(env) as client:
        env.members.clear()
        response = await client.get(ROOT)
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as anonymous:
        anon = await anonymous.get(ROOT)
    assert response.status_code == anon.status_code == 302
    env.mocks.list_reusable.assert_not_awaited()


@pytest.mark.asyncio
async def test_view_only_hides_attach_but_allows_detach(env):
    env.mocks.list_reusable.return_value = ([ACCOUNT], False)
    env.mocks.list_reusable.side_effect = None
    async with client_for(env) as client:
        response = await client.get(ROOT)
        forms = FormParser(response.text).forms
        assert ROOT + "/accounts/12/attach" not in forms
        detached = await client.post(ROOT + "/grants/31/detach", data=forms[ROOT + "/grants/31/detach"])
    assert detached.status_code == 303
    env.mocks.detach.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,status", [(ConnectionDenied(), 404), (RuntimeError(SECRET), 503)])
async def test_failure_is_opaque_and_does_not_assert_rollback(env, exception, status):
    env.mocks.attach.side_effect = exception
    async with client_for(env) as client:
        data = await form(client, "/accounts/12/attach")
        response = await client.post(ROOT + "/accounts/12/attach", data=data)
    assert response.status_code == status
    private(response)
    assert "успешно" not in response.text.lower()


@pytest.mark.asyncio
async def test_invalid_service_projection_not_reported_as_success(env):
    env.mocks.attach.return_value = {**GRANT, "authorization_id": 13, "secret": SECRET}
    async with client_for(env) as client:
        data = await form(client, "/accounts/12/attach")
        response = await client.post(ROOT + "/accounts/12/attach", data=data)
    assert response.status_code == 503
    private(response)


@pytest.mark.asyncio
async def test_foreign_actor_token_and_personal_surface_token_cannot_authorize(env, monkeypatch):
    from app.admin.csrf import SESSION_ID_KEY

    @env.app.get("/__other-token")
    async def other_token(request: Request):
        return PlainTextResponse(
            env.app.state.csrf_manager.generate_token(
                session_id=page._csrf_identity(request.session[SESSION_ID_KEY], 7)
            )
        )

    async with client_for(env) as client:
        data = await form(client, "/accounts/12/attach")
        personal_token = (await client.get("/__other-token")).text
        response = await client.post(ROOT + "/accounts/12/attach", data={**data, "_csrf": personal_token})
        # A fresh web identity in the same session invalidates the prior token.
        users = User.objects
        users.get = AsyncMock(
            return_value=SimpleNamespace(
                id=8, username="other", is_active=True, role=None, has_admin_access=lambda: False
            )
        )
        switched = await client.post(ROOT + "/accounts/12/attach", data=data)
    assert response.status_code == switched.status_code == 403
    env.mocks.attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_or_invalid_lists_never_render_false_empty(env):
    env.mocks.list_reusable.side_effect = ConnectionDenied()
    async with client_for(env) as client:
        denied = await client.get(ROOT)
        env.mocks.list_reusable.side_effect = None
        env.mocks.list_reusable.return_value = ([{**ACCOUNT, "provider_id": "unknown", "subject_key": SECRET}], True)
        malformed = await client.get(ROOT)
    assert denied.status_code == 404 and malformed.status_code == 503
    for response in (denied, malformed):
        private(response)
        assert 'role="alert"' in response.text
        assert "Нет доступных локальных авторизаций" not in response.text


@pytest.mark.asyncio
async def test_names_and_subject_are_escaped_in_html(env):
    env.mocks.list_reusable.side_effect = None
    env.mocks.list_reusable.return_value = ([{**ACCOUNT, "subject_key": '<img src=x onerror="bad()">'}], True)
    async with client_for(env) as client:
        response = await client.get(ROOT)
    assert response.status_code == 200
    assert "&lt;img" in response.text and '<img src=x onerror="bad()">' not in response.text
