"""VK OAuth 2.0 with PKCE for the L2 (user) access token.

The flow (RFC 7636, S256):
1. `build_authorize_url()` generates a `code_verifier` and a signed `state`
   and returns the VK authorize URL for the user to open in a browser.
2. VK redirects to the public callback (`/api/v1/social/callback`) with
   `code` + `state`.
3. `complete_authorization()` verifies the signed state (recovering the
   workspace and the verifier without touching the DB), exchanges the code
   for tokens, and writes `vk/user_token` (access + refresh in `meta`) to
   the tenant vault.

The signed `state` is `"{tenant_id}.{user_id}.{verifier}.{hmac}"`; the hmac key
is `SECRET_KEY`, so a callback carries everything the exchange needs (which
workspace, which web user owns the token, and the PKCE verifier) and needs no
shared in-memory or DB state. A token never needs a browser refresh at
collection time: `refresh_user_token()` is called from `resolve_token` when the
stored access token is expired.

Secrets never leave the vault except through `resolve_token`; nothing here
logs a plaintext token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.services.social.credentials import resolve_token

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://oauth.vk.com/access_token"

# Default scope for content collection (posts, groups, messages, identity).
DEFAULT_SCOPE = "offline,wall,groups,messages,photos,users"

# Access token lifetime once issued by VK (seconds). VK may omit `expires_in`
# for `offline` tokens; treat it as long-lived but refreshable.
DEFAULT_EXPIRES_IN = 86400


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_pkce() -> tuple[str, str]:
    """Return `(code_verifier, code_challenge)` for the S256 PKCE method."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _sign(tenant_id: int, user_id: Optional[int], verifier: str) -> str:
    owner = user_id or 0
    message = f"{tenant_id}.{owner}.{verifier}".encode()
    digest = hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()
    return f"{tenant_id}.{owner}.{verifier}.{digest}"


def _verify(state: str) -> Optional[tuple[int, Optional[int], str]]:
    """Recover `(tenant_id, user_id, verifier)` from a signed state, or None."""
    try:
        tenant_id, owner, verifier, digest = state.split(".", 3)
    except ValueError:
        return None
    expected = hmac.new(
        settings.SECRET_KEY.encode(), f"{tenant_id}.{owner}.{verifier}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(digest, expected):
        return None
    try:
        tenant = int(tenant_id)
        user = int(owner) or None
        return tenant, user, verifier
    except (TypeError, ValueError):
        return None


def build_authorize_url(
    tenant_id: int,
    *,
    user_id: Optional[int] = None,
    app_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
    scope: str = DEFAULT_SCOPE,
) -> str:
    """Return the VK authorize URL for a workspace.

    `user_id` is the web user (`users.id`) that will own the resulting L2 token.
    The signed state embeds the verifier and the owner, so the callback recovers
    everything (workspace + owner + verifier) from `state` alone — no server-side
    persistence.
    """
    verifier, challenge = generate_pkce()
    state = _sign(tenant_id, user_id, verifier)
    app = app_id or settings.VK_APP_ID
    if not app:
        raise RuntimeError("VK app_id is not configured (env VK_APP_ID or vault vk/app_id)")
    params = {
        "client_id": app,
        "redirect_uri": redirect_uri or settings.VK_REDIRECT_URI,
        "display": "page",
        "scope": scope,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "v": settings.VK_API_VERSION,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{settings.VK_OAUTH_BASE_URL}/authorize?{query}"
    logger.info("Built VK authorize URL for workspace %s", tenant_id)
    return url


async def _post_form(payload: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=settings.VK_REQUEST_TIMEOUT) as client:
        res = await client.post(OAUTH_TOKEN_URL, data=payload)
        res.raise_for_status()
        return res.json()


async def complete_authorization(code: str, state: str) -> dict[str, Any]:
    """Exchange an authorization code for tokens and store them in the vault.

    Returns the raw VK token response. Raises `ValueError` on a bad state and
    `RuntimeError` when the app credentials are missing.
    """
    recovered = _verify(state)
    if recovered is None:
        raise ValueError("Invalid or tampered OAuth state")
    tenant_id, user_id, verifier = recovered

    app_id = settings.VK_APP_ID
    client_secret = await resolve_token("vk", kinds=("client_secret",))
    if not app_id or not client_secret:
        raise RuntimeError("VK app_id/client_secret not configured for the exchange")

    data = await _post_form(
        {
            "client_id": app_id,
            "client_secret": client_secret,
            "redirect_uri": settings.VK_REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        }
    )
    meta = {"scope": data.get("scope"), "user_id": data.get("user_id")}
    await _store_vault_token(
        user_id=user_id,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        meta=meta,
    )
    logger.info("VK authorization completed for workspace %s", tenant_id)
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
        label="VK OAuth (PKCE)",
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
    client_secret = await resolve_token("vk", kinds=("client_secret",))
    if not app_id or not client_secret:
        return None

    row = await user_credentials.newest(user_id=user_id, platform="vk", kind="user_token")
    if row is None:
        return None
    meta = row.meta or {}
    refresh = meta.get("refresh_token")
    if not refresh:
        return None

    try:
        data = await _post_form(
            {
                "client_id": app_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            }
        )
    except Exception as exc:
        logger.warning("VK refresh failed for user %s: %s", user_id, exc)
        return None

    access = data["access_token"]
    expires_at = None
    if data.get("expires_in"):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
    meta["refresh_token"] = data.get("refresh_token") or refresh

    await user_credentials.update_by_id(
        row.id,
        secret_encrypted=encrypt_secret(access),
        expires_at=expires_at,
        meta=meta,
    )
    logger.info("VK user token refreshed for user %s", user_id)
    return access
