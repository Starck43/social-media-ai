"""Bitrix24 REST API client, rate-budget limiter, OAuth 2.0 connection manager, and health check (P14-T01).

Implements:
- OAuth 2.0 local portal authorization + callback via `WorkspaceOAuthService` (P13-T08)
  and optional admin-only single-portal incoming webhook fallback (Owner Decision D2).
- Per-portal rate limiter (`<= 2 req/s` per portal, matching `POLICIES['bitrix24']`).
- REST wrapper (`call_method`) and `batch` wrapper (`call_batch`, max 50 commands).
- Error mapping (`expired_token` -> single automatic token refresh; `QUERY_LIMIT_EXCEEDED`
  -> `Bitrix24RateLimitError`; `ACCESS_DENIED` / `insufficient_scope` -> `Bitrix24AccessDeniedError`).
- Health check (`app.info` / `profile`) updating `WorkspaceConnection.state` to
  `verified`, `reauth`, or `error`.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select

from app.core import outbound
from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.catalog import BITRIX24, CATALOG_VERSION
from app.integrations.oauth import (
    OAuthFlowError,
    OAuthNetworkError,
    WorkspaceOAuthService,
    decode_secret_bundle,
    post_oauth_form,
    sanitize_connection_view,
    workspace_oauth,
)
from app.integrations.ratelimit.catalog import POLICIES
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.identity.user import User
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole
from app.types import ActionType
from app.utils.crypto import decrypt_secret, encrypt_secret

from .portal import (
    CLOUD_BITRIX_SUFFIXES,
    InvalidBitrixPortalError,
    build_portal_authorize_url,
    build_portal_rest_url,
    normalize_portal_domain,
)

MAX_BATCH_COMMANDS = 50
BITRIX_RATE_LIMIT_PER_SEC = POLICIES["bitrix24"].rate  # 2 req/s


class Bitrix24Error(RuntimeError):
    """Base value-safe error for Bitrix24 integration operations."""

    def __init__(self, code: str, message: str = "Bitrix24 operation failed", *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class Bitrix24AuthError(Bitrix24Error):
    """Raised when OAuth token or webhook secret is expired, invalid, or revoked."""

    def __init__(self, code: str = "expired_token", message: str = "Bitrix24 authentication failed") -> None:
        super().__init__(code, message, status_code=401)


class Bitrix24RateLimitError(Bitrix24Error):
    """Raised when Bitrix24 returns QUERY_LIMIT_EXCEEDED."""

    def __init__(self, message: str = "Bitrix24 rate limit exceeded") -> None:
        super().__init__("query_limit_exceeded", message, status_code=429)


class Bitrix24AccessDeniedError(Bitrix24Error):
    """Raised when Bitrix24 returns ACCESS_DENIED or insufficient_scope."""

    def __init__(self, message: str = "Bitrix24 access denied") -> None:
        super().__init__("access_denied", message, status_code=403)


class PortalRateGate:
    """Sliding-window per-portal rate limiter enforcing `<= 2 req/s` across concurrent callers."""

    def __init__(
        self,
        *,
        rate_per_sec: int = BITRIX_RATE_LIMIT_PER_SEC,
        window_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._rate = rate_per_sec
        self._window = window_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._locks: dict[str, asyncio.Lock] = {}
        self._timestamps: dict[str, deque[float]] = {}

    async def acquire(self, portal_key: str) -> float:
        key = portal_key.lower().strip()
        lock = self._locks.setdefault(key, asyncio.Lock())
        history = self._timestamps.setdefault(key, deque())
        waited = 0.0
        async with lock:
            while True:
                now = self._clock()
                while history and now - history[0] >= self._window:
                    history.popleft()
                if len(history) < self._rate:
                    history.append(now)
                    return waited
                sleep_for = max(0.001, self._window - (now - history[0]))
                await self._sleeper(sleep_for)
                waited += sleep_for


async def _require_workspace_role(session, tenant_id: int, actor_id: int, *, require_write: bool = True) -> str:
    if is_bypass() or current_tenant_id() != tenant_id:
        raise Bitrix24Error("connection_denied", status_code=403)
    actor = get_current_user()
    if actor is None or getattr(actor, "id", None) != actor_id or not getattr(actor, "is_active", False):
        raise Bitrix24Error("connection_denied", status_code=403)
    user = (
        await session.execute(select(User.id).where(User.id == actor_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    tenant = (
        await session.execute(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
    ).scalar_one_or_none()
    member = (
        await session.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == actor_id,
                TenantUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if user is None or tenant is None or member is None or member.role_id is None:
        raise Bitrix24Error("connection_denied", status_code=403)
    role_code = (
        await session.execute(select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id))
    ).scalar_one_or_none()
    grants = workspace_grants(role_code) if role_code is not None else frozenset()
    if require_write and ("source", ActionType.CREATE) not in grants:
        raise Bitrix24Error("connection_denied", status_code=403)
    if not require_write and ("source", ActionType.VIEW) not in grants:
        raise Bitrix24Error("connection_denied", status_code=403)
    return str(role_code)


async def _load_trusted_registry() -> outbound.RegistryReader:
    from app.models.managers.trusted_outbound_host_manager import load_trusted_registry

    return await load_trusted_registry()


# Code-owned cloud policy. On-premise portals are never listed here: they reach the
# seam only through an installation-approved registry entry (P14-T09).
BITRIX24_STATIC_POLICY = outbound.StaticPolicy(
    ("oauth.bitrix.info", *[f"*{suffix}" for suffix in CLOUD_BITRIX_SUFFIXES])
)


class Bitrix24Service:
    """Bitrix24 OAuth 2.0 connection manager, REST client, batch executor, and health checker.

    Egress goes through `app.core.outbound`: the resolver and the registry loader are
    injected, and the registry is re-read for every operation, so an administrator's
    add or remove applies to the next attempt without a restart.
    """

    def __init__(
        self,
        *,
        oauth_service: WorkspaceOAuthService = workspace_oauth,
        rate_gate: PortalRateGate | None = None,
        resolver: outbound.Resolver = outbound.default_dns_resolver,
        http_client: httpx.AsyncClient | None = None,
        registry_loader: outbound.RegistryLoader = _load_trusted_registry,
    ) -> None:
        self._oauth = oauth_service
        self._rate_gate = rate_gate or PortalRateGate()
        self._resolver = resolver
        self._http_client = http_client
        self._registry_loader = registry_loader

    def _allowed_hosts_for(self, portal_domain: str) -> tuple[str, ...]:
        return BITRIX24_STATIC_POLICY.allowed_hosts

    @staticmethod
    def _portal(raw_domain: str) -> str:
        """Cloud portal, or an on-premise host the installation administrator approved."""
        registry = outbound.current_registry()
        try:
            return normalize_portal_domain(raw_domain, is_trusted_host=lambda host: registry(host) is not None)
        except InvalidBitrixPortalError as exc:
            if exc.code == "untrusted_portal_domain":
                host = raw_domain.strip().lower().removeprefix("https://").rstrip("/")[:253]
                refusal = outbound.OutboundRefusal(outbound.HOST_NOT_TRUSTED, host)
                raise Bitrix24Error(refusal.code, str(refusal), status_code=422) from exc
            raise Bitrix24Error(exc.code, str(exc), status_code=422) from exc

    async def start_oauth_connect(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        portal_domain: str,
        label: str = "",
        member_id: str | None = None,
        scopes: tuple[str, ...] = ("crm", "user"),
        redirect_uri: str = "https://app.example.test/app/settings/oauth/bitrix24/callback",
        session=None,
    ) -> dict[str, Any]:
        async with outbound.registry_scope(self._registry_loader):
            return await self._start_oauth_connect(
                tenant_id=tenant_id,
                actor_id=actor_id,
                portal_domain=portal_domain,
                label=label,
                member_id=member_id,
                scopes=scopes,
                redirect_uri=redirect_uri,
                session=session,
            )

    async def _start_oauth_connect(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        portal_domain: str,
        label: str,
        member_id: str | None,
        scopes: tuple[str, ...],
        redirect_uri: str,
        session,
    ) -> dict[str, Any]:
        normalized_domain = self._portal(portal_domain)

        config_overrides: dict[str, Any] = {
            "portal_domain": normalized_domain,
            "scopes": list(scopes),
        }
        if member_id:
            config_overrides["member_id"] = str(member_id).strip()[:128]

        try:
            return await self._oauth.start_authorization(
                tenant_id=tenant_id,
                actor_id=actor_id,
                provider_id="bitrix24",
                method_id="oauth2",
                account_key=normalized_domain,
                label=label or f"Битрикс24 ({normalized_domain})",
                config_overrides=config_overrides,
                authorize_url_override=build_portal_authorize_url(normalized_domain),
                redirect_uri=redirect_uri,
                session=session,
            )
        except OAuthFlowError as exc:
            raise Bitrix24Error(exc.code, status_code=400) from exc

    async def connect_admin_webhook(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        portal_domain: str,
        webhook_secret: str,
        label: str = "",
        session=None,
    ) -> dict[str, Any]:
        """Admin-only single-portal incoming webhook alternative per Owner Decision D2."""
        async with outbound.registry_scope(self._registry_loader):
            normalized_domain = self._portal(portal_domain)

        if not isinstance(webhook_secret, str) or not webhook_secret.strip():
            raise Bitrix24Error("webhook_secret_required", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.connect_admin_webhook(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    portal_domain=normalized_domain,
                    webhook_secret=webhook_secret,
                    label=label,
                    session=owned,
                )

        role_code = await _require_workspace_role(session, tenant_id, actor_id, require_write=True)
        if role_code not in {"owner", "admin"}:
            raise Bitrix24Error(
                "webhook_admin_only",
                "Incoming webhook connections are restricted to workspace owners and admins (Decision D2).",
                status_code=403,
            )

        row = WorkspaceConnection(
            tenant_id=tenant_id,
            owner_user_id=actor_id,
            platform=BITRIX24.platform,
            provider_id="bitrix24",
            auth_method="webhook_secret",
            schema_version=CATALOG_VERSION,
            label=(label or f"Битрикс24 Webhook ({normalized_domain})").strip()[:100],
            account_key=normalized_domain,
            config={"portal_domain": normalized_domain, "scopes": ["crm"]},
            is_active=True,
            state="unverified",
        )
        session.add(row)
        await session.flush()
        secret_row = WorkspaceConnectionSecret(
            connection_id=row.id,
            secret_encrypted=encrypt_secret(webhook_secret.strip()),
        )
        session.add(secret_row)
        await session.flush()
        return sanitize_connection_view(row)

    async def call_method(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_name: str,
        params: dict[str, Any] | None = None,
        session=None,
        _retried_refresh: bool = False,
    ) -> dict[str, Any]:
        """Execute one Bitrix24 REST method call under the portal rate budget (`<= 2 req/s`)."""
        async with outbound.registry_scope(self._registry_loader):
            return await self._call_method(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                method_name=method_name,
                params=params,
                session=session,
                _retried_refresh=_retried_refresh,
            )

    async def _call_method(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_name: str,
        params: dict[str, Any] | None,
        session,
        _retried_refresh: bool,
    ) -> dict[str, Any]:
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.call_method(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    method_name=method_name,
                    params=params,
                    session=owned,
                )

        await _require_workspace_role(session, tenant_id, actor_id, require_write=False)
        row = (
            await session.execute(
                select(WorkspaceConnection).where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None or not row.is_active or row.provider_id != "bitrix24":
            raise Bitrix24Error("connection_not_found", status_code=404)

        portal_domain = str((row.config or {}).get("portal_domain") or row.account_key)
        # Re-checked per attempt: a host removed from the registry fails closed here.
        self._portal(portal_domain)

        secret_row = await session.get(WorkspaceConnectionSecret, row.id)
        if secret_row is None or not secret_row.secret_encrypted:
            raise Bitrix24AuthError("secret_unavailable")

        if row.auth_method == "oauth2":
            try:
                bundle = decode_secret_bundle(secret_row.secret_encrypted)
            except OAuthFlowError as exc:
                raise Bitrix24AuthError(exc.code) from exc
            access_token = str(bundle["access_token"])
        else:
            access_token = decrypt_secret(secret_row.secret_encrypted)

        await self._rate_gate.acquire(portal_domain)
        url = build_portal_rest_url(portal_domain, method_name)

        form_payload: dict[str, str] = {"auth": access_token}
        for k, v in (params or {}).items():
            if k == "auth":
                continue
            form_payload[str(k)] = str(v)

        try:
            status_code, body = await post_oauth_form(
                url,
                form_payload,
                allowed_hosts=self._allowed_hosts_for(portal_domain),
                resolver=self._resolver,
                http_client=self._http_client,
            )
        except OAuthNetworkError as exc:
            raise Bitrix24Error(exc.code, str(exc.refusal or "Bitrix24 operation failed"), status_code=502) from exc

        error_code = str(body.get("error") or "").strip()
        if error_code in {"expired_token", "invalid_token", "NO_AUTH_FOUND"} or status_code == 401:
            if row.auth_method == "oauth2" and not _retried_refresh:
                try:
                    await self._oauth.refresh_tokens(
                        tenant_id=tenant_id,
                        actor_id=row.owner_user_id,
                        connection_id=row.id,
                        force=True,
                        session=session,
                    )
                except OAuthFlowError as exc:
                    raise Bitrix24AuthError(exc.code) from exc
                return await self.call_method(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    method_name=method_name,
                    params=params,
                    session=session,
                    _retried_refresh=True,
                )
            row.state = "reauth"
            row.check_code = "oauth_reauth_required"
            row.checked_at = datetime.now(timezone.utc)
            await session.flush()
            raise Bitrix24AuthError(error_code or "expired_token")

        if error_code == "QUERY_LIMIT_EXCEEDED" or status_code == 429:
            raise Bitrix24RateLimitError()

        if error_code in {"ACCESS_DENIED", "insufficient_scope"} or status_code == 403:
            raise Bitrix24AccessDeniedError()

        if status_code >= 400 or error_code:
            raise Bitrix24Error(error_code or "bitrix_api_error", status_code=502)

        return body

    async def call_batch(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        commands: dict[str, str],
        halt: int = 0,
        session=None,
    ) -> dict[str, Any]:
        """Execute a Bitrix24 `/rest/batch.json` request (maximum 50 commands per batch)."""
        if not isinstance(commands, dict) or not commands:
            raise Bitrix24Error("batch_empty", "Batch requires at least one command", status_code=422)
        if len(commands) > MAX_BATCH_COMMANDS:
            raise Bitrix24Error(
                "batch_size_exceeded",
                f"Bitrix24 batch supports at most {MAX_BATCH_COMMANDS} commands",
                status_code=422,
            )
        params: dict[str, Any] = {"halt": 1 if halt else 0}
        for key, cmd in commands.items():
            params[f"cmd[{key}]"] = cmd
        return await self.call_method(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            method_name="batch",
            params=params,
            session=session,
        )

    async def check_health(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        session=None,
    ) -> dict[str, Any]:
        """Run `app.info` health probe and update connection state (`verified`, `reauth`, `error`)."""
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.check_health(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    session=owned,
                )

        await _require_workspace_role(session, tenant_id, actor_id, require_write=True)
        row = (
            await session.execute(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.provider_id != "bitrix24":
            raise Bitrix24Error("connection_not_found", status_code=404)

        now = datetime.now(timezone.utc)
        try:
            info = await self.call_method(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                method_name="app.info",
                session=session,
            )
            result_data = info.get("result") if isinstance(info.get("result"), dict) else {}
            member_id = result_data.get("member_id") or info.get("member_id")
            if member_id:
                cfg = dict(row.config or {})
                cfg["member_id"] = str(member_id)[:128]
                row.config = cfg
            row.state = "verified"
            row.check_code = "bitrix_healthy"
            row.checked_at = now
        except Bitrix24AuthError:
            row.state = "reauth"
            row.check_code = "oauth_reauth_required"
            row.checked_at = now
        except Bitrix24Error as exc:
            row.state = "error"
            row.check_code = exc.code[:40]
            row.checked_at = now

        await session.flush()
        return sanitize_connection_view(row)

    async def list_connections(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        session=None,
    ) -> list[dict[str, Any]]:
        if session is None:
            async with async_session_maker() as owned:
                return await self.list_connections(tenant_id=tenant_id, actor_id=actor_id, session=owned)

        await _require_workspace_role(session, tenant_id, actor_id, require_write=False)
        rows = list(
            (
                await session.execute(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.provider_id == "bitrix24",
                    )
                    .order_by(WorkspaceConnection.id)
                )
            )
            .scalars()
            .all()
        )
        return [sanitize_connection_view(r) for r in rows]

    async def delete_connection(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        session=None,
    ) -> dict[str, Any]:
        try:
            return await self._oauth.disconnect(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                session=session,
            )
        except OAuthFlowError as exc:
            raise Bitrix24Error(exc.code, status_code=404 if exc.code == "connection_denied" else 400) from exc


bitrix24_service = Bitrix24Service()
