"""Mounted browser forms: real CSRF/routes/templates, fake service and identity edges."""

import socket
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Dialect
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.admin.csrf import CSRFTokenManager
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.services.social.connector_authorizations import connector_authorizations
from app.services.social.telegram_login import telegram_logins
from app.services.social.telegram_source_bindings import telegram_source_bindings
from app.web.connector_accounts import router as accounts_router
from app.web.telegram_login import router as login_router

BASE = "/app/settings/telegram-accounts/workspace"
ATTEMPT = "b1624820-b2a7-4f8a-a4b4-16a316ef5070"
SECRET = "SYNTHETIC-NEVER-ECHO"
SOURCE = dict(
    source_id=3,
    name="<synthetic>",
    external_id="@synthetic",
    binding_token="a" * 64,
    selection_state="unselected",
    grant_id=None,
    authorization_revision=None,
)
CHOICE = dict(grant_id=4, authorization_id=12, authorization_revision=2, subject_key="90001")


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms, self.action = {}, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.action = attrs["action"]
            self.forms[self.action] = {}
        elif tag in ("input", "button") and self.action and attrs.get("name"):
            self.forms[self.action][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.action = None


def metadata(state="code", **values):
    return dict(
        attempt_id=ATTEMPT,
        state=state,
        revision=2,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        failure_code=None,
        code_attempts_remaining=5,
        password_attempts_remaining=3,
        authorization_id=None,
        grant_id=None,
        recovery_authorization_id=None,
        **values,
    )


@pytest.fixture
def env(monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError("DB/provider/network forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_io)
    monkeypatch.setattr(socket, "getaddrinfo", no_io)
    monkeypatch.setattr(Dialect, "connect", no_io)
    from app.services.social import telegram_login

    monkeypatch.setattr(telegram_login, "telegram_login_step", no_io)
    monkeypatch.setattr(telegram_login, "async_session_maker", no_io)
    mocks = NS(
        start=AsyncMock(return_value=metadata()),
        status=AsyncMock(return_value=metadata()),
        status_by_request=AsyncMock(return_value=metadata()),
        submit=AsyncMock(return_value=metadata()),
        cancel=AsyncMock(return_value=metadata("cancelled")),
        get=AsyncMock(return_value={"source": SOURCE, "choices": [CHOICE], "can_edit": True}),
        set=AsyncMock(
            return_value={**SOURCE, "selection_state": "selected", "grant_id": 4, "authorization_revision": 2}
        ),
    )
    for key in ("start", "status", "status_by_request", "submit", "cancel"):
        monkeypatch.setattr(telegram_logins, key, getattr(mocks, key))
    for key in ("get", "set"):
        monkeypatch.setattr(telegram_source_bindings, key, getattr(mocks, key))
    monkeypatch.setattr(connector_authorizations, "list_reusable", AsyncMock(return_value=([], True)))
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(accounts_router, prefix="/app")
    app.include_router(login_router, prefix="/app")
    app.state.csrf_manager = CSRFTokenManager("synthetic-csrf-secret")

    @app.middleware("http")
    async def identities(request, call_next):
        actor = request.session.get("actor", 7)
        tenant = request.session.get("tenant", 1)
        user = NS(id=actor, username="synthetic", is_active=True, role=None, has_admin_access=lambda: False)
        request.state.web_user = user
        request.state.tenant_id = tenant
        with tenant_scope(tenant), permission_scope(user):
            return await call_next(request)

    @app.get("/__seed")
    async def seed(request: Request, actor: int = 7, tenant: int = 1):
        request.session.update(actor=actor, tenant=tenant)
        return PlainTextResponse("ok")

    app.add_middleware(SessionMiddleware, secret_key="synthetic-browser-session", https_only=True)
    return NS(app=app, mocks=mocks)


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert SECRET not in response.text


async def fields(client, path, action=None):
    response = await client.get(path)
    assert response.status_code == 200
    private(response)
    return Forms(response.text).forms[action or path]


@pytest.mark.parametrize("recovery", [False, True])
async def test_start_and_recovery_bound_to_explicit_payload_and_no_echo(env, recovery):
    path = BASE + "/login"
    query = "?authorization_id=12&expected_revision=2" if recovery else ""
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path + query, path)
        form.update(phone="+12345678901", confirm="begin-login")
        response = await client.post(path, data=form)
    assert response.status_code == 303 and response.headers["location"].endswith(ATTEMPT)
    private(response)
    assert env.mocks.start.await_args.kwargs["authorization_id"] == (12 if recovery else None)
    assert env.mocks.start.await_args.kwargs["expected_revision"] == (2 if recovery else None)


@pytest.mark.parametrize("change", ["target", "request", "workspace", "actor", "session", "expired"])
async def test_start_csrf_fences_scope_target_request_and_revision(env, change):
    path = BASE + "/login"
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path, path)
        form.update(phone="+12345678901", confirm="begin-login")
        if change == "target":
            form.update(authorization_id="12", expected_revision="2")
        elif change == "request":
            form["request_id"] = ATTEMPT
        elif change == "workspace":
            await client.get("/__seed?tenant=2")
        elif change == "actor":
            await client.get("/__seed?actor=8")
        elif change == "session":
            client.cookies.clear()
        else:
            env.app.state.csrf_manager.expire_seconds = -1
        response = await client.post(path, data=form)
    assert response.status_code == 403
    private(response)
    env.mocks.start.assert_not_awaited()


@pytest.mark.parametrize("operation", ["code", "password"])
async def test_secret_steps_are_private_and_revision_bound(env, operation):
    env.mocks.status.return_value = metadata(operation)
    path = BASE + "/login/" + ATTEMPT
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path, path + "/" + operation)
        form["secret"] = "12345" if operation == "code" else SECRET
        response = await client.post(path + "/" + operation, data=form)
    assert response.status_code == 303
    private(response)
    env.mocks.submit.assert_awaited_once_with(
        tenant_id=1, actor_id=7, attempt_id=ATTEMPT, expected_revision=2, operation=operation, secret=form["secret"]
    )


