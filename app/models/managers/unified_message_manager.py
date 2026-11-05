"""Tenant-fenced managers for unified communications (`unified_messages`, `message_threads`, `contact_channel_stats`, P15-T02).

Enforces workspace isolation (`current_tenant_id()`) and `comms.message` permission checks per D14.
"""

from __future__ import annotations

from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.models.communications.message import ContactChannelStat, MessageThread, UnifiedMessage
from app.models.managers.base_manager import BaseManager
from app.types import ActionType


def require_message_permission(action: ActionType) -> int:
    """Require an active workspace and `comms.message` permission for `action` (D14)."""
    tenant = current_tenant_id()
    if tenant is None:
        raise TenantContextError("Unified message access requires a selected workspace")
    user = get_current_user()
    if not has_permission(user, "comms.message", action):
        raise PermissionError("Unified message access denied")
    return tenant


def _check_optional_actor_permission(action: ActionType) -> int:
    """Enforce workspace context unconditionally and `comms.message` permission when an actor is in scope."""
    tenant = current_tenant_id()
    if tenant is None:
        raise TenantContextError("Unified message access requires a selected workspace")
    user = get_current_user()
    if user is not None and not has_permission(user, "comms.message", action):
        raise PermissionError("Unified message access denied")
    return tenant


class UnifiedMessageManager(BaseManager):
    """Enforces workspace isolation and `comms.message` permissions on `UnifiedMessage` queries."""

    def __init__(self):
        super().__init__(UnifiedMessage)

    def get_queryset(self, session=None):
        tenant = _check_optional_actor_permission(ActionType.VIEW)
        return super().get_queryset(session).filter(tenant_id=tenant)


class MessageThreadManager(BaseManager):
    """Enforces workspace isolation and `comms.message` permissions on `MessageThread` queries."""

    def __init__(self):
        super().__init__(MessageThread)

    def get_queryset(self, session=None):
        tenant = _check_optional_actor_permission(ActionType.VIEW)
        return super().get_queryset(session).filter(tenant_id=tenant)


class ContactChannelStatManager(BaseManager):
    """Enforces workspace isolation and `comms.message` permissions on `ContactChannelStat` queries."""

    def __init__(self):
        super().__init__(ContactChannelStat)

    def get_queryset(self, session=None):
        tenant = _check_optional_actor_permission(ActionType.VIEW)
        return super().get_queryset(session).filter(tenant_id=tenant)
