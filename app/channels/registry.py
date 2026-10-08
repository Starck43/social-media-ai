"""Channel registry: enabled channels by config + digest broadcast."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.channels.max import MaxChannel
from app.channels.telegram import TelegramChannel
from app.core.config import settings

if TYPE_CHECKING:
    from app.models import Tenant


def get_channel(name: str):
    """Return an enabled channel instance or None if not configured."""
    if name == "telegram":
        ch = TelegramChannel()
        return ch if ch.enabled else None
    if name == "max":
        ch = MaxChannel()
        return ch if ch.enabled else None
    return None


def enabled_channels() -> list:
    """All channels with credentials configured."""
    channels = []
    for name in ("telegram", "max"):
        ch = get_channel(name)
        if ch:
            channels.append(ch)
    return channels


async def resolve_digest_tenant(tenant_id: int | None = None) -> Tenant:
    """Resolve one active workspace without letting an ID grant access.

    An unscoped operator run selects the bootstrap workspace. The builder must
    enter its non-bypass scope before reading analytics, not only before send.
    """
    from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
    from app.models.managers.tenant_manager import tenants

    ambient = current_tenant_id()
    if not is_bypass() and (ambient is None or (tenant_id is not None and tenant_id != ambient)):
        raise TenantContextError("Digest delivery requires the current workspace; foreign overrides are forbidden")
    selected = tenant_id if tenant_id is not None else ambient
    if selected is None:
        tenant = await tenants.get_by_slug(settings.DEFAULT_TENANT_SLUG)
    else:
        tenant = await tenants.get(id=selected)
    if tenant is None or not tenant.is_active:
        raise TenantContextError("Digest workspace is missing or inactive")
    return tenant


def _destination_id(chat_id: str | None) -> str:
    """Normalize exact IDs/usernames without guessing numeric/username aliases."""
    if chat_id is None:
        return ""
    value = str(chat_id).strip()
    return value.lower() if value.startswith("@") else value


async def broadcast_digest(
    text: str, channel_filter: str | None = None, tenant_id: int | None = None
) -> dict[str, dict]:
    """Send only to active, digest-enabled bindings of one active workspace.

    Deprecated env destinations are ignored, including in bootstrap scope.
    Each normalized (transport, destination) is
    sent once per call. This does not make retries/transport timeouts exactly-once.
    """
    from app.models.managers.tenant_manager import tenant_channels

    tenant = await resolve_digest_tenant(tenant_id)
    bindings = await tenant_channels.digest_targets(tenant.id)
    targets: dict[tuple[str, str], str] = {}
    for binding in bindings:
        # Explicit backstop even when a caller is in operator bypass.
        if binding.tenant_id != tenant.id or not binding.is_active or not binding.is_digest_target:
            continue
        if channel_filter and binding.channel != channel_filter:
            continue
        chat_id = _destination_id(binding.chat_id)
        if chat_id:
            targets.setdefault((binding.channel, chat_id), f"{binding.channel}:{chat_id}")

    results: dict[str, dict] = {}
    for (name, chat_id), key in targets.items():
        ch = get_channel(name)
        if not ch:
            results[key] = {"success": False, "error": "channel not configured"}
            continue
        results[key] = await ch.send(chat_id, text, parse_mode="HTML")
    return results
