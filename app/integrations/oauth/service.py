"""Provider-neutral OAuth 2.0 workspace-connection service (P13-T08).

Responsibilities:
1. Start flow: validates workspace member permissions (`source.create`), creates or locks
   the target `WorkspaceConnection` (`state='unverified'`), issues a single-use state + PKCE
   challenge, and builds the provider authorization URL using deployment-configured `client_id`.
2. Callback flow: consumes the single-use `state`, verifies global DNS and allowlisted host,
   exchanges the authorization `code` (+ PKCE `code_verifier`) for tokens, encrypts the token
   bundle exclusively inside `WorkspaceConnectionSecret.secret_encrypted` via `encrypt_secret`,
   and transitions `WorkspaceConnection.state` to `'verified'`.
3. Refresh flow: serializes concurrent refresh attempts per connection via a per-connection
   async lock + DB `SELECT ... FOR UPDATE`, skipping redundant HTTP calls if a concurrent
   coroutine just refreshed the token. On refresh failure (`invalid_grant` / `400` / `401`),
   transitions the connection to `'reauth'` and notifies the connection owner.
4. Disconnect flow: revokes the token at the provider when `revoke_url` is configured, clears
   `WorkspaceConnectionSecret.secret_encrypted`, and transitions the connection to `'disabled'`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.catalog import CATALOG_VERSION, PROVIDERS, OAuthMethod
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.identity.user import User
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.notifications.notification import Notification
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole
from app.types import ActionType, NotificationType
from app.utils.crypto import decrypt_secret, encrypt_secret

from .credentials import BITRIX24_PROVIDER_ID, resolve_app_credentials
from .network import OAuthNetworkError, ResolverCallable, default_dns_resolver, post_oauth_form, validate_oauth_url
from .state import DEFAULT_STATE_TTL_SECONDS, OAuthPendingState, OAuthStateError, OAuthStateStore, oauth_state_store

REFRESH_SAFETY_WINDOW_SECONDS = 120


class OAuthFlowError(RuntimeError):
    """Value-safe OAuth flow exception."""

    def __init__(self, code: str, message: str = "OAuth operation failed") -> None:
        super().__init__(message)
        self.code = code


# Runtime client credential registry for providers (populated from deployment env or test fixtures, never forms).
RUNTIME_OAUTH_CLIENT_CREDENTIALS: dict[str, tuple[str, str]] = {}
# Runtime OAuthMethod overrides/registrations by provider_id.
RUNTIME_OAUTH_METHODS: dict[str, OAuthMethod] = {}


def resolve_oauth_method(provider_id: str, method_id: str = "oauth2") -> OAuthMethod:
    override = RUNTIME_OAUTH_METHODS.get(provider_id)
    if override is not None and override.id == method_id:
        return override
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise OAuthFlowError("unsupported_provider")
    for method in provider.methods:
        if isinstance(method, OAuthMethod) and method.id == method_id:
            return method
    raise OAuthFlowError("unsupported_oauth_method")


def resolve_client_credentials(method: OAuthMethod, provider_id: str) -> tuple[str, str]:
    # Global applications (P14-T05) resolve from the operator's secret source, never from
    # `client_config_keys`; a missing source fails closed with `bitrix_credentials_unavailable`.
    if provider_id == BITRIX24_PROVIDER_ID:
        return resolve_app_credentials(provider_id)
    if provider_id in RUNTIME_OAUTH_CLIENT_CREDENTIALS:
        cid, csec = RUNTIME_OAUTH_CLIENT_CREDENTIALS[provider_id]
        if cid and csec:
            return cid, csec
    id_key, secret_key = method.client_config_keys
    client_id = str(getattr(settings, id_key, "") or "").strip() if id_key else ""
    client_secret = str(getattr(settings, secret_key, "") or "").strip() if secret_key else ""
    if not client_id or not client_secret:
        raise OAuthFlowError("oauth_client_not_configured")
    return client_id, client_secret


def encode_secret_bundle(
    *,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime,
    scope: str = "",
    token_type: str = "Bearer",
) -> str:
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": expires_at.isoformat(),
        "scope": scope,
        "token_type": token_type,
    }
    return encrypt_secret(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def decode_secret_bundle(ciphertext: str) -> dict[str, Any]:
    if not ciphertext:
        raise OAuthFlowError("secret_unavailable")
    try:
        raw = decrypt_secret(ciphertext)
        data = json.loads(raw)
    except (ValueError, RuntimeError, TypeError) as exc:
        raise OAuthFlowError("secret_unavailable") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise OAuthFlowError("secret_unavailable")
    return data


def sanitize_connection_view(row: WorkspaceConnection) -> dict[str, Any]:
    """Return a value-safe connection representation guaranteed to contain zero token material."""
    safe_config = {
        k: v
        for k, v in (row.config or {}).items()
        if "token" not in k.lower() and "secret" not in k.lower() and "password" not in k.lower()
    }
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "owner_user_id": row.owner_user_id,
        "platform": row.platform,
        "provider_id": row.provider_id,
        "auth_method": row.auth_method,
        "label": row.label,
        "account_key": row.account_key,
        "config": safe_config,
        "is_active": row.is_active,
        "state": row.state,
        "check_code": row.check_code,
        "checked_at": row.checked_at,
    }


async def _authorize_member(session, tenant_id: int, actor_id: int) -> None:
    if is_bypass() or current_tenant_id() != tenant_id:
        raise OAuthFlowError("connection_denied")
    actor = get_current_user()
    if actor is None or getattr(actor, "id", None) != actor_id or not getattr(actor, "is_active", False):
        raise OAuthFlowError("connection_denied")
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
        raise OAuthFlowError("connection_denied")
    role_code = (
        await session.execute(select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id))
    ).scalar_one_or_none()
    grants = workspace_grants(role_code) if role_code is not None else frozenset()
    if ("source", ActionType.CREATE) not in grants:
        raise OAuthFlowError("connection_denied")


class WorkspaceOAuthService:
    """Provider-neutral OAuth 2.0 authorization code + PKCE + refresh manager."""

    def __init__(
        self,
        *,
        state_store: OAuthStateStore = oauth_state_store,
        resolver: ResolverCallable = default_dns_resolver,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._state_store = state_store
        self._resolver = resolver
        self._http_client = http_client
        self._refresh_locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, connection_id: int) -> asyncio.Lock:
        lock = self._refresh_locks.get(connection_id)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[connection_id] = lock
        return lock

    async def start_authorization(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        provider_id: str,
        method_id: str = "oauth2",
        account_key: str = "default",
        label: str = "",
        config_overrides: dict[str, Any] | None = None,
        authorize_url_override: str | None = None,
        redirect_uri: str = "https://app.example.test/app/settings/oauth/callback",
        connection_id: int | None = None,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        method = resolve_oauth_method(provider_id, method_id)
        client_id, _ = resolve_client_credentials(method, provider_id)
        auth_url_base = authorize_url_override or method.authorize_url
        validate_oauth_url(auth_url_base, allowed_hosts=method.allowed_hosts, resolver=self._resolver)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.start_authorization(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    provider_id=provider_id,
                    method_id=method_id,
                    account_key=account_key,
                    label=label,
                    config_overrides=config_overrides,
                    authorize_url_override=authorize_url_override,
                    redirect_uri=redirect_uri,
                    connection_id=connection_id,
                    now=now,
                    session=owned,
                )

        await _authorize_member(session, tenant_id, actor_id)
        provider = PROVIDERS[provider_id]
        clean_config = dict(provider.defaults)
        for k, v in (config_overrides or {}).items():
            if "token" in k.lower() or "secret" in k.lower() or "password" in k.lower():
                raise OAuthFlowError("secret_in_config_forbidden")
            clean_config[k] = v

        if connection_id is None:
            row = WorkspaceConnection(
                tenant_id=tenant_id,
                owner_user_id=actor_id,
                platform=provider.platform,
                provider_id=provider_id,
                auth_method=method.id,
                schema_version=CATALOG_VERSION,
                label=(label or provider.label).strip()[:100],
                account_key=account_key.strip().casefold()[:320] or "default",
                config=clean_config,
                is_active=True,
                state="unverified",
            )
            session.add(row)
            await session.flush()
            secret_row = WorkspaceConnectionSecret(connection_id=row.id, secret_encrypted="")
            session.add(secret_row)
            await session.flush()
        else:
            row = (
                await session.execute(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.id == connection_id,
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.owner_user_id == actor_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.provider_id != provider_id:
                raise OAuthFlowError("connection_denied")
            row.config = clean_config
            row.state = "unverified"
            row.is_active = True

        pending: OAuthPendingState = self._state_store.issue(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            provider_id=provider_id,
            connection_id=row.id,
            redirect_uri=redirect_uri,
            use_pkce=method.pkce,
            ttl_seconds=DEFAULT_STATE_TTL_SECONDS,
            now=now,
        )

        query_params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": pending.state_token,
        }
        if method.scopes:
            query_params["scope"] = " ".join(method.scopes)
        if pending.code_challenge:
            query_params["code_challenge"] = pending.code_challenge
            query_params["code_challenge_method"] = "S256"

        separator = "&" if "?" in auth_url_base else "?"
        full_authorize_url = f"{auth_url_base}{separator}{urlencode(query_params)}"
        return {
            "connection_id": row.id,
            "authorize_url": full_authorize_url,
            "state": pending.state_token,
            "connection": sanitize_connection_view(row),
        }

    async def complete_callback(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        provider_id: str,
        state_token: str,
        code: str,
        method_id: str = "oauth2",
        expected_connection_id: int | None = None,
        token_url_override: str | None = None,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise OAuthFlowError("missing_authorization_code")

        current_time = now or datetime.now(timezone.utc)
        try:
            pending = self._state_store.consume(
                state_token,
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                provider_id=provider_id,
                expected_connection_id=expected_connection_id,
                now=current_time,
            )
        except OAuthStateError as exc:
            raise OAuthFlowError(exc.code) from exc

        method = resolve_oauth_method(provider_id, method_id)
        client_id, client_secret = resolve_client_credentials(method, provider_id)
        token_url = token_url_override or method.token_url

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self._exchange_and_store(
                    session=owned,
                    pending=pending,
                    method=method,
                    provider_id=provider_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    token_url=token_url,
                    code=code.strip(),
                    now=current_time,
                )

        return await self._exchange_and_store(
            session=session,
            pending=pending,
            method=method,
            provider_id=provider_id,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            code=code.strip(),
            now=current_time,
        )

    async def _exchange_and_store(
        self,
        *,
        session,
        pending: OAuthPendingState,
        method: OAuthMethod,
        provider_id: str,
        client_id: str,
        client_secret: str,
        token_url: str,
        code: str,
        now: datetime,
    ) -> dict[str, Any]:
        await _authorize_member(session, pending.tenant_id, pending.actor_user_id)
        row = (
            await session.execute(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.id == pending.connection_id,
                    WorkspaceConnection.tenant_id == pending.tenant_id,
                    WorkspaceConnection.owner_user_id == pending.actor_user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or not row.is_active or row.provider_id != provider_id:
            raise OAuthFlowError("connection_denied")

        form_data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": pending.redirect_uri,
        }
        if pending.code_verifier:
            form_data["code_verifier"] = pending.code_verifier

        try:
            status_code, token_payload = await post_oauth_form(
                token_url,
                form_data,
                allowed_hosts=method.allowed_hosts,
                resolver=self._resolver,
                http_client=self._http_client,
            )
        except OAuthNetworkError as exc:
            row.state = "error"
            row.check_code = exc.code
            row.checked_at = now
            await session.flush()
            raise OAuthFlowError(exc.code) from exc

        access_token = str(token_payload.get("access_token") or "").strip()
        refresh_token = str(token_payload.get("refresh_token") or "").strip()
        expires_in = int(token_payload.get("expires_in") or 3600)
        if status_code >= 400 or not access_token:
            row.state = "error"
            row.check_code = "oauth_exchange_failed"
            row.checked_at = now
            await session.flush()
            raise OAuthFlowError("oauth_exchange_failed")

        secret_row = await session.get(WorkspaceConnectionSecret, row.id)
        if secret_row is None:
            secret_row = WorkspaceConnectionSecret(connection_id=row.id, secret_encrypted="")
            session.add(secret_row)

        expires_at = now + timedelta(seconds=max(60, expires_in))
        secret_row.secret_encrypted = encode_secret_bundle(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=str(token_payload.get("scope") or ""),
            token_type=str(token_payload.get("token_type") or "Bearer"),
        )
        row.state = "verified"
        row.check_code = "oauth_authorized"
        row.checked_at = now
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=pending.actor_user_id,
            action="connector.authorize",
            resource_kind="connection",
            resource_id=row.id,
        )
        return sanitize_connection_view(row)

    async def refresh_tokens(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_id: str = "oauth2",
        token_url_override: str | None = None,
        force: bool = False,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Serialize token refresh per connection so concurrent callers perform at most one HTTP call."""
        lock = self._lock_for(connection_id)
        async with lock:
            if session is None:
                async with async_session_maker() as owned, owned.begin():
                    return await self._refresh_locked(
                        session=owned,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        connection_id=connection_id,
                        method_id=method_id,
                        token_url_override=token_url_override,
                        force=force,
                        now=now or datetime.now(timezone.utc),
                    )
            return await self._refresh_locked(
                session=session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                method_id=method_id,
                token_url_override=token_url_override,
                force=force,
                now=now or datetime.now(timezone.utc),
            )

    async def _refresh_locked(
        self,
        *,
        session,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_id: str,
        token_url_override: str | None,
        force: bool,
        now: datetime,
    ) -> dict[str, Any]:
        await _authorize_member(session, tenant_id, actor_id)
        row = (
            await session.execute(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == tenant_id,
                    WorkspaceConnection.owner_user_id == actor_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or not row.is_active:
            raise OAuthFlowError("connection_denied")

        secret_row = await session.get(WorkspaceConnectionSecret, row.id)
        bundle = decode_secret_bundle(secret_row.secret_encrypted if secret_row else "")

        expires_at = datetime.fromisoformat(bundle["expires_at"])
        if not force and expires_at > now + timedelta(seconds=REFRESH_SAFETY_WINDOW_SECONDS):
            # Already refreshed by an earlier concurrent caller holding the lock!
            return sanitize_connection_view(row)

        refresh_token = str(bundle.get("refresh_token") or "").strip()
        if not refresh_token:
            await self._mark_reauth(session, row, secret_row, now)
            raise OAuthFlowError("oauth_reauth_required")

        method = resolve_oauth_method(row.provider_id, method_id)
        client_id, client_secret = resolve_client_credentials(method, row.provider_id)
        token_url = token_url_override or method.token_url

        try:
            status_code, payload = await post_oauth_form(
                token_url,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                allowed_hosts=method.allowed_hosts,
                resolver=self._resolver,
                http_client=self._http_client,
            )
        except OAuthNetworkError as exc:
            row.state = "error"
            row.check_code = exc.code
            row.checked_at = now
            await session.flush()
            raise OAuthFlowError(exc.code) from exc

        new_access = str(payload.get("access_token") or "").strip()
        if (
            status_code in (400, 401)
            or payload.get("error") in ("invalid_grant", "unauthorized_client")
            or not new_access
        ):
            await self._mark_reauth(session, row, secret_row, now)
            raise OAuthFlowError("oauth_reauth_required")

        new_refresh = str(payload.get("refresh_token") or refresh_token).strip()
        expires_in = int(payload.get("expires_in") or 3600)
        secret_row.secret_encrypted = encode_secret_bundle(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=now + timedelta(seconds=max(60, expires_in)),
            scope=str(payload.get("scope") or bundle.get("scope") or ""),
            token_type=str(payload.get("token_type") or bundle.get("token_type") or "Bearer"),
        )
        row.state = "verified"
        row.check_code = "oauth_refreshed"
        row.checked_at = now
        await session.flush()
        return sanitize_connection_view(row)

    @staticmethod
    async def _mark_reauth(
        session,
        row: WorkspaceConnection,
        secret_row: WorkspaceConnectionSecret | None,
        now: datetime,
    ) -> None:
        row.state = "reauth"
        row.check_code = "oauth_reauth_required"
        row.checked_at = now
        if secret_row is not None:
            secret_row.secret_encrypted = ""
        notif = Notification(
            tenant_id=row.tenant_id,
            title=f"Требуется повторная авторизация: {row.label}",
            message="Токен доступа OAuth истёк или был отозван провайдером. Переподключите интеграцию в настройках.",
            notification_type=NotificationType.SOURCE_AUTH_REQUIRED,
            severity="action_required",
            related_entity_type="workspace_connection",
            related_entity_id=row.id,
            dedup_key=f"oauth_reauth:{row.tenant_id}:{row.id}",
        )
        session.add(notif)
        await session.flush()

    async def disconnect(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_id: str = "oauth2",
        revoke_url_override: str | None = None,
        session=None,
    ) -> dict[str, Any]:
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.disconnect(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    method_id=method_id,
                    revoke_url_override=revoke_url_override,
                    session=owned,
                )

        await _authorize_member(session, tenant_id, actor_id)
        row = (
            await session.execute(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == tenant_id,
                    WorkspaceConnection.owner_user_id == actor_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise OAuthFlowError("connection_denied")

        secret_row = await session.get(WorkspaceConnectionSecret, row.id)
        token_to_revoke = ""
        if secret_row and secret_row.secret_encrypted:
            try:
                bundle = decode_secret_bundle(secret_row.secret_encrypted)
                token_to_revoke = str(bundle.get("refresh_token") or bundle.get("access_token") or "")
            except OAuthFlowError:
                token_to_revoke = ""

        method = resolve_oauth_method(row.provider_id, method_id)
        revoke_url = revoke_url_override if revoke_url_override is not None else method.revoke_url
        if revoke_url and token_to_revoke:
            try:
                await post_oauth_form(
                    revoke_url,
                    {"token": token_to_revoke},
                    allowed_hosts=method.allowed_hosts,
                    resolver=self._resolver,
                    http_client=self._http_client,
                )
            except OAuthNetworkError:
                pass

        if secret_row is not None:
            secret_row.secret_encrypted = ""
        row.is_active = False
        row.state = "disabled"
        row.check_code = None
        row.checked_at = None
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="connector.revoke",
            resource_kind="connection",
            resource_id=row.id,
        )
        return sanitize_connection_view(row)


workspace_oauth = WorkspaceOAuthService()
