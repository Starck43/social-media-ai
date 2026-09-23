"""Inbound routing: (channel, chat_id, user_id) -> tenant workspace.

One bot serves every workspace, so the routing decision *is* the access check:
a chat is served by whichever tenant `tenant_channels` binds it to, and only
members of that workspace reach the agent. An unbound chat is onboarded either
by the platform owner (env id allowlist → bootstrap workspace) or by redeeming
an invitation code.

The lookups run with `bypass=True`: no tenant is known yet, and this lookup is
what establishes one. Once a tenant is resolved, callers wrap the rest of the
work in `tenant_scope(tenant_id)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models.managers.tenant_manager import (
    tenant_channels,
    tenant_invites,
    tenant_users,
    tenants,
)

logger = logging.getLogger(__name__)

START_COMMAND = "/start"


@dataclass
class Resolution:
    """Who is talking and which workspace owns the conversation."""

    tenant_id: int
    channel: str
    chat_id: str
    user_id: str
    role: str
    onboarded: bool = False
    is_platform_owner: bool = False

    @property
    def is_owner(self) -> bool:
        """Writes that affect the whole workspace stay with its owner."""
        return self.is_platform_owner or self.role == "owner"


def is_platform_owner(channel: str, user_id: str) -> bool:
    """Env allowlist: ids that may bootstrap a workspace without an invitation."""
    uid = str(user_id or "")
    if not uid:
        return False
    if channel == "telegram":
        allowed = {str(x) for x in settings.telegram_owner_ids()}
    elif channel == "max":
        allowed = {str(x) for x in settings.max_owner_ids()}
    else:
        return False
    return uid in allowed


def parse_invite_code(text: str) -> Optional[str]:
    """`/start ABCD-2345` (also `/start@bot CODE`) → `'ABCD-2345'`."""
    parts = (text or "").strip().split()
    if len(parts) < 2:
        return None
    if parts[0].split("@")[0].lower() != START_COMMAND:
        return None
    return parts[1]


async def tenant_daily_cost_limit(tenant_id: int) -> float:
    """Per-workspace LLM budget; falls back to the global setting when unset."""
    tenant = await tenants.get(id=tenant_id)
    limit = float(getattr(tenant, "daily_cost_limit", 0) or 0)
    return limit if limit > 0 else float(settings.AGENT_DAILY_COST_LIMIT or 0)


def _chat_kind(inbound: Any) -> str:
    return "channel" if getattr(inbound, "is_channel_post", False) else "private"


async def _bootstrap_owner(inbound: Any) -> Resolution:
    """Bind a platform owner's chat to the bootstrap workspace (first contact)."""
    tenant = await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)
    channel = inbound.channel
    await tenant_users.add_member(
        tenant_id=tenant.id,
        channel=channel,
        external_user_id=str(inbound.user_id),
        role="owner",
    )
    await tenant_channels.bind(
        tenant_id=tenant.id,
        channel=channel,
        chat_id=str(inbound.chat_id),
        kind=_chat_kind(inbound),
    )
    logger.info(f"Onboarded platform owner {channel}:{inbound.user_id} into tenant {tenant.id}")
    return Resolution(
        tenant_id=tenant.id,
        channel=channel,
        chat_id=str(inbound.chat_id),
        user_id=str(inbound.user_id),
        role="owner",
        onboarded=True,
        is_platform_owner=True,
    )


async def resolve_inbound(inbound: Any) -> Optional[Resolution]:
    """Route one inbound message to its workspace, or None to ignore it.

    Returns None (silently) for chats that are not bound and whose sender is
    neither the platform owner nor redeeming a valid invite code: unknown ids
    must not learn that the bot exists, and must never reach the agent loop.
    """
    channel = inbound.channel
    chat_id = str(inbound.chat_id)
    user_id = str(inbound.user_id)
    text = getattr(inbound, "text", "") or ""

    with tenant_scope(bypass=True):
        binding = await tenant_channels.get_binding(channel=channel, chat_id=chat_id)
        member = None
        if binding is not None:
            member = await tenant_users.get_member(
                tenant_id=binding.tenant_id, channel=channel, external_user_id=user_id
            )

        if binding is not None and member is not None:
            return Resolution(
                tenant_id=binding.tenant_id,
                channel=channel,
                chat_id=chat_id,
                user_id=user_id,
                role=member.role,
                is_platform_owner=is_platform_owner(channel, user_id),
            )

        code = parse_invite_code(text)
        if code:
            result = await tenant_invites.redeem(code=code, channel=channel, chat_id=chat_id, external_user_id=user_id)
            if result.get("status") in ("bound", "already"):
                return Resolution(
                    tenant_id=result["tenant_id"],
                    channel=channel,
                    chat_id=chat_id,
                    user_id=user_id,
                    role=result.get("role", "member"),
                    onboarded=True,
                )
            logger.info(f"Invite rejected for {channel}:{chat_id}: {result}")
            return None

        if member is None and is_platform_owner(channel, user_id):
            return await _bootstrap_owner(inbound)

    return None
