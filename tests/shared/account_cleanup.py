"""Teardown helper: delete a synthetic account in the accepted lifecycle order.

`User.objects.delete_user` refuses (409) accounts that sponsor workspaces or
hold tariff history. Fixture-owned accounts sponsor their workspace, so
teardown must follow the application's prescribed order: drop the workspace's
RESTRICT-referencing evidence first (payer consents, action-audit events),
then the workspace itself, then the account's tariff rows, then the account.
Redundant tenant deletion is safe: `delete_by_id` reports False for a missing
row.
"""

from __future__ import annotations

from sqlalchemy import delete

from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope


async def purge_account(user_id: int) -> None:
    """Resolve one fixture-owned account's lifecycle, then delete the account."""
    from app.models import Tenant, UserServicePlan, User
    from tests.p11_t12_audit_cleanup import purge_tenant_audit_events
    from tests.shared.payer_cleanup import purge_task_payer_consents

    with tenant_scope(bypass=True), operator_permission_scope():
        sponsored = await Tenant.objects.filter(plan_holder_user_id=user_id).all()
    for tenant in sponsored:
        await purge_task_payer_consents(tenant.id)
        await purge_tenant_audit_events(tenant.id)
        with tenant_scope(bypass=True), operator_permission_scope():
            await Tenant.objects.delete_by_id(tenant.id)
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(UserServicePlan).where(UserServicePlan.user_id == user_id))
    with tenant_scope(bypass=True), operator_permission_scope():
        await User.objects.delete_user(user_id)
