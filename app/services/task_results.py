"""Bounded, permission-aware task outcomes for chat and machine interfaces."""

from __future__ import annotations

from typing import Any

from app.core.permissions import get_current_user, has_permission, require_permission
from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import ActionType


def outcome_counts(result: Any) -> dict[str, int]:
    """Expose counters only; diagnostics, prompts and provider bodies stay private."""
    if not isinstance(result, dict):
        return {}
    return {
        key: result[key]
        for key in ("sources", "items", "analyzed", "collected", "skipped", "error")
        if type(result.get(key)) is int and 0 <= result[key] <= 1_000_000_000
    }


async def _task_definition(task: Any) -> dict[str, Any]:
    """Return an allowlisted task view and its canonical saved plan only."""
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.task_plans import TaskPlanError, read_saved_task_plan, task_plan_block

    payload = task.payload if isinstance(task.payload, dict) else {}
    result: dict[str, Any] = {
        "id": task.id,
        "name": task.name,
        "job_type": task.job_type,
        "active": task.is_active is True,
        "last_status": task.last_status,
        "cron": task.cron_expr,
        "task_url": f"/app/tasks/{task.id}",
        "plan": None,
        "plan_status": "legacy",
    }
    try:
        parsed = read_saved_task_plan(payload)
    except (TaskPlanError, TypeError, ValueError):
        result["plan_status"] = "unavailable"
        return result
    if parsed is None:
        return result

    actor = get_current_user()
    if not has_permission(actor, "source", ActionType.VIEW):
        result["plan_status"] = "source_access_required"
        return result
    if parsed.scenario_id is not None and not has_permission(actor, "agentscenario", ActionType.VIEW):
        result["plan_status"] = "scenario_access_required"
        return result

    plan = task_plan_block(parsed)
    # The relationship table is the execution source of truth. Refuse to hand
    # a stale persisted plan back as editable when it disagrees with the links.
    if parsed.source_scope == "selected":
        linked_ids = await AgentTaskManager().get_sources(task.id)
        if sorted(parsed.source_ids) != linked_ids:
            result["plan_status"] = "scope_changed"
            return result
        from app.models import Source

        visible_sources = await Source.objects.filter(id__in=linked_ids, is_active=True)
        if {source.id for source in visible_sources} != set(linked_ids):
            result["plan_status"] = "source_unavailable"
            return result
    result["plan"] = plan
    result["plan_status"] = "available"
    return result


@require_permission("agenttask", ActionType.VIEW)
async def task_configuration(task_id: int) -> dict | None:
    """Read task-editing inputs without returning raw payload/provider fields."""
    from app.models import AgentTask

    if type(task_id) is not int or task_id <= 0:
        raise ValueError("A persisted task ID is required")
    if type(current_tenant_id()) is not int or is_bypass():
        raise PermissionError("A concrete workspace is required")
    task = await AgentTask.objects.get(id=task_id)
    return await _task_definition(task) if task is not None else None


@require_permission("agenttask", ActionType.VIEW)
async def task_result(task_id: int, *, job_id: int | None = None, limit: int = 5) -> dict | None:
    """Read one task's retained run and permitted analyses without launching work."""
    from app.models import AgentTask, AIAnalytics, Job

    if type(task_id) is not int or task_id <= 0:
        raise ValueError("A persisted task ID is required")
    if job_id is not None and (type(job_id) is not int or job_id <= 0):
        raise ValueError("Invalid job ID")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Result limit must be between 1 and 10")
    if type(current_tenant_id()) is not int or is_bypass():
        raise PermissionError("A concrete workspace is required")
    task = await AgentTask.objects.get(id=task_id)
    if task is None:
        return None
    jobs = Job.objects.filter(agent_task_id=task.id)
    job = await jobs.filter(id=job_id).first() if job_id is not None else await jobs.order_by(Job.id.desc()).first()
    if job_id is not None and job is None and task.launch_job_id != job_id:
        return None
    result: dict[str, Any] = {
        "task_id": task.id,
        "status": task.last_status,
        "task_url": f"/app/tasks/{task.id}",
        "job_id": job.id if job is not None else task.launch_job_id,
        "job_status": job.status if job is not None else "unavailable",
        "counts": {},
        "analyses": [],
        "coverage": "not_available" if job is None else "partial_or_bounded",
        "task": await _task_definition(task),
    }
    if job is None:
        return result
    raw = job.result if isinstance(job.result, dict) else {}
    result["counts"] = outcome_counts(raw)
    from app.jobs.progress import read_snapshot

    progress = read_snapshot(raw, getattr(job, "attempts", None))
    if progress is not None:
        if has_permission(get_current_user(), "source", ActionType.VIEW):
            result["progress"] = progress
        else:
            # Keep useful aggregate status while hiding source identities.
            result["progress"] = {key: progress[key] for key in ("phase", "total", "completed", "updated_at")}
    actor = get_current_user()
    if not (has_permission(actor, "source", ActionType.VIEW) and has_permission(actor, "aianalytics", ActionType.VIEW)):
        return result
    ids: list[int] = []
    sources: set[int] = set()
    per_source = raw.get("per_source")
    for row in per_source[:100] if isinstance(per_source, list) else ():
        if not isinstance(row, dict) or type(row.get("source_id")) is not int:
            continue
        sources.add(row["source_id"])
        candidates = row.get("analytics_ids")
        for value in candidates[:100] if isinstance(candidates, list) else ():
            if type(value) is int and value > 0 and value not in ids:
                ids.append(value)
            if len(ids) >= 100:
                break
        if len(ids) >= 100:
            break
    if not ids:
        return result
    rows = await (
        AIAnalytics.objects.filter(id__in=ids, source_id__in=list(sources)).order_by(AIAnalytics.id.desc()).limit(limit)
    )
    for row in rows:
        summary = row.summary_data if isinstance(row.summary_data, dict) else {}
        title = summary.get("analysis_title")
        text = summary.get("summary")
        result["analyses"].append(
            {
                "id": row.id,
                "title": title[:200] if isinstance(title, str) else f"Analysis #{row.id}",
                "summary": text[:2_000] if isinstance(text, str) else None,
                "url": f"/app/analytics/{row.id}",
            }
        )
    return result
