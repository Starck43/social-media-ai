"""1C capability policy: workspace grants never imply provider-account access.

Role entity lists only narrow the connection's accepted entity allowlist. They
never supply credentials, grant a capability, or bypass command approval.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from app.core.access_catalog import workspace_grants
from app.core.permissions import get_current_user, member_decision
from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import ActionType

_DENIED = logging.getLogger("app.action_audit.onec_denied")

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

    Existing connections without ``allowed_entity_sets_by_role`` retain their accepted
    connection-level entity policy. Once supplied, a missing role grants no
    entities. Owner/admin are identical capability presets, not account bypasses.
    """
    if role not in {"owner", "admin", "manager", "analyst", "viewer"}:
        return frozenset()
    from app.services.onec.metadata import extract_connection_allowlist

    permitted, _ = extract_connection_allowlist(allowlist)
    if "allowed_entity_sets_by_role" not in allowlist:
        return permitted
    roles = allowlist["allowed_entity_sets_by_role"]
    if not isinstance(roles, Mapping):
        return frozenset()
    selected = roles.get(role)
    if not isinstance(selected, (list, tuple)) or any(not isinstance(item, str) for item in selected):
        return frozenset()
    return permitted.intersection(selected)


def log_denial(*, tenant_id: int, actor_id: int, capability: str) -> None:
    """Use the existing non-receipt denial category, never fake a success event."""
    if capability not in CAPABILITY_PERMISSIONS:
        return
    if any(type(value) is not int or not 0 < value <= 2147483647 for value in (tenant_id, actor_id)):
        return
    _DENIED.warning(
        "action_audit_path=denied outcome=denied tenant_id=%s actor_kind=user actor_id=%s "
        "action=%s reason_code=permission_denied",
        tenant_id,
        actor_id,
        capability,
    )


async def authorize_capability(*, tenant_id: int, actor_id: int, capability: str, session=None) -> str:
    """Reload active account/workspace/member authority before each operation.

    Returns the current role only for allowlist intersection, never for a
    role-name authority shortcut. Connection ownership remains the caller's gate.
    """
    from sqlalchemy import select

    from app.models import TenantUser, WorkspaceConnection, WorkspaceRole
    from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize

    if session is None:
        async with WorkspaceConnection.objects._get_owned_session() as owned:
            return await authorize_capability(
                tenant_id=tenant_id, actor_id=actor_id, capability=capability, session=owned
            )
    try:
        if is_bypass() or capability not in CAPABILITY_PERMISSIONS:
            raise ConnectionDenied()
        await _authorize(session, tenant_id, actor_id)
        role = (
            await session.execute(
                select(WorkspaceRole.codename)
                .join(TenantUser, TenantUser.role_id == WorkspaceRole.id)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.user_id == actor_id,
                    TenantUser.channel == "web",
                    TenantUser.is_active.is_(True),
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if CAPABILITY_PERMISSIONS[capability] not in workspace_grants(role):
            raise ConnectionDenied()
        return role
    except ConnectionDenied:
        log_denial(tenant_id=tenant_id, actor_id=actor_id, capability=capability)
        raise


class OneCAuditView:
    """Bounded read projection over the existing audit manager, not another store."""

    async def read(
        self, *, tenant_id: int, actor_id: int, export: bool = False, event_actor_id: int | None = None, **filters
    ) -> list[dict]:
        from app.models import ActionAuditEvent
        from app.models.managers.workspace_connection_manager import ConnectionDenied

        capability = "onec.export" if export else "onec.query"
        async with ActionAuditEvent.objects._get_owned_session() as session:
            role = await authorize_capability(
                session=session, tenant_id=tenant_id, actor_id=actor_id, capability=capability
            )
            action = ActionType.EXPORT if export else ActionType.VIEW
            if ("actionauditevent", action) not in workspace_grants(role):
                log_denial(tenant_id=tenant_id, actor_id=actor_id, capability=capability)
                raise ConnectionDenied()
            events = await ActionAuditEvent.objects.list_events(
                session=session, onec_only=True, actor_id=event_actor_id, **filters
            )
            rows = [
                {
                    "id": event.id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "outcome": event.outcome,
                    "resource_kind": event.resource_kind,
                    "resource_id": event.resource_id,
                    "reason_code": event.reason_code,
                }
                for event in events
            ]
            if export:
                await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=actor_id,
                    action="onec.audit_export",
                    resource_kind="workspace",
                    resource_id=tenant_id,
                )
            return rows
