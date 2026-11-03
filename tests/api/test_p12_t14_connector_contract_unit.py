"""DB-exempt mounted JWT API contracts; ORM/provider/service boundaries are fakes.

No lifespan/startup, database, encryption, provider operation or external network
is allowed. These tests are not transaction or real-provider evidence.
"""

import socket
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Dialect

from app.api.v1 import entry
from app.api.v1.endpoints import connector_accounts as endpoints
from app.core.access_catalog import workspace_grants
from app.core.api_scope import ApiScopeMiddleware
from app.models import User
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.social.telegram_login import LoginRejected
from app.utils.token import create_access_token

ROOT = "/api/v1/connector-accounts"
ATTEMPT = "b1624820-b2a7-4f8a-a4b4-16a316ef5070"
ACCOUNT = {
    "authorization_id": 12,
    "provider_id": "telegram",
    "auth_method": "mtproto",
    "subject_key": "90001",
    "revision": 2,
    "state": "active",
}
GRANT = {"grant_id": 23, "authorization_id": 12, "authorization_revision": 2, "is_active": True}
LOGIN = {
    "attempt_id": ATTEMPT,
    "state": "code",
    "revision": 2,
    "expires_at": datetime(2030, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc),
    "failure_code": None,
    "code_attempts_remaining": 5,
    "password_attempts_remaining": 3,
    "authorization_id": None,
    "grant_id": None,
    "recovery_authorization_id": None,
}
SECRET = "SYNTHETIC-PRIVATE-CONTRACT-SENTINEL"
EXTRA = dict.fromkeys(("session", "secret_encrypted", "phone", "api_hash", "owner_user_id", "tenant_id"), SECRET)
# method, concrete suffix, service method, body, response contract
CASES = [
    ("GET", "", "list_reusable", None, "reusable"),
    ("GET", "/personal", "list_personal", None, "accounts"),
    ("GET", "/grants", "list_grants", None, "grants"),
    ("POST", "/12/attach", "attach", {"expected_revision": 2}, "grant"),
    ("POST", "/grants/23/detach", "detach", None, "grant"),
    ("POST", "/12/revoke", "revoke_personal", {"expected_revision": 2}, "account"),
    ("POST", "/telegram/login", "start", {"request_id": ATTEMPT, "phone": "+12345678901"}, "login"),
    ("GET", f"/telegram/login/{ATTEMPT}", "status", None, "login"),
    ("POST", f"/telegram/login/{ATTEMPT}/code", "submit", {"expected_revision": 2, "code": "12345"}, "login"),
    (
        "POST",
        f"/telegram/login/{ATTEMPT}/password",
        "submit",
        {"expected_revision": 2, "password": "  synthetic password  "},
        "login",
    ),
    ("POST", f"/telegram/login/{ATTEMPT}/cancel", "cancel", None, "login"),
]
CASES.extend(
    [
        (
            "POST",
            "/telegram/reauthorize",
            "start",
            {"request_id": ATTEMPT, "phone": "+12345678901", "authorization_id": 12, "expected_revision": 2},
            "login",
        ),
        ("GET", f"/telegram/login-request/{ATTEMPT}", "status_by_request", None, "login"),
    ]
)

SOURCE = {
    "source_id": 3,
    "name": "synthetic",
    "external_id": "@synthetic",
    "binding_token": "a" * 64,
    "selection_state": "selected",
    "grant_id": 23,
    "authorization_revision": 2,
}
CHOICE = {"grant_id": 23, "authorization_id": 12, "authorization_revision": 2, "subject_key": "90001"}
CASES.extend(
    [
        ("GET", "/telegram/sources", "source_list", None, "catalog"),
        ("GET", "/telegram/sources/3", "source_get", None, "picker"),
        (
            "PUT",
            "/telegram/sources/3",
            "source_set",
            {"expected_binding": "a" * 64, "grant_id": 23, "authorization_id": 12, "authorization_revision": 2},
            "source",
        ),
        ("POST", "/telegram/sources/3/clear", "source_set", {"expected_binding": "a" * 64}, "source"),
    ]
)


