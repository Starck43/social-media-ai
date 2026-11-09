"""Five-minute pulls; persisted connection owner is the only scheduler actor."""

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import User, WorkspaceConnection
from app.services.calendar.service import PROVIDERS, calendar_service
from app.services.planner.tasks import planner_service
from app.types import ActionType


async def handle_calendar_sync(payload):
    tenant = current_tenant_id()
    if is_bypass() or type(tenant) is not int or tenant <= 0:
        raise PermissionError("calendar_denied")
    # Payload cannot select a different tenant, actor or connection.
    if payload:
        raise ValueError("calendar_sync_payload_forbidden")
    async with async_session_maker() as session:
        ids = list(
            (
                await session.scalars(
                    select(WorkspaceConnection.id)
                    .where(
                        WorkspaceConnection.tenant_id == tenant,
                        WorkspaceConnection.provider_id.in_(PROVIDERS),
                        WorkspaceConnection.is_active.is_(True),
                        WorkspaceConnection.state == "verified",
                    )
                    .order_by(WorkspaceConnection.id)
                    .limit(100)
                )
            ).all()
        )
    runs = []
    for connection_id in ids:
        async with async_session_maker() as session, session.begin():
            # First read determines only whom to reauthorize. The service locks
            # actor/workspace/membership and rechecks connection ownership.
            conn = await session.scalar(
                select(WorkspaceConnection).where(
                    WorkspaceConnection.tenant_id == tenant, WorkspaceConnection.id == connection_id
                )
            )
            if conn is None:
                continue
            user = await session.scalar(select(User).where(User.id == conn.owner_user_id, User.is_active.is_(True)))
            if user is None:
                continue
            try:
                with permission_scope(user):
                    await planner_service._actor(session, ActionType.UPDATE)
                    runs.append(await calendar_service.sync(connection_id, session=session))
            except (PermissionError, LookupError):
                runs.append({"state": "owner_unavailable", "events": 0})
    return {"status": "ok" if runs else "skipped", "runs": runs}
