"""Fresh interactive identity in an already resolved workspace, never routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.core.tenant_context import current_tenant_id, is_bypass

logger = logging.getLogger(__name__)


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class RuntimeIdentity:
    tenant_id: int
    user_id: int
    membership_id: int
    channel: str
    chat_id: str
    external_user_id: str
    membership_role_id: int | None
    user_role_id: int | None
    is_owner: bool
    user: Any = field(repr=False, compare=False)

    def actor_binding(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "membership_id": self.membership_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "external_user_id": self.external_user_id,
        }

    def approval_binding(self) -> dict[str, Any]:
        return {
            **self.actor_binding(),
            "membership_role_id": self.membership_role_id,
            "user_role_id": self.user_role_id,
        }


async def resolve_runtime_identity(resolution: Any) -> RuntimeIdentity | None:
    """Reload active membership/binding/User; no env-owner or NULL-role elevation."""
    tenant_id = getattr(resolution, "tenant_id", None)
    channel = getattr(resolution, "channel", None)
    external_id = getattr(resolution, "user_id", None)
    chat_id = getattr(resolution, "chat_id", None)
    if (
        not _positive_id(tenant_id)
        or current_tenant_id() != tenant_id
        or is_bypass()
        or channel not in ("web", "telegram", "max")
        or not isinstance(external_id, str)
        or not external_id
        or not isinstance(chat_id, str)
        or not chat_id
    ):
        return None
    try:
        from app.models import Tenant, User
        from app.models.managers.tenant_manager import tenant_channels, tenant_users

        tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
        if tenant is None or tenant.id != tenant_id or not tenant.is_active:
            return None
        membership = await tenant_users.prefetch_related("role").get(
            tenant_id=tenant_id, channel=channel, external_user_id=external_id, is_active=True
        )
        if (
            membership is None
            or not membership.is_active
            or membership.tenant_id != tenant_id
            or membership.channel != channel
            or membership.external_user_id != external_id
            or not _positive_id(membership.id)
            or not _positive_id(membership.user_id)
        ):
            return None
        if channel == "web":
            if external_id != str(membership.user_id) or chat_id != external_id:
                return None
        else:
            binding = await tenant_channels.get(
                tenant_id=tenant_id, channel=channel, chat_id=chat_id, is_active=True
            )
            if (
                binding is None
                or not binding.is_active
                or binding.tenant_id != tenant_id
                or binding.channel != channel
                or binding.chat_id != chat_id
            ):
                return None
        user = await User.objects.prefetch_related("role.permissions.model_type").get(
            id=membership.user_id, is_active=True
        )
        if user is None or user.id != membership.user_id or not user.is_active:
            return None
        role = membership.role if membership.role_id is not None else None
        codename = getattr(role, "codename", None)
        role_name = getattr(codename, "name", codename)
        owner = role is not None and role_name == "SUPERUSER"
        return RuntimeIdentity(
            tenant_id=tenant_id, user_id=user.id, membership_id=membership.id,
            channel=channel, chat_id=chat_id, external_user_id=external_id,
            membership_role_id=membership.role_id, user_role_id=user.role_id,
            is_owner=owner, user=user,
        )
    except Exception:  # closed/detached/failed identity loads must never authorize
        logger.warning("runtime_identity_load_failed")
        return None


async def refresh_runtime_identity(identity: RuntimeIdentity) -> RuntimeIdentity | None:
    """Refresh rights; an identity rebind requires a new turn/approval."""
    fresh = await resolve_runtime_identity(SimpleNamespace(
        tenant_id=identity.tenant_id, channel=identity.channel,
        chat_id=identity.chat_id, user_id=identity.external_user_id,
    ))
    if fresh is None or fresh.actor_binding() != identity.actor_binding():
        return None
    return fresh


def session_matches_identity(session: Any, identity: RuntimeIdentity) -> bool:
    return bool(
        session is not None
        and _positive_id(getattr(session, "id", None))
        and getattr(session, "is_active", False)
        and getattr(session, "tenant_id", None) == identity.tenant_id
        and getattr(session, "channel", None) == identity.channel
        and getattr(session, "chat_id", None) == identity.chat_id
    )
