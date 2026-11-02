"""VK ID (OAuth 2.1) for the L2 (user) access token.

The flow follows VK ID's Authorization Code + PKCE scheme against
`id.vk.ru` (see https://id.vk.ru docs — Access token / API reference):

1. `build_authorize_url()` generates a random `state` (no data encoded in it)
   and a PKCE `code_verifier`, stores `(state -> user, verifier)` as a
   short-lived `oauth_pending` row in the personal vault (TTL 10 min, matching
   the authorization code lifetime), and returns the `id.vk.ru/authorize` URL
   with the S256 `code_challenge`.
2. VK redirects to the public callback (`/api/v1/social/callback`) with
   `code` + `state` + `device_id`.
3. `complete_authorization()` reads the pending row by `state` (consumed, so a
   code cannot be replayed), exchanges `code` + `code_verifier` at
   `id.vk.ru/oauth2/auth` (`grant_type=authorization_code`) using the app's
   `service_token` when the app is confidential, and writes `vk/user_token`
   (access + refresh + `device_id` in `meta`) to the tenant vault.

A token never needs a browser refresh at collection time:
`refresh_user_token()` is called from `resolve_token` when the stored access
token is expired; it exchanges the refresh token at `id.vk.ru/oauth2/auth`
(`grant_type=refresh_token`) and keeps the stored `device_id`.

`code_verifier` and `device_id` are secrets/bindings that must survive across
the authorize -> exchange hop; they live in the vault (`oauth_pending` row and
token `meta`), never in the `state` string (VK ID forbids data in `state`).

Secrets never leave the vault except through `resolve_token`; nothing here
logs a plaintext token.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.social.credential_diagnostics import credential_exception_status
from app.services.social.credentials import resolve_token
from app.services.social.platform_config import load_platform_config, require_config_param

logger = logging.getLogger(__name__)


_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_state() -> str:
    """Return a random OAuth `state` string (>= 32 chars, VK ID alphabet).

    Deliberately carries no data: tenant/user/code_verifier live in the vault,
    keyed by this value (VK ID forbids data in `state`).
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(32))


def generate_code_verifier() -> str:
    """Return a fresh PKCE `code_verifier` (43-128 chars, VK ID alphabet)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(64))


def code_challenge(verifier: str) -> str:
    """S256 code challenge for a PKCE verifier (BASE64URL-ENCODE(SHA256))."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


DEFAULT_EXPIRES_IN = 3600

# How long before its expiry a VK user token is renewed proactively. The access
# token lives one hour and the refresh token six months, so renewing early is
# essentially free — while renewing late means a run can start with a token that
# dies mid-request.
_RENEW_MARGIN = timedelta(minutes=5)

# Ephemeral authorization states live in the personal vault (`user_credentials`)
# as short-lived rows: `kind="oauth_pending"`, `label=state` (queryable),
# `secret_encrypted=code_verifier`, `expires_at=now+TTL`. This reuses the
# existing vault (encryption + TTL) instead of a dedicated table; the rows are
# transient and are consumed/deleted on the code exchange.
_PENDING_KIND = "oauth_pending"
_PENDING_TTL_MINUTES = 10


def _is_expired(expires_at: Optional[datetime]) -> bool:
    if expires_at is None:
        return False
    moment = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc)


async def _store_pending(*, user_id: int, state: str, verifier: str, tenant_id: int) -> None:
    from app.models.managers.user_credential_manager import user_credentials

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PENDING_TTL_MINUTES)
    row = await user_credentials.store(user_id=user_id, platform="vk", kind=_PENDING_KIND, secret=verifier, label=state)
    await user_credentials.update_by_id(row.id, expires_at=expires_at, meta={"tenant_id": tenant_id})


