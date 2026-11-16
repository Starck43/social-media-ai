"""DB-exempt real cookie JWT/middleware/CSRF/router tests; ORM/services are fakes."""

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

from app.admin.csrf import SESSION_ID_KEY, CSRFTokenManager
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.utils.token import create_access_token, create_refresh_token
from app.web import connector_accounts as page
from app.web import web_router
from app.web.middleware import TenantUIMiddleware, _is_personal_connector_path

ROOT = "/app/settings/telegram-accounts"
SECRET = "SYNTHETIC-PRIVATE-WEB-SENTINEL"
ACCOUNT = dict(
    authorization_id=12, provider_id="telegram", auth_method="mtproto", subject_key="90001", revision=2, state="active"
)


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def token_of(response):
    # The shell has unrelated unbound _csrf inputs. Select the account form only.
    start = response.text.index('class="telegram-account-revoke"')
    section = response.text[start : response.text.index("</form>", start)]
    return next(a["value"] for tag, a in Document(section).tags if tag == "input" and a.get("name") == "_csrf")


@pytest.fixture(autouse=True)
def deny_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB/provider/network forbidden in personal page unit tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Dialect, "connect", forbidden)
    from app.services.social import connector_authorizations, telegram_login

    for name in ("async_session_maker", "encrypt_secret", "decrypt_secret"):
        monkeypatch.setattr(connector_authorizations, name, forbidden)
    monkeypatch.setattr(telegram_login, "telegram_login_step", forbidden)


@pytest.fixture
def api(monkeypatch):
    users_by_id = {
        i: SimpleNamespace(id=i, username=f"synthetic-{i}", is_active=True, role=None, has_admin_access=lambda: False)
        for i in (7, 8)
    }
    users = SimpleNamespace()
    users.prefetch_related = lambda *args: users
    users.get = AsyncMock(side_effect=lambda *, id: users_by_id.get(id))
    monkeypatch.setattr(User, "objects", users)
    memberships = AsyncMock(return_value=[])
    monkeypatch.setattr(TenantUserManager, "web_memberships", memberships)
    workspace_read = AsyncMock(side_effect=AssertionError("personal page must not resolve workspace"))
    monkeypatch.setattr(tenants, "get", workspace_read)
    rows = [{**ACCOUNT, "secret_encrypted": SECRET, "phone": SECRET, "owner_user_id": SECRET}]
    contexts = []

    async def listing(**kwargs):
        contexts.append((current_tenant_id(), is_bypass(), get_current_user().id, kwargs))
        return rows

    async def revoke(**kwargs):
        contexts.append((current_tenant_id(), is_bypass(), get_current_user().id, kwargs))
        result = {**ACCOUNT, "state": "revoked", "revision": 3}
        rows[:] = [result]
        return result

    listing_mock = AsyncMock(side_effect=listing)
    revoke_mock = AsyncMock(side_effect=revoke)
    monkeypatch.setattr(page.connector_authorizations, "list_personal", listing_mock)
    monkeypatch.setattr(page.connector_authorizations, "revoke_personal", revoke_mock)
    app = FastAPI()
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.csrf_manager = CSRFTokenManager("synthetic-session-secret")

    @app.get("/__seed")
    async def seed(request: Request, user: int = 7, refresh: bool = False):
        # Test-only session seeding, not mounted by production code.
        request.session["token"] = (create_refresh_token if refresh else create_access_token)(subject=str(user))[0]
        return PlainTextResponse("seeded")

    @app.get("/__unbound")
    async def unbound(request: Request, session_only: bool = False):
        return PlainTextResponse(
            app.state.csrf_manager.generate_token(
                session_id=request.session.get(SESSION_ID_KEY) if session_only else None
            )
        )

    app.add_middleware(TenantUIMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="synthetic-session-secret", https_only=True)
    return SimpleNamespace(
        app=app,
        rows=rows,
        listing=listing_mock,
        revoke=revoke_mock,
        contexts=contexts,
        memberships=memberships,
        workspace_read=workspace_read,
        users=users_by_id,
    )


@asynccontextmanager
async def seeded(api):
    async with AsyncClient(
        transport=ASGITransport(app=api.app), base_url="https://testserver", follow_redirects=False
    ) as client:
        await client.get("/__seed")
        yield client


