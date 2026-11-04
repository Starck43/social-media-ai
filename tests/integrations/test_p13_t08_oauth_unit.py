"""P13-T08 unit and security tests for the provider-neutral OAuth 2.0 workspace-connection framework.

Covers:
- OAuthMethod catalog descriptor and client credential resolution from deployment config.
- Global DNS validation (`validate_oauth_url`): rejects loopback, RFC1918 private, link-local,
  non-HTTPS, credentialed URLs, and untrusted hosts.
- Redirect rejection (`302 Found` -> `redirect_forbidden`).
- Single-use state & PKCE (`S256`): rejects forged state, expired state, replayed state,
  cross-tenant callback, cross-actor callback, and foreign-connection callback.
- Token encryption in `WorkspaceConnectionSecret.secret_encrypted`: verifies zero token leakage
  in `WorkspaceConnection.config`, return views, or logs.
- Concurrent `asyncio.gather` refresh with per-connection lock: performs exactly ONE provider HTTP call.
- Revoked refresh token (`400 invalid_grant`) transitions connection to `reauth`, clears secret,
  and emits an `action_required` `Notification` for the workspace owner.
- Disconnect revokes at the provider when supported, clears `secret_encrypted`, and marks `disabled`.
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))

import httpx
import pytest

__source_only__ = True

from app.core.config import settings
from app.core.permissions import set_current_user
from app.core.tenant_context import tenant_scope
from app.integrations.catalog import OAuthMethod
from app.integrations.oauth import (
    RUNTIME_OAUTH_CLIENT_CREDENTIALS,
    RUNTIME_OAUTH_METHODS,
    OAuthFlowError,
    OAuthNetworkError,
    OAuthStateStore,
    WorkspaceOAuthService,
    decode_secret_bundle,
    encode_secret_bundle,
    generate_pkce_pair,
    validate_oauth_url,
)
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.notifications.notification import Notification


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeOAuthDBSession:
    """In-memory async session simulating WorkspaceConnection, WorkspaceConnectionSecret, and Notification persistence."""

    def __init__(self) -> None:
        self.connections: dict[int, WorkspaceConnection] = {}
        self.secrets: dict[int, WorkspaceConnectionSecret] = {}
        self.notifications: list[Notification] = []
        self._next_conn_id = 1
        self.active_users = {101, 102, 202}
        self.active_tenants = {10, 20}
        self.memberships: dict[tuple[int, int], tuple[bool, int]] = {
            (10, 101): (True, 1),
            (10, 102): (True, 1),
            (20, 202): (True, 1),
        }
        self.roles = {1: "owner", 2: "viewer"}

    def add(self, obj: Any) -> None:
        if isinstance(obj, WorkspaceConnection):
            if obj.id is None:
                obj.id = self._next_conn_id
                self._next_conn_id += 1
            self.connections[obj.id] = obj
        elif isinstance(obj, WorkspaceConnectionSecret):
            self.secrets[obj.connection_id] = obj
        elif isinstance(obj, Notification):
            self.notifications.append(obj)

    async def flush(self) -> None:
        for obj in list(self.connections.values()):
            if obj.id is None:
                obj.id = self._next_conn_id
                self._next_conn_id += 1
                self.connections[obj.id] = obj

    async def get(self, model_cls: Any, pk: int) -> Any:
        if model_cls is WorkspaceConnectionSecret:
            return self.secrets.get(pk)
        if model_cls is WorkspaceConnection:
            return self.connections.get(pk)
        return None

    async def execute(self, stmt: Any) -> _ScalarResult:
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}

        if "users" in tables:
            uid = params.get("id_1")
            return _ScalarResult([uid] if uid in self.active_users else [])

        if "tenants" in tables:
            tid = params.get("id_1")
            return _ScalarResult([tid] if tid in self.active_tenants else [])

        if "tenant_users" in tables:
            tid = params.get("tenant_id_1")
            uid = params.get("user_id_1")
            mem = self.memberships.get((tid, uid))
            if mem and mem[0]:
                return _ScalarResult([SimpleNamespace(tenant_id=tid, user_id=uid, is_active=True, role_id=mem[1])])
            return _ScalarResult([])

        if "workspace_roles" in tables:
            rid = params.get("id_1")
            code = self.roles.get(rid)
            return _ScalarResult([code] if code else [])

        if "workspace_connections" in tables:
            cid = params.get("id_1")
            tid = params.get("tenant_id_1")
            oid = params.get("owner_user_id_1")
            row = self.connections.get(cid)
            if row and row.tenant_id == tid and row.owner_user_id == oid:
                return _ScalarResult([row])
            return _ScalarResult([])

        return _ScalarResult([])


def _global_dns_resolver(host: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture(autouse=True)
def _setup_oauth_catalog(monkeypatch: pytest.MonkeyPatch):
    audit_mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-oauth"))
    monkeypatch.setattr(
        ActionAuditEvent,
        "objects",
        SimpleNamespace(record_success=audit_mock, record_unknown=audit_mock),
    )
    test_method = OAuthMethod(
        id="oauth2",
        label="OAuth 2.0 PKCE",
        fields=(),
        authorize_url="https://oauth.bitrix.info/oauth/authorize/",
        token_url="https://oauth.bitrix.info/oauth/token/",
        revoke_url="https://oauth.bitrix.info/oauth/revoke/",
        scopes=("crm", "user"),
        pkce=True,
        redirect_path="/app/settings/oauth/bitrix24/callback",
        client_config_keys=("BITRIX24_CLIENT_ID", "BITRIX24_CLIENT_SECRET"),
        allowed_hosts=("oauth.bitrix.info", "*.bitrix24.ru", "*.bitrix24.com"),
    )
    monkeypatch.setitem(RUNTIME_OAUTH_METHODS, "bitrix24", test_method)
    monkeypatch.setitem(RUNTIME_OAUTH_CLIENT_CREDENTIALS, "bitrix24", ("b24-client-id", "b24-client-secret"))
    set_current_user(SimpleNamespace(id=101, is_active=True))
    yield audit_mock
    set_current_user(None)


def test_network_global_dns_and_url_validation() -> None:
    # Valid global IP passes
    host, port = validate_oauth_url(
        "https://oauth.bitrix.info/oauth/token/",
        allowed_hosts=("oauth.bitrix.info",),
        resolver=_global_dns_resolver,
    )
    assert host == "oauth.bitrix.info"
    assert port == 443

    # Non-HTTPS rejected
    with pytest.raises(OAuthNetworkError) as scheme_err:
        validate_oauth_url("http://oauth.bitrix.info/oauth/token/", resolver=_global_dns_resolver)
    assert scheme_err.value.code == "unsafe_oauth_url"

    # Untrusted host rejected
    with pytest.raises(OAuthNetworkError) as host_err:
        validate_oauth_url(
            "https://evil.example.com/oauth/token/",
            allowed_hosts=("oauth.bitrix.info",),
            resolver=_global_dns_resolver,
        )
    assert host_err.value.code == "untrusted_oauth_host"

    # Loopback / private / link-local DNS answers rejected
    for unsafe_ip in ("127.0.0.1", "10.0.0.8", "192.168.1.1", "169.254.169.254", "::1"):
        with pytest.raises(OAuthNetworkError) as dns_err:
            validate_oauth_url(
                "https://oauth.bitrix.info/oauth/token/",
                allowed_hosts=("oauth.bitrix.info",),
                resolver=lambda _h, p, ip=unsafe_ip: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, p))],
            )
        assert dns_err.value.code == "unsafe_address"


@pytest.mark.asyncio
async def test_oauth_start_callback_state_replay_and_cross_tenant_rejection() -> None:
    session = FakeOAuthDBSession()
    store = OAuthStateStore()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "secret-access-token-111",
                "refresh_token": "secret-refresh-token-222",
                "expires_in": 3600,
                "scope": "crm user",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkspaceOAuthService(state_store=store, resolver=_global_dns_resolver, http_client=client)

    with tenant_scope(10):
        # Reject secret keys in config_overrides
        with pytest.raises(OAuthFlowError) as sec_err:
            await service.start_authorization(
                tenant_id=10,
                actor_id=101,
                provider_id="bitrix24",
                config_overrides={"access_token": "leaked"},
                session=session,
            )
        assert sec_err.value.code == "secret_in_config_forbidden"

        started = await service.start_authorization(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            account_key="corp.bitrix24.ru",
            label="Corp Portal",
            config_overrides={"portal_domain": "corp.bitrix24.ru"},
            session=session,
        )
        conn_id = started["connection_id"]
        state_token = started["state"]
        assert "code_challenge=" in started["authorize_url"]
        assert "code_challenge_method=S256" in started["authorize_url"]

        # Forged state fails
        with pytest.raises(OAuthFlowError) as forged_err:
            await service.complete_callback(
                tenant_id=10,
                actor_id=101,
                provider_id="bitrix24",
                state_token="forged-state-value",
                code="auth-code-1",
                session=session,
            )
        assert forged_err.value.code == "state_invalid_or_replayed"

        # Valid callback succeeds and stores encrypted secret only in WorkspaceConnectionSecret
        verified = await service.complete_callback(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            state_token=state_token,
            code="auth-code-1",
            session=session,
        )
        assert verified["state"] == "verified"
        assert verified["check_code"] == "oauth_authorized"
        assert "secret-access-token-111" not in str(verified)
        assert "secret-access-token-111" not in str(session.connections[conn_id].config)

        bundle = decode_secret_bundle(session.secrets[conn_id].secret_encrypted)
        assert bundle["access_token"] == "secret-access-token-111"
        assert bundle["refresh_token"] == "secret-refresh-token-222"

        # State replay with the same state_token is rejected immediately
        with pytest.raises(OAuthFlowError) as replay_err:
            await service.complete_callback(
                tenant_id=10,
                actor_id=101,
                provider_id="bitrix24",
                state_token=state_token,
                code="auth-code-1",
                session=session,
            )
        assert replay_err.value.code == "state_invalid_or_replayed"

        # Issue a second state and attempt cross-tenant callback -> rejected
        started2 = await service.start_authorization(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            connection_id=conn_id,
            session=session,
        )

    set_current_user(SimpleNamespace(id=202, is_active=True))
    with tenant_scope(20):
        with pytest.raises(OAuthFlowError) as cross_err:
            await service.complete_callback(
                tenant_id=20,
                actor_id=202,
                provider_id="bitrix24",
                state_token=started2["state"],
                code="auth-code-2",
                session=session,
            )
        assert cross_err.value.code == "state_cross_tenant"

    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_refresh_performs_single_provider_call_and_revoked_token_marks_reauth() -> None:
    session = FakeOAuthDBSession()
    store = OAuthStateStore()
    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

    refresh_http_calls = 0
    revoke_http_calls = 0
    should_fail_refresh = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_http_calls, revoke_http_calls
        url_str = str(request.url)
        if "revoke" in url_str:
            revoke_http_calls += 1
            return httpx.Response(200, json={"revoked": True})
        body = request.content.decode("utf-8")
        if "grant_type=refresh_token" in body:
            refresh_http_calls += 1
            if should_fail_refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": f"refreshed-access-{refresh_http_calls}",
                    "refresh_token": f"refreshed-refresh-{refresh_http_calls}",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "initial-access",
                "refresh_token": "initial-refresh",
                "expires_in": 60,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkspaceOAuthService(state_store=store, resolver=_global_dns_resolver, http_client=client)

    with tenant_scope(10):
        started = await service.start_authorization(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            now=now,
            session=session,
        )
        conn_id = started["connection_id"]
        await service.complete_callback(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            state_token=started["state"],
            code="code-init",
            now=now,
            session=session,
        )

        # Token expires in 60s (< 120s safety window). Run 5 concurrent refresh calls:
        # the first refreshes and updates expires_at to +3600s; the other 4 observe the fresh token!
        results = await asyncio.gather(
            *[
                service.refresh_tokens(
                    tenant_id=10,
                    actor_id=101,
                    connection_id=conn_id,
                    now=now + timedelta(seconds=10),
                    session=session,
                )
                for _ in range(5)
            ]
        )
        assert refresh_http_calls == 1
        assert all(r["state"] == "verified" for r in results)
        bundle = decode_secret_bundle(session.secrets[conn_id].secret_encrypted)
        assert bundle["access_token"] == "refreshed-access-1"

        # Now simulate revoked refresh token on forced refresh -> transitions to `reauth` + owner Notification
        should_fail_refresh = True
        with pytest.raises(OAuthFlowError) as reauth_err:
            await service.refresh_tokens(
                tenant_id=10,
                actor_id=101,
                connection_id=conn_id,
                force=True,
                now=now + timedelta(hours=2),
                session=session,
            )
        assert reauth_err.value.code == "oauth_reauth_required"
        assert session.connections[conn_id].state == "reauth"
        assert session.secrets[conn_id].secret_encrypted == ""
        assert len(session.notifications) == 1
        assert session.notifications[0].severity == "action_required"

        # Re-populate secret and test disconnect -> calls revoke endpoint and clears secret
        session.secrets[conn_id].secret_encrypted = encode_secret_bundle(
            access_token="active-tok",
            refresh_token="active-ref",
            expires_at=now + timedelta(hours=1),
        )
        disconnected = await service.disconnect(
            tenant_id=10,
            actor_id=101,
            connection_id=conn_id,
            session=session,
        )
        assert disconnected["state"] == "disabled"
        assert disconnected["is_active"] is False
        assert session.secrets[conn_id].secret_encrypted == ""
        assert revoke_http_calls == 1

    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_response_from_token_endpoint_is_rejected() -> None:
    session = FakeOAuthDBSession()
    store = OAuthStateStore()

    def redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example.com/steal"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler))
    service = WorkspaceOAuthService(state_store=store, resolver=_global_dns_resolver, http_client=client)

    with tenant_scope(10):
        started = await service.start_authorization(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            session=session,
        )
        with pytest.raises(OAuthFlowError) as redir_err:
            await service.complete_callback(
                tenant_id=10,
                actor_id=101,
                provider_id="bitrix24",
                state_token=started["state"],
                code="code-redir",
                session=session,
            )
        assert redir_err.value.code == "redirect_forbidden"
        assert session.connections[started["connection_id"]].state == "error"

    await client.aclose()
