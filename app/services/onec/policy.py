"""1C capability policy: workspace grants never imply provider-account access.

Role entity lists only narrow the connection's accepted entity allowlist. They
never supply credentials, grant a capability, or bypass command approval.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.permissions import get_current_user, member_decision
from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import ActionType

CAPABILITY_PERMISSIONS = {
    "onec.query": ("onecdata", ActionType.VIEW),
    "onec.export": ("onecdata", ActionType.EXPORT),
    "onec.command.propose": ("oneccommand", ActionType.CREATE),
    "onec.command.approve": ("oneccommand", ActionType.CONFIGURE),
}


def capability_allowed(capability: str, *, tenant_id: int, actor_id: int) -> bool:
    """Answer only from an active, tenant/actor-bound membership grant."""
    user = get_current_user()
    permission = CAPABILITY_PERMISSIONS.get(capability)
    return bool(
        permission
        and type(tenant_id) is int
        and type(actor_id) is int
        and tenant_id > 0
        and actor_id > 0
        and not is_bypass()
        and current_tenant_id() == tenant_id
        and user is not None
        and user.id == actor_id
        and user.is_active
        and member_decision(user, *permission) is True
    )


def role_entity_allowlist(allowlist: Mapping, role: str) -> frozenset[str]:
    """Intersect optional role restrictions; malformed/unknown roles fail closed.

    Existing connections without ``entity_sets_by_role`` retain their accepted
    connection-level entity policy. Once supplied, a missing role grants no
    entities. Owner/admin are identical capability presets, not account bypasses.
    """
    if role not in {"owner", "admin", "manager", "analyst", "viewer"}:
        return frozenset()
    entities = allowlist.get("entity_sets")
    if not isinstance(entities, (list, tuple)) or any(not isinstance(item, str) for item in entities):
        return frozenset()
    permitted = frozenset(entities)
    if "entity_sets_by_role" not in allowlist:
        return permitted
    roles = allowlist["entity_sets_by_role"]
    if not isinstance(roles, Mapping):
        return frozenset()
    selected = roles.get(role)
    if not isinstance(selected, (list, tuple)) or any(not isinstance(item, str) for item in selected):
        return frozenset()
    return permitted.intersection(selected)