async def form(client):
    response = await client.get(ROOT)
    assert response.status_code == 200
    return dict(_csrf=token_of(response), expected_revision="2", confirm="revoke-local")


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_real_cookie_jwt_lists_and_revokes_without_any_membership_or_tenant_lookup(api):
    async with seeded(api) as client:
        listing = await client.get(ROOT, headers={"X-Tenant-Id": "999"})
        data = dict(_csrf=token_of(listing), expected_revision="2", confirm="revoke-local")
        result = await client.post(ROOT + "/12/revoke", data=data, headers={"X-Tenant-Id": "999"})
        after = await client.get(ROOT)
    assert listing.status_code == 200 and "90001" in listing.text
    assert SECRET not in listing.text
    assert result.status_code == 303 and result.headers["location"] == ROOT
    assert "Отозвана локально" in after.text and 'name="confirm"' not in after.text
    api.revoke.assert_awaited_once_with(actor_id=7, authorization_id=12, expected_revision=2)
    assert all(tenant is None and not bypass and actor == 7 for tenant, bypass, actor, _ in api.contexts)
    api.memberships.assert_not_awaited()
    api.workspace_read.assert_not_awaited()
    for response in (listing, result, after):
        private(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh,inactive", [(True, False), (False, True)])
async def test_invalid_web_identity_cannot_read_or_mutate(api, refresh, inactive):
    api.users[7].is_active = not inactive
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        await client.get("/__seed", params={"refresh": str(refresh).lower()})
        for method, path in (("GET", ROOT), ("POST", ROOT + "/12/revoke")):
            response = await client.request(method, path)
            assert response.status_code == 302 and response.headers["location"].startswith("/app/login?next=")
    api.listing.assert_not_awaited()
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_bearer_without_session_does_not_authenticate_browser_page(api):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.get(ROOT, headers={"Authorization": "Bearer " + create_access_token(subject="7")[0]})
    assert response.status_code == 302
    api.listing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["missing", "forged", "unbound", "session-only", "expired", "another-session", "another-actor"]
)
async def test_csrf_requires_expiry_session_actor_and_surface_binding(api, kind):
    async with seeded(api) as client:
        data = await form(client)
        if kind == "missing":
            data["_csrf"] = ""
        elif kind == "forged":
            data["_csrf"] = SECRET
        elif kind in {"unbound", "session-only"}:
            data["_csrf"] = (
                await client.get("/__unbound", params={"session_only": str(kind == "session-only").lower()})
            ).text
        elif kind == "expired":
            api.app.state.csrf_manager.expire_seconds = -1
        elif kind == "another-session":
            async with seeded(api) as second:
                data["_csrf"] = (await form(second))["_csrf"]
        else:
            await client.get("/__seed", params={"user": 8})
        response = await client.post(ROOT + "/12/revoke", data=data)
    assert response.status_code == 403 and SECRET not in response.text
    private(response)
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": "0"},
        {"expected_revision": "true"},
        {"expected_revision": "2.0"},
        {"expected_revision": "02"},
        {"expected_revision": "２"},
        {"expected_revision": "-2"},
        {"expected_revision": "9" * 100},
        {"confirm": ""},
        {"confirm": "yes"},
        {"tenant_id": "999"},
        {"owner_user_id": "8"},
        {"session": SECRET},
    ],
)
async def test_invalid_confirmation_revision_or_forged_scope_never_reaches_service(api, change):
    async with seeded(api) as client:
        data = {**await form(client), **change}
        response = await client.post(ROOT + "/12/revoke", data=data)
    assert response.status_code == 422 and SECRET not in response.text
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["_csrf", "expected_revision", "confirm"])
async def test_duplicate_form_fields_are_rejected_not_silently_selected(api, field):
    async with seeded(api) as client:
        data = await form(client)
        body = urlencode([*data.items(), (field, SECRET)])
        response = await client.post(
            ROOT + "/12/revoke", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    assert response.status_code == 422 and SECRET not in response.text
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,content_type",
    [
        (SECRET, "application/json"),
        (SECRET, "multipart/form-data"),
        (b"\xff", "application/x-www-form-urlencoded"),
        ("x=" + "a" * 5000, "application/x-www-form-urlencoded"),
    ],
)
async def test_wrong_format_invalid_utf8_and_oversized_body_fail_safely(api, body, content_type):
    async with seeded(api) as client:
        await form(client)
        response = await client.post(ROOT + "/12/revoke", content=body, headers={"Content-Type": content_type})
    assert response.status_code == 422 and SECRET not in response.text
    api.revoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["listing", "revoke"])
