"""Platform credentials, resolved from the personal vault first, env second.

Personal secrets (VK L2 `user_token`, Telegram MTProto parts) live encrypted
(Fernet, key `CREDENTIALS_KEY`) in `user_credentials`, keyed by `users.id` and
shared across the workspaces that user belongs to — never duplicated per tenant.

Everything else that used to live in `tenant_credentials` (app_id, client_secret,
service_token, bot_token) is application/infrastructure config, now read from the
environment: a VK app is one-per-deployment, so `VK_APP_ID`/`VK_CLIENT_ACCESS_KEY`
cover every workspace.

`resolve_token()` needs a `owner_user_id` to read a *personal* secret; without it
only env fallback applies. Environment values are always the last resort and keep
unconfigured installs startable.

The plaintext exists solely as the return value of `resolve_token()`: never log
it, never put it into an LLM prompt, never echo it into a chat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class CredentialMissing(RuntimeError):
    """No usable secret exists and the caller asked for a required one."""


class AuthorizationRequired(RuntimeError):
    """A source needs a personal (L2) secret that nobody has authorized yet.

    Raised by the collectors instead of returning an empty list, because
    "nothing was collected" and "we are not authorized to look" are different
    facts: the first is quiet, the second needs the user to press one button.
    The `hint` is what the UI shows next to that button, so it is written for
    a person, not for a log file.
    """

    def __init__(self, message: str, *, platform: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.platform = platform
        self.hint = hint


# Ordered kinds per platform: the first available one wins. A VK user token sees
# more than a community service token, so it is preferred when both are present.
KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "vk": ("user_token", "service_token"),
    "telegram": ("bot_token",),
    "max": ("bot_token",),
}

# (platform, kind) -> settings attribute. Personal kinds are resolved from the
# user vault and only fall back to env when no personal row exists.
ENV_FALLBACK: dict[tuple[str, str], str] = {
    ("vk", "service_token"): "VK_SERVICE_KEY",
    ("vk", "client_secret"): "VK_CLIENT_ACCESS_KEY",
    ("vk", "app_id"): "VK_APP_ID",
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


async def _newest_row(rows: list, kind: str) -> Optional[Any]:
    """Newest active row of one kind among already-filtered `rows`, or None."""
    candidates = [row for row in rows if row.kind == kind]
    candidates.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    return candidates[0] if candidates else None


async def _from_user_vault(user_id: int, platform: str, kind: str) -> Optional[str]:
    """Newest usable personal vault row for (user, platform, kind), or None.

    A VK L2 user token that has expired is transparently renewed via its stored
    `refresh_token` (see `app.services.social.vk_oauth`) before the row is
    skipped, so an L2 collection never fails just because the token aged out.
    """
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.active(user_id=user_id, platform=platform)
    row = await _newest_row(rows, kind)
    if row is None:
        return None
    if platform == "vk" and kind == "user_token":
        # Renew slightly *before* the expiry, not only after it: a run that
        # starts at 10:59 would otherwise carry a token that dies mid-request.
        from app.services.social.vk_oauth import renew_if_expiring

        renewed = await renew_if_expiring(user_id)
        if renewed:
            return renewed
    if _is_expired(row.expires_at):
        logger.warning(f"{platform}/{kind} credential of user {user_id} expired - skipped")
        return None
    try:
        return row.reveal()
    except ValueError as e:  # wrong or rotated CREDENTIALS_KEY
        logger.error(f"Cannot decrypt {platform}/{kind} of user {user_id}: {e}")
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
    owner_user_id: Optional[int] = None,
) -> Optional[str]:
    """Return a usable secret for `platform`: user vault first, env as fallback.

    `owner_user_id` selects the *personal* vault (`user_credentials`): pass it
    when the secret belongs to a person (VK L2 `user_token` for a specific user,
    e.g. from `Source.params["token_owner"]`). Without it, only env fallback
    applies. `tenant_id` is accepted for backward compatibility but is no longer
    consulted — there is no workspace-scoped vault anymore.
    """
    wanted = kinds or KIND_PREFERENCE.get(platform, ())

    for kind in wanted:
        if owner_user_id is not None:
            secret = await _from_user_vault(owner_user_id, platform, kind)
            if secret:
                logger.info(f"Resolved {platform}/{kind} credential from user vault for user {owner_user_id}")
                return secret
        secret = _from_env(platform, kind)
        if secret:
            return secret

    if required:
        raise CredentialMissing(
            f"No {platform} credential configured (looked for: {', '.join(wanted) or 'nothing'}). "
            "Set the matching env var or authorize a personal token (CLI: credentials oauth vk)."
        )
    return None


async def credential_status(*, owner_user_id: Optional[int] = None) -> dict[str, str]:
    """Where each platform's secret comes from, without revealing it.

    Used by admin/CLI diagnostics and by agent tools, which must never see the
    secret itself. Values: `user-vault`, `env` or `missing`.
    """
    status: dict[str, str] = {}
    for platform, kinds in KIND_PREFERENCE.items():
        source = "missing"
        for kind in kinds:
            if owner_user_id is not None and await _from_user_vault(owner_user_id, platform, kind):
                source = "user-vault"
                break
            if _from_env(platform, kind):
                source = "env"
                break
        status[platform] = source
    return status
