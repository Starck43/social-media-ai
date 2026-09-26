"""Per-tenant platform credentials, resolved from the vault first.

Secrets live encrypted in `tenant_credentials` (Fernet, key `CREDENTIALS_KEY`).
Environment variables are the legacy single-workspace fallback only: a vault row
always wins, so an operator can move a token into the database without touching
the deployment. This keeps unconfigured installs startable.

The plaintext exists solely as the return value of `resolve_token()`: never log
it, never put it into an LLM prompt, never echo it into a chat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.core.tenant_context import current_tenant_id

logger = logging.getLogger(__name__)


class CredentialMissing(RuntimeError):
    """No usable secret exists and the caller asked for a required one."""


# Ordered kinds per platform: the first available one wins. A VK user token sees
# more than a community service token, so it is preferred when both are present.
KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "vk": ("user_token", "service_token"),
    "telegram": ("bot_token",),
    "max": ("bot_token",),
}

# Every kind `credentials set` accepts for a platform. KIND_PREFERENCE stays the
# resolution order for the *default* secret (bot sends must keep resolving the
# bot token); L2 MTProto parts (api_id/api_hash/session) are resolved explicitly
# via resolve_token(kinds=...) — see app.services.social.tg_session.
SETTABLE_KINDS: dict[str, tuple[str, ...]] = {
    "vk": KIND_PREFERENCE["vk"],
    "telegram": KIND_PREFERENCE["telegram"] + ("api_id", "api_hash", "session"),
    "max": KIND_PREFERENCE["max"],
}

# (platform, kind) -> settings attribute used as the legacy fallback.
ENV_FALLBACK: dict[tuple[str, str], str] = {
    ("vk", "user_token"): "VK_USER_ACCESS_TOKEN",
    ("vk", "service_token"): "VK_SERVICE_ACCESS_TOKEN",
    ("telegram", "bot_token"): "TELEGRAM_BOT_TOKEN",
    ("telegram", "api_id"): "TELEGRAM_API_ID",
    ("telegram", "api_hash"): "TELEGRAM_API_HASH",
    ("telegram", "session"): "TELEGRAM_SESSION",
    ("max", "bot_token"): "MAX_BOT_TOKEN",
}


def _is_expired(expires_at: Optional[datetime]) -> bool:
    if expires_at is None:
        return False
    moment = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc)


async def _from_vault(tenant_id: int, platform: str, kind: str) -> Optional[str]:
    """Newest usable vault row for (tenant, platform, kind), or None."""
    from app.models.managers.tenant_manager import tenant_credentials

    rows = await tenant_credentials.active(tenant_id=tenant_id, platform=platform)
    candidates = [row for row in rows if row.kind == kind]
    candidates.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    for row in candidates:
        if _is_expired(row.expires_at):
            logger.warning(f"{platform}/{kind} credential of workspace {tenant_id} expired - skipped")
            continue
        try:
            return row.reveal()
        except ValueError as e:  # wrong or rotated CREDENTIALS_KEY
            logger.error(f"Cannot decrypt {platform}/{kind} of workspace {tenant_id}: {e}")
            return None
    return None


def _from_env(platform: str, kind: str) -> Optional[str]:
    attribute = ENV_FALLBACK.get((platform, kind))
    if not attribute:
        return None
    value = getattr(settings, attribute, None)
    if not value:
        return None
    logger.info(f"Using legacy env credential {attribute} for {platform}/{kind}")
    return value


async def resolve_token(
    platform: str,
    *,
    tenant_id: Optional[int] = None,
    kinds: Optional[tuple[str, ...]] = None,
    required: bool = False,
) -> Optional[str]:
    """Return a usable secret for `platform`: vault first, env as fallback.

    `tenant_id` defaults to the current scope, which the job dispatcher and the
    agent chat both set before calling into collection.
    """
    tenant = tenant_id if tenant_id is not None else current_tenant_id()
    wanted = kinds or KIND_PREFERENCE.get(platform, ())

    for kind in wanted:
        if tenant is not None:
            secret = await _from_vault(tenant, platform, kind)
            if secret:
                logger.info(f"Resolved {platform}/{kind} credential from vault for workspace {tenant}")
                return secret
        secret = _from_env(platform, kind)
        if secret:
            return secret

    if required:
        raise CredentialMissing(
            f"No {platform} credential configured (looked for: {', '.join(wanted) or 'nothing'}). "
            "Add it in the admin (Credentials) or in the tenant vault."
        )
    return None


async def credential_status(tenant_id: Optional[int] = None) -> dict[str, str]:
    """Where each platform's secret comes from, without revealing it.

    Used by admin/CLI diagnostics and by agent tools, which must never see the
    secret itself. Values: `vault`, `env` or `missing`.
    """
    tenant = tenant_id if tenant_id is not None else current_tenant_id()
    status: dict[str, str] = {}
    for platform, kinds in KIND_PREFERENCE.items():
        source = "missing"
        for kind in kinds:
            if tenant is not None and await _from_vault(tenant, platform, kind):
                source = "vault"
                break
            if _from_env(platform, kind):
                source = "env"
                break
        status[platform] = source
    return status
