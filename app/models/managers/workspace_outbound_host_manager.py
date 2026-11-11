"""Workspace host approvals, independent of connector and installation trust.

The caller must keep the supplied transaction open through a network attempt
when using require_approved(): its shared row lock serializes host revocation.
The connector remains responsible for fresh DNS and its own egress policy.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.integrations.workspace_outbound_host import WorkspaceOutboundHost
from app.models.managers.base_manager import BaseManager
from app.models.managers.trusted_outbound_host_manager import validate_host
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole


class WorkspaceHostError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def _require_admin(session, tenant_id, actor_id):
    # _authorize locks and revalidates actor, active tenant and membership.
    await _authorize(session, tenant_id, actor_id, write=True)
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
            .with_for_update(read=True, of=WorkspaceRole)
        )
    ).scalar_one_or_none()
    if role not in {"owner", "admin"}:
        raise ConnectionDenied()


class WorkspaceOutboundHostManager:
    def __init__(self):
        self._sessions = BaseManager(WorkspaceOutboundHost)

    async def list_hosts(self, *, tenant_id, actor_id):
        async with self._sessions._get_owned_session() as session:
            await _require_admin(session, tenant_id, actor_id)
            rows = (
                (
                    await session.execute(
                        select(WorkspaceOutboundHost)
                        .where(WorkspaceOutboundHost.tenant_id == tenant_id)
                        .order_by(WorkspaceOutboundHost.host)
                    )
                )
                .scalars()
                .all()
            )
            return [{"id": row.id, "host": row.host} for row in rows]

    async def approve(self, *, tenant_id, actor_id, host):
        normalized = validate_host(host)
        try:
            async with self._sessions._get_owned_session() as session:
                await _require_admin(session, tenant_id, actor_id)
                row = WorkspaceOutboundHost(tenant_id=tenant_id, host=normalized, approved_by_user_id=actor_id)
                session.add(row)
                await session.flush()
                return {"id": row.id, "host": row.host}
        except IntegrityError:
            raise WorkspaceHostError("host_already_approved") from None

    async def revoke(self, *, tenant_id, actor_id, host_id):
        if type(host_id) is not int or host_id <= 0:
            raise ConnectionDenied()
        async with self._sessions._get_owned_session() as session:
            await _require_admin(session, tenant_id, actor_id)
            row = (
                await session.execute(
                    select(WorkspaceOutboundHost)
                    .where(WorkspaceOutboundHost.tenant_id == tenant_id, WorkspaceOutboundHost.id == host_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise ConnectionDenied()
            await session.delete(row)
            await session.flush()

    async def require_approved(self, *, session, tenant_id, actor_id, host):
        """Authorize this actor and lock one exact approval for the caller's transaction."""
        normalized = validate_host(host)
        await _authorize(session, tenant_id, actor_id, write=True)
        row = (
            await session.execute(
                select(WorkspaceOutboundHost.id)
                .where(WorkspaceOutboundHost.tenant_id == tenant_id, WorkspaceOutboundHost.host == normalized)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ConnectionDenied()
        return normalized
