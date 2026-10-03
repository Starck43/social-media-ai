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

import httpx

from app.core.config import settings
from app.services.social.credentials import resolve_token

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://id.vk.ru/oauth2/auth"

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

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": app,
        "redirect_uri": redirect_uri or settings.VK_REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{settings.VK_OAUTH_BASE_URL}/authorize?{query}"

    await _store_pending(user_id=user_id, state=state, verifier=verifier, tenant_id=tenant_id)
    logger.info("Built VK ID authorize URL for workspace %s", tenant_id)
    return url


async def _post_form(payload: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=settings.VK_REQUEST_TIMEOUT) as client:
        res = await client.post(OAUTH_TOKEN_URL, data=payload)
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
        "redirect_uri": settings.VK_REDIRECT_URI,
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

    meta["refresh_token"] = refresh_token

    row = await user_credentials.newest(user_id=user_id, platform="vk", kind="user_token")
    if row is not None:
        await user_credentials.update_by_id(
            row.id,
            secret_encrypted=encrypt_secret(access_token),
            expires_at=expires_at,
            meta=meta,
        )
        return
    created = await user_credentials.store(
        user_id=user_id,
        platform="vk",
        kind="user_token",
        secret=access_token,
        label="VK ID (PKCE)",
    )
    if expires_at or meta.get("refresh_token"):
        await user_credentials.update_by_id(created.id, expires_at=expires_at, meta=meta)


async def refresh_user_token(user_id: int) -> Optional[str]:
    """Refresh a user's VK user token from its stored refresh token.

    Returns the new access token, or None when there is nothing to refresh.
    """
    from app.models.managers.user_credential_manager import user_credentials
    from app.utils.crypto import encrypt_secret

    app_id = settings.VK_APP_ID
    if not app_id:
        return None

    row = await user_credentials.newest(user_id=user_id, platform="vk", kind="user_token")
    if row is None:
        return None
    meta = row.meta or {}
    refresh = meta.get("refresh_token")
    if not refresh:
        return None

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": app_id,
        "state": generate_state(),
    }
    if meta.get("device_id"):
        payload["device_id"] = str(meta["device_id"])
    service_token = await resolve_token("vk", kinds=("service_token",))
    if service_token:
        payload["service_token"] = service_token

    try:
        data = await _post_form(payload)
    except Exception as exc:
        logger.warning("VK ID refresh failed for user %s: %s", user_id, exc)
        return None

    access = data["access_token"]
    expires_at = None
    if data.get("expires_in"):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
    meta["refresh_token"] = data.get("refresh_token") or refresh
    if data.get("user_id"):
        meta["user_id"] = data.get("user_id")

    await user_credentials.update_by_id(
        row.id,
        secret_encrypted=encrypt_secret(access),
        expires_at=expires_at,
        meta=meta,
    )
    logger.info("VK ID user token refreshed for user %s", user_id)
    return access
