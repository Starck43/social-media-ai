"""Shared preparation of canonical task-plan creates.

Transport adapters retain their own authorization, validation errors and payer
consent. This module owns task-create transaction helpers and makes a plan's
persisted trigger, scenario, payload and source scope identical at each surface.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.services.task_plans import TaskPlan, TaskSourceScope, apply_task_plan, parse_task_plan

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


@dataclass(frozen=True)
class TaskPlanCreate:
    """Canonical persisted values owned by a task plan."""

    payload: dict[str, Any] | None
    cron_expr: str
    scenario_id: int | None
    source_scope: TaskSourceScope
    source_ids: tuple[int, ...]
    plan: TaskPlan
    raw_plan: Mapping[str, Any]


class TaskPlanCreateConflict(ValueError):
    """A legacy create field disagrees with the authoritative task plan."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


def resolve_task_plan_create(
    raw_plan: Mapping[str, Any],
    *,
    requested_cron: str | None = None,
    requested_scenario_id: int | None = None,
    requested_source_ids: Sequence[int] | None = None,
    enforce_source_match: bool = False,
    conflict_order: Sequence[str] = ("cron_expr", "scenario_id", "source_ids"),
) -> TaskPlanCreate:
    """Parse a plan and resolve its canonical fields without projecting payload.

    Early adapter validation can use this before job-type/collection checks that
    historically precede plan application. No authorization or storage occurs.
    """
    plan = parse_task_plan(raw_plan)
    cron_expr = "@once" if plan.trigger.type == "once" else plan.trigger.cron
    mismatches = {
        "cron_expr": requested_cron is not None and requested_cron != cron_expr,
        "scenario_id": requested_scenario_id is not None and requested_scenario_id != plan.scenario_id,
        "source_ids": (
            enforce_source_match
            and requested_source_ids is not None
            and list(requested_source_ids) != list(plan.source_ids)
        ),
    }
    for field in conflict_order:
        if field not in mismatches:
            raise ValueError(f"Unknown task-plan create conflict field: {field}")
        if mismatches[field]:
            raise TaskPlanCreateConflict(field)

    return TaskPlanCreate(
        payload=None,
        cron_expr=cron_expr,
        scenario_id=plan.scenario_id,
        source_scope=plan.source_scope,
        source_ids=plan.source_ids,
        plan=plan,
        raw_plan=dict(raw_plan),
    )


def project_task_plan_create(payload: Mapping[str, Any], creation: TaskPlanCreate, *, job_type: str) -> TaskPlanCreate:
    """Apply a resolved plan at the adapter's existing payload-write point."""
    return replace(creation, payload=apply_task_plan(dict(payload), creation.raw_plan, job_type=job_type))


def prepare_task_plan_create(
    payload: Mapping[str, Any],
    raw_plan: Mapping[str, Any],
    *,
    job_type: str,
    requested_cron: str | None = None,
    requested_scenario_id: int | None = None,
    requested_source_ids: Sequence[int] | None = None,
    enforce_source_match: bool = False,
    conflict_order: Sequence[str] = ("cron_expr", "scenario_id", "source_ids"),
) -> TaskPlanCreate:
    """Resolve and project canonical task-plan create values for one surface."""
    creation = resolve_task_plan_create(
        raw_plan,
        requested_cron=requested_cron,
        requested_scenario_id=requested_scenario_id,
        requested_source_ids=requested_source_ids,
        enforce_source_match=enforce_source_match,
        conflict_order=conflict_order,
    )
    return project_task_plan_create(payload, creation, job_type=job_type)