async def _consume_pending(state: str) -> Optional[tuple[int, str]]:
    """Return `(user_id, code_verifier)` for a fresh pending state, consuming it.

    Deletes the row before returning, so a code cannot be replayed with the
    same state even if the exchange fails. None when missing or expired.
    """
    from app.models.managers.user_credential_manager import user_credentials

    row = await user_credentials.filter(platform="vk", kind=_PENDING_KIND, label=state).first()
    if row is None:
        return None
    await user_credentials.delete_by_id(row.id)
    if _is_expired(row.expires_at):
        return None
    return row.user_id, row.reveal()


async def build_authorize_url(
    tenant_id: int,
    *,
    user_id: Optional[int] = None,
    app_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    """Return the VK ID authorize URL for a workspace.

    `user_id` is the web user (`users.id`) that will own the resulting L2 token.
    A pending row records the PKCE `code_verifier` so the callback can exchange
    the code; the `state` itself is a random string (no encoded data).

    `scope` is optional: VK ID defaults to `vkid.personal_info`, and access to
    VK API methods (wall/groups/messages) is granted by the app's "Доступы"
    settings in the VK ID cabinet, not by this parameter.
    """
    if user_id is None:
        raise RuntimeError("VK ID OAuth requires a web user_id to own the resulting token")
    state = generate_state()
    verifier = generate_code_verifier()
    app = app_id or settings.VK_APP_ID
    if not app:
        raise RuntimeError("VK app_id is not configured (env VK_APP_ID or vault vk/app_id)")

    config = await load_platform_config("vk")
    callback = require_config_param(config, "redirect_uri")
    if redirect_uri is not None and redirect_uri != callback:
        raise ValueError("OAuth callback must match shared Platform configuration")
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": app,
        "redirect_uri": callback,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    query = urlencode(params)
    url = f"{require_config_param(config, 'oauth_base_url')}/authorize?{query}"

    await _store_pending(user_id=user_id, state=state, verifier=verifier, tenant_id=tenant_id)
    logger.info("Built VK ID authorize URL for workspace %s", tenant_id)
    return url


async def _post_form(payload: dict[str, str]) -> dict[str, Any]:
    config = await load_platform_config("vk")
    token_url = require_config_param(config, "oauth_token_url")
    async with httpx.AsyncClient(timeout=settings.VK_REQUEST_TIMEOUT) as client:
        res = await client.post(token_url, data=payload)
        res.raise_for_status()
        return res.json()


async def complete_authorization(code: str, state: str, device_id: Optional[str] = None) -> dict[str, Any]:
    """Exchange an authorization code for tokens and store them in the vault.

    Returns the raw VK token response. Raises `ValueError` on a bad/unknown/
    expired state and `RuntimeError` when the app credentials are missing.
    """
    consumed = await _consume_pending(state)
    if consumed is None:
        raise ValueError("Invalid, unknown, or expired OAuth state")
    user_id, verifier = consumed

    app_id = settings.VK_APP_ID
    if not app_id:
        raise RuntimeError("VK app_id not configured for the exchange")

    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "redirect_uri": require_config_param(await load_platform_config("vk"), "redirect_uri"),
        "code": code,
        "client_id": app_id,
        "state": state,
    }
    if device_id:
        payload["device_id"] = device_id
    service_token = await resolve_token("vk", kinds=("service_token",))
    if service_token:
        payload["service_token"] = service_token

    data = await _post_form(payload)
    meta: dict[str, Any] = {"scope": data.get("scope"), "user_id": data.get("user_id")}
    if device_id:
        meta["device_id"] = device_id
    await _store_vault_token(
        user_id=user_id,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        meta=meta,
    )
    logger.info("VK ID authorization completed for user %s", user_id)
    return data