@pytest.mark.parametrize("denied", [False, True])
async def test_failures_are_opaque_and_never_render_a_false_empty_or_success(api, operation, denied, caplog):
    async with seeded(api) as client:
        data = await form(client)
        error = ConnectionDenied() if denied else RuntimeError(SECRET)
        getattr(api, operation).side_effect = error
        response = await (client.get(ROOT) if operation == "listing" else client.post(ROOT + "/12/revoke", data=data))
    assert response.status_code == (404 if denied else 503)
    assert SECRET not in response.text + caplog.text
    assert "data-telegram-empty" not in response.text and 'class="telegram-account-revoke"' not in response.text
    assert "Данные недоступны" in response.text
    private(response)


@pytest.mark.asyncio
async def test_missing_encryption_key_and_untrusted_metadata_do_not_leak(api, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    api.rows[0]["subject_key"] = '<img src=x onerror="bad()">'
    async with seeded(api) as client:
        response = await client.get(ROOT)
    assert response.status_code == 200 and "&lt;img" in response.text
    assert not any(tag == "img" for tag, _ in Document(response.text).tags)
    assert SECRET not in response.text
    fields = {a["name"] for tag, a in Document(response.text).tags if tag == "input"}
    assert not fields.intersection({"tenant_id", "owner_user_id", "session", "phone", "api_hash"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,method",
    [
        ("/extra", "GET"),
        ("/12/revoke/extra", "POST"),
        ("/12/revoke", "GET"),
        ("", "POST"),
        ("/12/attach", "POST"),
        ("/telegram/login", "POST"),
    ],
)
async def test_workspace_exemption_never_covers_prefix_lookalikes(api, suffix, method):
    async with seeded(api) as client:
        response = await client.request(method, ROOT + suffix)
    assert response.status_code == 302 and response.headers["location"] == "/app/invite"
    api.memberships.assert_awaited_once()
    api.listing.assert_not_awaited()
    api.revoke.assert_not_awaited()


@pytest.mark.parametrize(
    "path,method,wanted",
    [
        (ROOT, "GET", True),
        (ROOT, "HEAD", False),
        (ROOT + "/", "GET", False),
        (ROOT + "/12/revoke", "POST", True),
        (ROOT + "/0/revoke", "POST", False),
        (ROOT + "/-1/revoke", "POST", False),
        (ROOT + "/12/revoke/", "POST", False),
    ],
)
def test_personal_path_classifier_is_exact(path, method, wanted):
    assert _is_personal_connector_path(path, method) is wanted


@pytest.mark.asyncio
async def test_multiple_accounts_have_independent_forms_and_selected_revision(api):
    api.rows.append({**ACCOUNT, "authorization_id": 19, "subject_key": "90002", "revision": 5})
    async with seeded(api) as client:
        response = await client.get(ROOT)
        doc = Document(response.text)
        assert response.status_code == 200
        assert sum(tag == "form" and a.get("action") == ROOT + "/19/revoke" for tag, a in doc.tags) == 1
        data = dict(_csrf=token_of(response), expected_revision="5", confirm="revoke-local")
        api.revoke.side_effect = None
        api.revoke.return_value = {**ACCOUNT, "authorization_id": 19, "state": "revoked", "revision": 6}
        result = await client.post(ROOT + "/19/revoke", data=data)
    assert result.status_code == 303
    api.revoke.assert_awaited_once_with(actor_id=7, authorization_id=19, expected_revision=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["listing", "revoke"])
async def test_invalid_service_projection_fails_without_echo_or_false_success(api, operation, caplog):
    async with seeded(api) as client:
        data = await form(client)
        if operation == "listing":
            api.rows[:] = [{**ACCOUNT, "provider_id": SECRET}]
            response = await client.get(ROOT)
        else:
            api.revoke.side_effect = None
            api.revoke.return_value = {**ACCOUNT, "state": "active", "subject_key": SECRET}
            response = await client.post(ROOT + "/12/revoke", data=data)
    assert response.status_code == 503 and SECRET not in response.text + caplog.text
    assert "data-telegram-empty" not in response.text and 'class="telegram-account-revoke"' not in response.text
    private(response)


@pytest.mark.asyncio
async def test_streamed_oversized_form_is_rejected_before_service(api):
    async def chunks():
        yield b"x=" + b"a" * 3000
        yield b"b" * 2000

    async with seeded(api) as client:
        response = await client.post(
            ROOT + "/12/revoke", content=chunks(), headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    assert response.status_code == 422
    api.revoke.assert_not_awaited()
