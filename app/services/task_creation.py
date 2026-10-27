"""Shared preparation of canonical task-plan creates.

Transport adapters retain their own authorization, validation errors,
transactions, payer consent and persistence. This use case makes a plan's
persisted trigger, scenario, payload and source scope identical at each surface.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from app.services.task_plans import TaskPlan, TaskSourceScope, apply_task_plan, parse_task_plan


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
