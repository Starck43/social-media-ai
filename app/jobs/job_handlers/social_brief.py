"""Scheduled social morning brief producer for the shared D18 notification ledger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import TenantUser, User, WorkspaceRole
from app.services.social.brief_schedule import (
    publish_dispatch,
    schedule_for_task,
    verify_occurrence_token,
)


def _aware_datetime(value):
    if not isinstance(value, str):
        raise ValueError("brief_scheduled_for_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("brief_scheduled_for_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("brief_scheduled_for_invalid")
    return parsed


async def _recipient(tenant_id: int, user_id: int):
    async with async_session_maker() as session:
        user = await session.scalar(
            select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update(read=True)
        )
        membership = await session.scalar(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.channel == "web",
                TenantUser.is_active.is_(True),
                TenantUser.role_id.is_not(None),
            )
        )
        role = None if membership is None else await session.get(WorkspaceRole, membership.role_id)
    if user is None or membership is None or role is None:
        return None
    from app.core.access_catalog import workspace_grants

    return user, role.codename, workspace_grants(role.codename)


async def handle_social_brief(payload, *, load_task):
    """Recheck current task signature and actor/source rights before publishing."""
    tenant_id = current_tenant_id()
    task_id = payload.get("agent_task_id") if isinstance(payload, dict) else None
    if type(task_id) is not int or task_id <= 0:
        task_id = None
    if is_bypass() or type(tenant_id) is not int or task_id is None:
        raise PermissionError("social_brief_workspace_required")
    task = await load_task(task_id)
    if task is None or task.tenant_id != tenant_id or not task.is_active:
        return {"status": "skipped", "reason": "schedule_unavailable"}
    schedule = schedule_for_task(task)
    current = task.payload if isinstance(task.payload, dict) else {}
    if payload.get("kind") != current.get("kind") or payload.get("signed_config") != current.get("signed_config"):
        return {"status": "skipped", "reason": "schedule_changed"}
    scheduled_for = _aware_datetime(payload.get("scheduled_for"))
    if not verify_occurrence_token(
        payload.get("occurrence_token"),
        tenant_id=tenant_id,
        task_id=task_id,
        config_token=current["signed_config"],
        scheduled_for=scheduled_for,
    ):
        return {"status": "skipped", "reason": "occurrence_invalid"}
    recipient = await _recipient(tenant_id, schedule.user_id)
    if recipient is None:
        return {"status": "skipped", "reason": "recipient_unavailable"}
    user, role, grants = recipient
    from app.core.permissions import permission_scope

    try:
        with permission_scope(user, is_owner=role == "owner", member_grants=grants):
            notification_id = await publish_dispatch(
                schedule,
                task_id=task_id,
                task_config_token=current["signed_config"],
                scheduled_for=scheduled_for,
            )
    except PermissionError:
        return {"status": "skipped", "reason": "actor_or_source_access_revoked"}
    return {"status": "queued", "notification_id": notification_id}