def forbidden(*args, **kwargs):
    raise AssertionError("DB/provider/network forbidden in contract unit tests")


@pytest.fixture(autouse=True)
def deny_io(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Dialect, "connect", forbidden)
    from app.services.social import connector_authorizations, telegram_login

    for module in (connector_authorizations, telegram_login):
        for name in ("encrypt_secret", "decrypt_secret", "async_session_maker"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(telegram_login, "telegram_login_step", forbidden)


@pytest.fixture
def api(monkeypatch):
    user = SimpleNamespace(id=7, is_active=True, has_admin_access=lambda: False)
    users = SimpleNamespace()
    users.prefetch_related = lambda *args: users
    users.get = AsyncMock(side_effect=lambda *, id: user if id == 7 else None)
    monkeypatch.setattr(User, "objects", users)
    resolver = AsyncMock(return_value=(12, None, workspace_grants("manager")))
    monkeypatch.setattr(ApiScopeMiddleware, "_resolve_tenant", resolver)
    mocks = {}
    for target, names in (
        (
            endpoints.connector_authorizations,
            ("list_reusable", "list_personal", "list_grants", "attach", "detach", "revoke_personal"),
        ),
        (endpoints.telegram_logins, ("start", "status", "status_by_request", "submit", "cancel")),
    ):
        for name in names:
            mocks[name] = AsyncMock()
            monkeypatch.setattr(target, name, mocks[name])
    for public_name, method in (("source_list", "list_sources"), ("source_get", "get"), ("source_set", "set")):
        mocks[public_name] = AsyncMock()
        monkeypatch.setattr(endpoints.telegram_source_bindings, method, mocks[public_name])
    app = FastAPI()
    app.include_router(entry.router, prefix="/api/v1")
    app.add_middleware(ApiScopeMiddleware)
    return SimpleNamespace(
        app=app,
        mocks=mocks,
        resolver=resolver,
        headers={"Authorization": "Bearer " + create_access_token(subject="7")[0]},
    )


def expected(kind):
    return deepcopy(
        {
            "reusable": {"accounts": [ACCOUNT], "can_attach": True},
            "accounts": [ACCOUNT],
            "grants": [GRANT],
            "account": ACCOUNT,
            "grant": GRANT,
            "login": LOGIN,
            "source": SOURCE,
            "picker": {"source": SOURCE, "choices": [CHOICE], "can_edit": True},
            "catalog": {"sources": [SOURCE], "can_edit": True, "next_after": None},
        }[kind]
    )


def service_result(kind):
    if kind == "reusable":
        return ([{**ACCOUNT, **EXTRA}], True)
    if kind in {"accounts", "grants"}:
        return [{**(ACCOUNT if kind == "accounts" else GRANT), **EXTRA}]
    return {**expected(kind), **EXTRA}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,service,body,kind", CASES)
async def test_all_existing_success_shapes_filter_private_extra_fields(api, method, suffix, service, body, kind):
    api.mocks[service].return_value = service_result(kind)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.request(method, ROOT + suffix, headers=api.headers, json=body)
    want = expected(kind)
    if kind == "login":
        want["expires_at"] = LOGIN["expires_at"].isoformat()
    assert response.status_code == 200 and response.json() == want
    assert SECRET not in response.text
    api.mocks[service].assert_awaited_once()
    if suffix.endswith("/password"):
        assert api.mocks[service].await_args.kwargs["secret"] == "  synthetic password  "
    if suffix in {"/personal", "/12/revoke"}:
        api.resolver.assert_not_awaited()
    else:
        api.resolver.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,service,body,kind", CASES)
@pytest.mark.parametrize("headers", [{}, {"Cookie": "session=synthetic"}])
async def test_each_operation_requires_access_bearer_not_cookie(api, method, suffix, service, body, kind, headers):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.request(method, ROOT + suffix, headers=headers, json=body)
    assert response.status_code == 401
    for target in api.mocks.values():
        target.assert_not_awaited()
    api.resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["processing", "code", "password", "completed", "failed", "expired", "cancelled"])