async def _store_vault_token(
    *,
    user_id: int,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Optional[int],
    meta: dict[str, Any],
) -> None:
    """Write `vk/user_token` into the personal vault, updating the newest row if any.

    The token belongs to a person, so `user_id` is required and the row always
    lives in `user_credentials` (shared across that user's workspaces).
    """
    from app.models.managers.user_credential_manager import user_credentials
    from app.utils.crypto import encrypt_secret

    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    elif refresh_token:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_EXPIRES_IN)

    async with user_credentials.locked_vk_token(user_id=user_id) as (session, row):
        # A completed explicit OAuth login supersedes an older refresh claim.
        # Preserve unrelated metadata; clear only renewal state invalidated by
        # this newly authorized session.
        stored_meta = dict(row.meta or {}) if row is not None else {}
        stored_meta.update(meta)
        # Never persist a secret or a caller-supplied status flag in metadata.
        stored_meta.pop("refresh_token", None)
        stored_meta["refresh_available"] = bool(refresh_token)
        stored_meta.pop("refresh_attempt", None)
        stored_meta.pop("refresh_failed_at", None)
        values = {
            "secret_encrypted": encrypt_secret(access_token),
            "refresh_secret_encrypted": encrypt_secret(refresh_token) if refresh_token else None,
            "expires_at": expires_at,
            "meta": stored_meta,
        }
        if row is not None:
            await user_credentials.update_by_id(row.id, session=session, **values)
        else:
            await user_credentials.create(
                user_id=user_id,
                platform="vk",
                kind="user_token",
                label="VK ID (PKCE)",
                session=session,
                **values,
            )


async def refresh_user_token(user_id: int) -> Optional[str]:
    """Renew under a durable, DB-serialized single-use refresh claim.

    A claim is committed before HTTP. Concurrent processes cannot replay the
    token, even if the first process is cancelled or loses the response. An
    unfinished/failed claim is deliberately NOT reclaimed by a timer: VK may
    have consumed the token. Explicit OAuth login is the recovery path.
    Success/failure is fenced by both row ID and claim ID; an old request cannot
    overwrite a newer login, revoke or successful rotation.

    Refresh material is encrypted at rest. Both status surfaces use only the
    transactionally maintained non-secret availability indicator.
    """
    return await _renew_user_token(user_id, margin=_RENEW_MARGIN, explicit=True)