async def test_step_unknown_outcome_returns_no_secret_and_does_not_auto_retry(env):
    env.mocks.submit.side_effect = RuntimeError(SECRET)
    path = BASE + "/login/" + ATTEMPT
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path, path + "/code")
        form["secret"] = "12345"
        response = await client.post(path + "/code", data=form)
    assert response.status_code == 503
    private(response)
    assert "12345" not in response.text
    assert env.mocks.submit.await_count == 1


async def test_unknown_start_has_status_only_recovery_link(env):
    env.mocks.start.side_effect = RuntimeError(SECRET)
    path = BASE + "/login"
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path)
        form.update(phone="+12345678901", confirm="begin-login")
        result = await client.post(path, data=form)
        recovered = await client.get(path + "/request/" + form["request_id"])
    assert result.status_code == 503 and "/request/" + form["request_id"] in result.text
    assert recovered.status_code == 303
    assert env.mocks.start.await_count == 1
    env.mocks.status_by_request.assert_awaited_once_with(tenant_id=1, actor_id=7, request_id=form["request_id"])
    private(result)


async def test_picker_binds_exact_own_grant_and_preserves_secret_projection(env):
    path = BASE + "/sources/3"
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path, path + "/bind")
        form.update(grant_choice="4:12:2", confirm="bind-source")
        response = await client.post(path + "/bind", data=form)
    assert response.status_code == 303
    env.mocks.set.assert_awaited_once_with(
        tenant_id=1,
        actor_id=7,
        source_id=3,
        expected_binding="a" * 64,
        grant_id=4,
        authorization_id=12,
        authorization_revision=2,
    )
    private(response)


@pytest.mark.parametrize("field", ["source", "workspace", "revision", "duplicate", "json", "oversized"])
async def test_picker_forged_and_malformed_forms_do_not_mutate(env, field):
    path = BASE + "/sources/3"
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="https://testserver") as client:
        form = await fields(client, path, path + "/bind")
        form.update(grant_choice="4:12:2", confirm="bind-source")
        target = path + "/bind"
        expected = 422
        if field == "source":
            target = BASE + "/sources/4/bind"
            expected = 403
        elif field == "workspace":
            await client.get("/__seed?tenant=2")
            expected = 403
        elif field == "revision":
            form["grant_choice"] = "4:12:02"
        if field == "duplicate":
            from urllib.parse import urlencode

            result = await client.post(
                target,
                content=urlencode(form) + "&confirm=bind-source",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        elif field == "json":
            result = await client.post(target, json=form)
        elif field == "oversized":
            result = await client.post(target, data={"secret": SECRET * 4096})
        else:
            result = await client.post(target, data=form)
    assert result.status_code == expected
    private(result)
    env.mocks.set.assert_not_awaited()