@pytest.mark.parametrize("offset", [timezone.utc, timezone(timedelta(hours=5, minutes=30))])
async def test_login_200_is_state_metadata_with_original_datetime_spelling(api, state, offset):
    row = {**LOGIN, "state": state, "expires_at": LOGIN["expires_at"].astimezone(offset)}
    if state == "completed":
        row.update(authorization_id=12, grant_id=23)
    if state == "failed":
        row["failure_code"] = "provider_outcome_unknown"
    api.mocks["status"].return_value = row
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.get(ROOT + f"/telegram/login/{ATTEMPT}", headers=api.headers)
    assert response.status_code == 200
    assert response.json() == {**row, "expires_at": row["expires_at"].isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", [{"state": "secret-invalid"}, {"revision": True}, {"attempt_id": SECRET}, {"grant_id": 0}]
)
async def test_invalid_internal_projection_has_fixed_safe_500(api, invalid, caplog):
    api.mocks["status"].return_value = {**LOGIN, **EXTRA, **invalid}
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.get(ROOT + f"/telegram/login/{ATTEMPT}", headers=api.headers)
    assert response.status_code == 500 and response.json() == {"detail": "account_response_unavailable"}
    assert SECRET not in response.text + caplog.text
    assert "secret-invalid" not in response.text + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [
        ("login_conflict", 409),
        ("login_already_pending", 409),
        ("login_rate_limited", 429),
        ("login_configuration_unavailable", 503),
        ("private_login_logging_required", 503),
        ("invalid_login_input", 422),
    ],
)
async def test_service_rejection_status_and_fixed_detail_are_preserved(api, code, status):
    api.mocks["start"].side_effect = LoginRejected(code, status)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/telegram/login", headers=api.headers, json=CASES[6][3])
    assert response.status_code == status and response.json() == {"detail": code}


@pytest.mark.asyncio
async def test_foreign_selection_is_opaque_and_never_echoes_exception(api):
    error = ConnectionDenied()
    error.args = (SECRET,)
    api.mocks["status"].side_effect = error
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.get(ROOT + f"/telegram/login/{ATTEMPT}", headers=api.headers)
    assert response.status_code == 404 and response.json() == {"detail": "connection_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["owner_user_id", "tenant_id", "session", "api_hash"])
async def test_start_extra_fields_rejected_without_call_or_echo(api, field):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post(ROOT + "/telegram/login", headers=api.headers, json={**CASES[6][3], field: SECRET})
    assert response.status_code == 422 and response.json() == {"detail": "invalid_account_selection"}
    assert SECRET not in response.text
    api.mocks["start"].assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_login_still_rejects_without_terminal_or_provider(api, monkeypatch):
    monkeypatch.setattr("builtins.input", forbidden)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="https://testserver") as client:
        response = await client.post("/api/v1/credentials/login", headers=api.headers, json={"platform": "telegram"})
    assert response.status_code == 409
    assert response.json() == {"detail": "workspace_telegram_authorization_not_available"}
    for target in api.mocks.values():
        target.assert_not_awaited()


def test_real_service_projection_keys_match_contracts_without_reading_ciphertext():
    from app.schemas.connector_account import ConnectorAccountMetadata, TelegramLoginMetadata, WorkspaceGrantMetadata
    from app.services.social.connector_authorizations import _account_projection, _grant_projection
    from app.services.social.telegram_login import _projection

    class MetadataRow(SimpleNamespace):
        @property
        def secret_encrypted(self):
            raise AssertionError("projection must not read ciphertext")

    account = MetadataRow(id=12, **{key: value for key, value in ACCOUNT.items() if key != "authorization_id"})
    grant = MetadataRow(id=23, **{key: value for key, value in GRANT.items() if key != "grant_id"})
    login = MetadataRow(
        id=ATTEMPT,
        code_attempts=0,
        password_attempts=0,
        **{
            key: value
            for key, value in LOGIN.items()
            if key not in {"attempt_id", "code_attempts_remaining", "password_attempts_remaining"}
        },
    )
    assert _account_projection(account) == ACCOUNT
    assert _grant_projection(grant) == GRANT
    assert _projection(login) == LOGIN
    assert ConnectorAccountMetadata.model_validate(_account_projection(account)).model_dump(mode="json") == ACCOUNT
    assert WorkspaceGrantMetadata.model_validate(_grant_projection(grant)).model_dump(mode="json") == GRANT
    assert TelegramLoginMetadata.model_validate(_projection(login)).model_dump(mode="json") == {
        **LOGIN,
        "expires_at": LOGIN["expires_at"].isoformat(),
    }


