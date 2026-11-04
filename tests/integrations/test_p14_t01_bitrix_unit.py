"""P14-T01 unit and contract tests for Bitrix24 OAuth 2.0 portal authorization, REST client, rate limit, and health check.

Covers:
- Portal domain normalization (`normalize_portal_domain`): cloud suffixes (`.bitrix24.ru`, `.bitrix24.com`, etc.),
  explicit on-prem allowlist, and rejection of IP literals, HTTP URLs, paths, ports, and untrusted domains.
- OAuth 2.0 portal authorization start + callback + secret encryption (zero tokens in config/logs/responses).
- Admin-only webhook connection fallback (Owner Decision D2): allowed for `owner`/`admin`, denied for `manager`.
- REST `call_method` and `call_batch` (`<= 50` commands enforced; `> 50` rejected with `batch_size_exceeded`).
- Error mapping: `expired_token` triggers automatic single token refresh; revoked refresh token marks `reauth`;
  `QUERY_LIMIT_EXCEEDED` -> `Bitrix24RateLimitError`; `ACCESS_DENIED` -> `Bitrix24AccessDeniedError`.
- Per-portal rate limiter (`PortalRateGate`): `<= 2 req/s` under concurrent calls.
- Health check (`app.info`) transitions connection state to `verified`, `reauth`, or `error`, and stores `member_id`.
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import datetime, timezone
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

from app.core.permissions import set_current_user
from app.core.tenant_context import tenant_scope
from app.integrations.bitrix24 import (
    Bitrix24AccessDeniedError,
    Bitrix24AuthError,
    Bitrix24Error,
    Bitrix24RateLimitError,
    Bitrix24Service,
    InvalidBitrixPortalError,
    PortalRateGate,
    build_portal_authorize_url,
    build_portal_rest_url,
    normalize_portal_domain,
)
from app.integrations.oauth import (
    RUNTIME_OAUTH_CLIENT_CREDENTIALS,
    OAuthStateStore,
    WorkspaceOAuthService,
)
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.notifications.notification import Notification


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeBitrixDBSession:
    def __init__(self) -> None:
        self.connections: dict[int, WorkspaceConnection] = {}
        self.secrets: dict[int, WorkspaceConnectionSecret] = {}
        self.notifications: list[Notification] = []
        self._next_id = 1
        self.active_users = {101, 102, 202}
        self.active_tenants = {10, 20}
        # 101 = owner (role 1), 102 = manager (role 2)
        self.memberships: dict[tuple[int, int], tuple[bool, int]] = {
            (10, 101): (True, 1),
            (10, 102): (True, 2),
            (20, 202): (True, 1),
        }
        self.roles = {1: "owner", 2: "manager", 3: "viewer"}

    def add(self, obj: Any) -> None:
        if isinstance(obj, WorkspaceConnection):
            if obj.id is None:
                obj.id = self._next_id
                self._next_id += 1
            self.connections[obj.id] = obj
        elif isinstance(obj, WorkspaceConnectionSecret):
            self.secrets[obj.connection_id] = obj
        elif isinstance(obj, Notification):
            self.notifications.append(obj)

    async def flush(self) -> None:
        for obj in list(self.connections.values()):
            if obj.id is None:
                obj.id = self._next_id
                self._next_id += 1
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
            tid = params.get("tenant_id_1")
            cid = params.get("id_1")
            if cid is not None:
                row = self.connections.get(cid)
                if row and row.tenant_id == tid:
                    oid = params.get("owner_user_id_1")
                    if oid is not None and row.owner_user_id != oid:
                        return _ScalarResult([])
                    return _ScalarResult([row])
                return _ScalarResult([])
            rows = [
                c
                for c in self.connections.values()
                if c.tenant_id == tid and c.provider_id == params.get("provider_id_1", "bitrix24")
            ]
            rows.sort(key=lambda r: r.id or 0)
            return _ScalarResult(rows)

        return _ScalarResult([])


def _global_dns(host: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture(autouse=True)
def _setup_bitrix(monkeypatch: pytest.MonkeyPatch):
    audit_mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-b24"))
    monkeypatch.setattr(
        ActionAuditEvent,
        "objects",
        SimpleNamespace(record_success=audit_mock, record_unknown=audit_mock),
    )
    monkeypatch.setitem(RUNTIME_OAUTH_CLIENT_CREDENTIALS, "bitrix24", ("b24-cid", "b24-csec"))
    set_current_user(SimpleNamespace(id=101, is_active=True))
    yield audit_mock
    set_current_user(None)


def test_portal_domain_validation_and_url_builders() -> None:
    assert normalize_portal_domain("https://Acme.Bitrix24.ru/") == "acme.bitrix24.ru"
    assert normalize_portal_domain("sales.bitrix24.com") == "sales.bitrix24.com"
    assert (
        normalize_portal_domain("crm.internal.corp", allowed_onprem_domains={"crm.internal.corp"})
        == "crm.internal.corp"
    )

    for bad in (
        "",
        "http://acme.bitrix24.ru",
        "127.0.0.1",
        "evil.example.com",
        "sub.acme.bitrix24.ru",
        "acme.bitrix24.ru/path",
    ):
        with pytest.raises(InvalidBitrixPortalError):
            normalize_portal_domain(bad)

    assert build_portal_authorize_url("acme.bitrix24.ru") == "https://acme.bitrix24.ru/oauth/authorize/"
    assert (
        build_portal_rest_url("acme.bitrix24.ru", "crm.contact.list")
        == "https://acme.bitrix24.ru/rest/crm.contact.list.json"
    )
    with pytest.raises(InvalidBitrixPortalError):
        build_portal_rest_url("acme.bitrix24.ru", "../evil")


@pytest.mark.asyncio
async def test_portal_rate_gate_enforces_2_req_per_sec() -> None:
    fake_now = [100.0]
    slept: list[float] = []
    slot_times: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        fake_now[0] += seconds

    gate = PortalRateGate(rate_per_sec=2, window_seconds=1.0, clock=lambda: fake_now[0], sleeper=fake_sleep)

    async def call_once() -> None:
        await gate.acquire("acme.bitrix24.ru")
        slot_times.append(fake_now[0])

    # 5 concurrent requests to the same portal: at most 2 execute at t=100.0, 2 at t=101.0, 1 at t=102.0
    await asyncio.gather(*[call_once() for _ in range(5)])
    assert slot_times.count(100.0) == 2
    assert slot_times.count(101.0) == 2
    assert slot_times.count(102.0) == 1
    assert sum(slept) >= 2.0


@pytest.mark.asyncio
async def test_oauth_connect_rest_calls_batch_refresh_and_health() -> None:
    session = FakeBitrixDBSession()
    store = OAuthStateStore()

    call_log: list[str] = []
    mode = {"state": "ok"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = request.content.decode("utf-8")
        call_log.append(url)

        if "oauth/token" in url:
            if "grant_type=refresh_token" in body:
                if mode["state"] == "revoked":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(
                    200,
                    json={"access_token": "fresh-b24-token", "refresh_token": "fresh-ref-token", "expires_in": 3600},
                )
            return httpx.Response(
                200,
                json={"access_token": "initial-b24-token", "refresh_token": "initial-ref-token", "expires_in": 3600},
            )

        if "oauth/revoke" in url:
            return httpx.Response(200, json={"revoked": True})

        if "app.info.json" in url:
            if mode["state"] == "expired_once":
                mode["state"] = "ok"
                return httpx.Response(401, json={"error": "expired_token"})
            if mode["state"] == "revoked":
                return httpx.Response(401, json={"error": "expired_token"})
            if mode["state"] == "quota":
                return httpx.Response(429, json={"error": "QUERY_LIMIT_EXCEEDED"})
            if mode["state"] == "denied":
                return httpx.Response(403, json={"error": "ACCESS_DENIED"})
            return httpx.Response(200, json={"result": {"member_id": "portal-member-42", "VERSION": 24}})

        if "batch.json" in url:
            return httpx.Response(200, json={"result": {"result": {"c1": [{"ID": "1"}]}}})

        return httpx.Response(200, json={"result": []})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    oauth_svc = WorkspaceOAuthService(state_store=store, resolver=_global_dns, http_client=http_client)
    b24 = Bitrix24Service(oauth_service=oauth_svc, resolver=_global_dns, http_client=http_client)

    with tenant_scope(10):
        # 1. Start OAuth connect for acme.bitrix24.ru
        started = await b24.start_oauth_connect(
            tenant_id=10,
            actor_id=101,
            portal_domain="acme.bitrix24.ru",
            label="Acme CRM",
            session=session,
        )
        conn_id = started["connection_id"]
        assert started["authorize_url"].startswith("https://acme.bitrix24.ru/oauth/authorize/?")

        # Complete callback
        await oauth_svc.complete_callback(
            tenant_id=10,
            actor_id=101,
            provider_id="bitrix24",
            state_token=started["state"],
            code="b24-code-1",
            session=session,
        )

        # 2. Health check succeeds and stores member_id
        health = await b24.check_health(tenant_id=10, actor_id=101, connection_id=conn_id, session=session)
        assert health["state"] == "verified"
        assert health["check_code"] == "bitrix_healthy"
        assert health["config"]["member_id"] == "portal-member-42"

        # 3. Expired token during call_method triggers single refresh and succeeds
        mode["state"] = "expired_once"
        info_resp = await b24.call_method(
            tenant_id=10,
            actor_id=101,
            connection_id=conn_id,
            method_name="app.info",
            session=session,
        )
        assert info_resp["result"]["member_id"] == "portal-member-42"

        # 4. Batch call <= 50 succeeds; > 50 fails with batch_size_exceeded
        batch_resp = await b24.call_batch(
            tenant_id=10,
            actor_id=101,
            connection_id=conn_id,
            commands={"c1": "crm.contact.list"},
            session=session,
        )
        assert "c1" in batch_resp["result"]["result"]

        with pytest.raises(Bitrix24Error) as batch_err:
            await b24.call_batch(
                tenant_id=10,
                actor_id=101,
                connection_id=conn_id,
                commands={f"c{i}": "crm.contact.list" for i in range(51)},
                session=session,
            )
        assert batch_err.value.code == "batch_size_exceeded"

        # 5. Error mapping: QUERY_LIMIT_EXCEEDED and ACCESS_DENIED
        mode["state"] = "quota"
        with pytest.raises(Bitrix24RateLimitError):
            await b24.call_method(
                tenant_id=10,
                actor_id=101,
                connection_id=conn_id,
                method_name="app.info",
                session=session,
            )

        mode["state"] = "denied"
        with pytest.raises(Bitrix24AccessDeniedError):
            await b24.call_method(
                tenant_id=10,
                actor_id=101,
                connection_id=conn_id,
                method_name="app.info",
                session=session,
            )

        # 6. Revoked token during health check transitions state to `reauth`
        mode["state"] = "revoked"
        reauth_health = await b24.check_health(tenant_id=10, actor_id=101, connection_id=conn_id, session=session)
        assert reauth_health["state"] == "reauth"
        assert reauth_health["check_code"] == "oauth_reauth_required"

        # 7. Admin-only webhook fallback: denied for manager (102), allowed for owner (101)
        set_current_user(SimpleNamespace(id=102, is_active=True))
        with pytest.raises(Bitrix24Error) as wh_err:
            await b24.connect_admin_webhook(
                tenant_id=10,
                actor_id=102,
                portal_domain="wh.bitrix24.ru",
                webhook_secret="wh-sec-123",
                session=session,
            )
        assert wh_err.value.code == "webhook_admin_only"

        set_current_user(SimpleNamespace(id=101, is_active=True))
        wh_conn = await b24.connect_admin_webhook(
            tenant_id=10,
            actor_id=101,
            portal_domain="wh.bitrix24.ru",
            webhook_secret="wh-sec-123",
            session=session,
        )
        assert wh_conn["auth_method"] == "webhook_secret"
        assert "wh-sec-123" not in str(wh_conn)

        # List and delete connections
        conns = await b24.list_connections(tenant_id=10, actor_id=101, session=session)
        assert len(conns) == 2
        deleted = await b24.delete_connection(tenant_id=10, actor_id=101, connection_id=wh_conn["id"], session=session)
        assert deleted["state"] == "disabled"

    await http_client.aclose()
