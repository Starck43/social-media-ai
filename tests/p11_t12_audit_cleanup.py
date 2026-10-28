"""Teardown helper for fixtures that delete a workspace after task admission.

P11-T12 gives `action_audit_events.tenant_id` a deliberate `ON DELETE RESTRICT`
foreign key: evidence outlives a careless tenant delete, so a fixture that
removes a workspace must clear that workspace's events first. The only
supported delete path is `ActionAuditEventManager.delete_expired`, and it needs
rows past the workspace plan's retention window. A test cannot wait 7-90 days,
so the fixture ages its own rows and then lets the manager expire them: the plan
read, the retention marker and the guarded delete all stay exercised.

`occurred_at` accuracy is the event's only value, so aging is the one place a
fixture bypasses the immutability trigger, and it does so deliberately: the
trigger is disabled around the aging UPDATE only. Disabling it globally would
let a concurrent test's evidence be rewritten, and `session_replication_role`
is not grantable to the application role.

Fallback: `delete_expired` needs a readable plan and an existing workspace. A
teardown that runs after a failed case may find neither, so it then deletes the
workspace's aged rows with the same transaction-local retention marker the
manager sets; the immutability trigger stays enabled and the tenant goes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, text, update

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import ActionAuditEvent
from app.models.managers.action_audit_event_manager import ActionAuditEventManager

_GUARD = "action_audit_events_immutable"


async def purge_tenant_audit_events(tenant_id: int) -> None:
    """Expire every action-audit event of one fixture-owned workspace."""
    table = ActionAuditEvent.__table__
    qualified = f'"{table.schema}"."{table.name}"'
    aged = datetime.now(timezone.utc) - timedelta(days=400)  # beyond every plan window
    with tenant_scope(tenant_id):
        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(text(f"ALTER TABLE {qualified} DISABLE TRIGGER {_GUARD}"))
                await session.execute(
                    update(ActionAuditEvent).where(ActionAuditEvent.tenant_id == tenant_id).values(occurred_at=aged)
                )
                await session.execute(text(f"ALTER TABLE {qualified} ENABLE TRIGGER {_GUARD}"))
        while True:
            try:
                if not await ActionAuditEventManager().delete_expired(now=datetime.now(timezone.utc)):
                    return
            except TenantContextError:
                break  # the workspace itself is already gone
        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
                await session.execute(delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id == tenant_id))
