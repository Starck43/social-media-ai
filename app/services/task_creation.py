"""Task-create persistence and backward-compatible preparation exports.

Pure preparation lives in task_preparation. Lazy ORM imports below are explicit
persistence boundaries, not dependencies of that core. Adapters retain their
existing imports, authorization, payer consent and transaction ownership.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.services.task_preparation import TaskPlanCreate as TaskPlanCreate
from app.services.task_preparation import TaskPlanCreateConflict as TaskPlanCreateConflict
from app.services.task_preparation import prepare_task_plan_create as prepare_task_plan_create
from app.services.task_preparation import project_task_plan_create as project_task_plan_create
from app.services.task_preparation import resolve_task_plan_create as resolve_task_plan_create

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.scheduling.agent_task import AgentTask


async def persist_task_create(
    session: "AsyncSession",
    *,
    task_values: Mapping[str, Any],
    source_ids: Sequence[int] | None = None,
    tenant_id: int | None = None,
) -> "AgentTask":
    """Persist one task and its optional source links in the caller's UoW.

    The caller must already own an active transaction and the permissions needed
    for each manager call. Keeping the session explicit makes a failed link
    write roll back the task row as well. The optional tenant id must match the
    task tenant resolved by BaseManager, including its bypass/default path.
    """
    if not session.in_transaction():
        raise RuntimeError("Task creation requires an active transaction")

    from app.models.managers.agent_task_manager import AgentTaskManager

    manager = AgentTaskManager()
    stamped = await manager._apply_tenant(session, dict(task_values))
    resolved_tenant_id = stamped.get("tenant_id")
    if tenant_id is not None and tenant_id != resolved_tenant_id:
        raise ValueError("Task workspace changed during creation")
    if resolved_tenant_id is not None:
        from app.models.managers.job_manager import JobManager

        await JobManager.lock_source_admission(session, resolved_tenant_id)
    elif source_ids is not None:
        raise ValueError("Task source writes require an explicit workspace")
    from app.models import AgentTask

    task = await AgentTask.objects.create(session=session, **stamped)
    if source_ids is not None:
        await AgentTaskManager().set_sources(task.id, list(source_ids), session=session)
    return task


async def create_task_with_sources(
    *, task_values: Mapping[str, Any], source_ids: Sequence[int] | None, tenant_id: int | None
) -> "AgentTask":
    """Own the transaction for task creation and its optional source links.

    Adapters with distinct create/link permission scopes use the explicit-session
    `persist_task_create` primitive inside their existing transaction instead.
    """
    from app.core.database import new_session

    session = new_session()
    try:
        async with session.begin():
            return await persist_task_create(
                session, task_values=task_values, source_ids=source_ids, tenant_id=tenant_id
            )
    finally:
        await session.close()