def test_full_application_openapi_has_explicit_metadata_and_legacy_contracts():
    from app.main import create_application

    schema = create_application().openapi()  # metadata only; lifespan is not entered
    paths = schema["paths"]
    models = schema["components"]["schemas"]
    response_models = {
        "reusable": "ReusableConnectorAccounts",
        "accounts": "ConnectorAccountMetadata",
        "account": "ConnectorAccountMetadata",
        "grants": "WorkspaceGrantMetadata",
        "grant": "WorkspaceGrantMetadata",
        "login": "TelegramLoginMetadata",
        "source": "TelegramSourceMetadata",
        "picker": "TelegramSourcePicker",
        "catalog": "TelegramSourceCatalog",
    }
    for method, suffix, _, _, kind in CASES:
        suffix = (
            suffix.replace(ATTEMPT, "{request_id}" if "login-request" in suffix else "{attempt_id}")
            .replace("/12/", "/{authorization_id}/")
            .replace("/23/", "/{grant_id}/")
            .replace("/sources/3", "/sources/{source_id}")
        )
        operation = paths[ROOT + suffix][method.lower()]
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]
        result = operation["responses"]["200"]["content"]["application/json"]["schema"]
        if kind in {"accounts", "grants"}:
            assert result["type"] == "array"
            result = result["items"]
        assert result["$ref"].endswith("/" + response_models[kind])
        for code in ("401", "403", "404", "422", "500"):
            assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ConnectorAccountError"
            )
    for name, keys in (
        ("ConnectorAccountMetadata", ACCOUNT),
        ("WorkspaceGrantMetadata", GRANT),
        ("TelegramLoginMetadata", LOGIN),
        ("ReusableConnectorAccounts", {"accounts": None, "can_attach": None}),
        ("ConnectorAccountError", {"detail": None}),
    ):
        assert set(models[name]["properties"]) == set(keys)
        optional = {"recovery_authorization_id"} if name == "TelegramLoginMetadata" else set()
        assert set(models[name]["required"]) == set(keys) - optional
    assert models["TelegramLoginMetadata"]["properties"]["expires_at"]["format"] == "date-time"
    assert models["TelegramLoginMetadata"]["properties"]["state"]["enum"] == [
        "processing",
        "code",
        "password",
        "completed",
        "failed",
        "expired",
        "cancelled",
    ]
    for name in ("AuthorizationSelection", "TelegramLoginStart", "TelegramCode", "TelegramPassword"):
        assert models[name]["additionalProperties"] is False
    for name, secret in (("TelegramLoginStart", "phone"), ("TelegramCode", "code"), ("TelegramPassword", "password")):
        assert models[name]["properties"][secret]["writeOnly"] is True
    for name in ("AuthorizationSelection", "TelegramCode", "TelegramPassword"):
        assert models[name]["properties"]["expected_revision"]["type"] == "integer"
        assert models[name]["properties"]["expected_revision"]["exclusiveMinimum"] == 0
    for suffix in ("/telegram/login", "/telegram/login/{attempt_id}/code", "/telegram/login/{attempt_id}/password"):
        assert {"409", "503"} <= paths[ROOT + suffix]["post"]["responses"].keys()
    assert "429" in paths[ROOT + "/telegram/login"]["post"]["responses"]
    legacy = paths["/api/v1/credentials/login"]["post"]
    assert legacy["deprecated"] is True and "200" not in legacy["responses"]
    assert legacy["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ConnectorAccountError"
    )
    assert paths["/api/v1/credentials/credentials/{credential_id}/disable"]["patch"]["deprecated"] is True
    assert not paths["/api/v1/credentials/{credential_id}/disable"]["patch"].get("deprecated", False)