async def _renew_user_token(user_id: int, *, margin: timedelta, explicit: bool = False) -> Optional[str]:
    from app.models.managers.user_credential_manager import user_credentials
    from app.services.social.connections import REFRESH_FAILURE_COOLDOWN
    from app.utils.crypto import decrypt_secret, encrypt_secret

    if type(user_id) is not int or user_id <= 0 or not settings.VK_APP_ID:
        return None

    attempt_id = secrets.token_urlsafe(24)
    async with user_credentials.locked_vk_token(user_id=user_id) as (session, row):
        if row is None:
            return None
        meta = dict(row.meta or {})
        now = datetime.now(timezone.utc)
        expiry = row.expires_at
        if expiry is not None:
            moment = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
            if moment > now + margin:
                # Another caller may already have renewed while we waited.
                return row.reveal()
        elif not explicit:
            return None
        # A pending or failed attempt may already have consumed this token.
        # Never clear/reclaim this marker automatically, even after a timeout.
        if meta.get("refresh_attempt"):
            return None
        failed_at = meta.get("refresh_failed_at")
        if failed_at:
            try:
                failed = datetime.fromisoformat(str(failed_at))
                failed = failed if failed.tzinfo else failed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
            if now - failed < REFRESH_FAILURE_COOLDOWN:
                return None
        encrypted_refresh = row.refresh_secret_encrypted
        if not encrypted_refresh or meta.get("refresh_available") is not True:
            return None
        try:
            refresh = decrypt_secret(encrypted_refresh)
            if not isinstance(refresh, str) or not refresh:
                raise ValueError("Empty refresh secret")
        except (ValueError, RuntimeError, TypeError):
            # No provider request was sent. Do not reveal the secret or claim a
            # renewable session when the configured key cannot read the vault.
            meta["refresh_attempt"] = {"state": "unreadable"}
            await user_credentials.update_by_id(row.id, session=session, meta=meta)
            logger.warning("VK ID refresh unavailable user_id=%s code=unreadable_secret", user_id)
            return None
        row_id = row.id
        meta["refresh_attempt"] = {"id": attempt_id, "state": "pending", "started_at": now.isoformat()}
        await user_credentials.update_by_id(row_id, session=session, meta=meta)
    # The manager context has COMMITTED the claim before any external request.
    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": settings.VK_APP_ID,
        "state": generate_state(),
    }
    if meta.get("device_id"):
        payload["device_id"] = str(meta["device_id"])
    try:
        service_token = await resolve_token("vk", kinds=("service_token",))
        if service_token:
            payload["service_token"] = service_token
        data = await _post_form(payload)
        access = data["access_token"]
        if not isinstance(access, str) or not access:
            raise ValueError("Missing access token")
        expires_at = None
        if data.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
        encrypted_access = encrypt_secret(access)
        rotated_refresh = data.get("refresh_token")
        if rotated_refresh is not None and not isinstance(rotated_refresh, str):
            raise ValueError("Invalid refresh token response")
        encrypted_refresh = encrypt_secret(rotated_refresh) if rotated_refresh else None
    except Exception as exc:
        logger.warning("VK ID refresh failed user_id=%s code=%s", user_id, credential_exception_status(exc))
        async with user_credentials.locked_vk_token(user_id=user_id) as (session, current):
            if _owns_refresh_attempt(current, row_id, attempt_id):
                failed_meta = dict(current.meta or {})
                failed_meta["refresh_attempt"] = {**failed_meta["refresh_attempt"], "state": "failed"}
                await _mark_refresh_failed(user_credentials, current, failed_meta, session=session)
        return None

    async with user_credentials.locked_vk_token(user_id=user_id) as (session, current):
        if not _owns_refresh_attempt(current, row_id, attempt_id):
            return None
        current_meta = dict(current.meta or {})
        # Never fall back to a spent refresh token, even if VK omits its replacement.
        current_meta.pop("refresh_token", None)
        current_meta["refresh_available"] = bool(encrypted_refresh)
        if data.get("user_id"):
            current_meta["user_id"] = data["user_id"]
        current_meta.pop("refresh_failed_at", None)
        current_meta.pop("refresh_attempt", None)
        await user_credentials.update_by_id(
            row_id,
            session=session,
            secret_encrypted=encrypted_access,
            refresh_secret_encrypted=encrypted_refresh,
            expires_at=expires_at,
            meta=current_meta,
        )
    logger.info("VK ID user token refreshed for user %s", user_id)
    return access


def _owns_refresh_attempt(row, row_id: int, attempt_id: str) -> bool:
    if row is None or row.id != row_id:
        return False
    attempt = (row.meta or {}).get("refresh_attempt")
    return isinstance(attempt, dict) and attempt.get("id") == attempt_id and attempt.get("state") == "pending"


async def _mark_refresh_failed(user_credentials, row, meta: dict[str, Any], *, session=None) -> None:
    """Remember that renewal failed, so the UI can ask for a re-login.

    Best-effort: this is a bookkeeping write on an already-failing path, so a
    failure to record must not mask the original problem.
    """
    meta["refresh_failed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        values = {"meta": meta}
        if session is not None:
            values["session"] = session
        await user_credentials.update_by_id(row.id, **values)
    except Exception as exc:  # noqa: BLE001 — bookkeeping must never raise
        logger.warning(
            "VK ID refresh failure bookkeeping failed user_id=%s code=%s",
            row.user_id if type(row.user_id) is int and row.user_id > 0 else None,
            credential_exception_status(exc),
        )


async def renew_if_expiring(user_id: int, *, margin: timedelta = _RENEW_MARGIN) -> Optional[str]:
    """Return a usable VK token, renewing it before it actually expires.

    `resolve_token` only renews a token that has *already* aged out. That is late:
    a collection run starting at 10:59 would take the token, and the request
    landing at 11:00 would fail. Renewing a margin early costs nothing (the
    refresh token is valid for months) and removes the race.
    """
    return await _renew_user_token(user_id, margin=margin)
