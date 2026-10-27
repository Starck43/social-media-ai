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


def _pipeline_outcome(raw: Any, *, can_view_preview: bool) -> dict[str, Any] | None:
    """Project the current composed-run receipt without provider diagnostics."""
    if not isinstance(raw, dict):
        return None
    pipeline = raw.get("pipeline")
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
    if not isinstance(stages, dict):
        # Final job results may expose the same stages directly.
        stages = raw.get("stages")
    if not isinstance(stages, dict):
        return None
    projected: dict[str, Any] = {"version": 1, "stages": {}}
    for name in ("collect", "analyze", "digest"):
        value = stages.get(name)
        if not isinstance(value, dict):
            continue
        state = value.get("state", value.get("status"))
        if state not in {"started", "complete", "completed", "failed", "skipped", "review_required", "unknown"}:
            state = "unknown"
        receipt = value.get("result") if isinstance(value.get("result"), dict) else value
        stage: dict[str, Any] = {"status": "complete" if state == "completed" else state}
        counts = outcome_counts(receipt)
        if type(receipt.get("analysis_count")) is int and 0 <= receipt["analysis_count"] <= 1_000_000_000:
            counts["analysis_count"] = receipt["analysis_count"]
        if counts:
            stage["counts"] = counts
        if name == "digest":
            for key in ("report_start", "report_end", "group_by"):
                item = receipt.get(key)
                if isinstance(item, str) and len(item) <= 80:
                    stage[key] = item
            if type(receipt.get("time_breakdown")) is bool:
                stage["time_breakdown"] = receipt["time_breakdown"]
            if receipt.get("generated") is True or state in {"complete", "completed"}:
                stage["generated"] = True
                stage["generated_count"] = 1
            delivered = receipt.get("delivered")
            if type(delivered) is int and 0 <= delivered <= 1_000_000:
                stage["delivered"] = delivered
            preview = receipt.get("preview_text", receipt.get("text"))
            if can_view_preview and isinstance(preview, str):
                stage["preview_text"] = preview[:5_000]
        projected["stages"][name] = stage
    return projected if projected["stages"] else None


async def task_pipeline_outcome(raw: Any, *, can_view_preview: bool = False) -> dict[str, Any] | None:
    """Project a pipeline and resolve its current delivery receipt for authorized readers."""
    result = _pipeline_outcome(raw, can_view_preview=can_view_preview)
    if result is None or not can_view_preview or not isinstance(raw, dict):
        return result
    digest = result["stages"].get("digest")
    reference = raw.get("task_digest_delivery")
    if not isinstance(digest, dict) or not isinstance(reference, dict):
        return result
    run_id = reference.get("run_id")
    generation = reference.get("generation")
    if type(run_id) is not int or run_id <= 0 or not isinstance(generation, str) or not generation or len(generation) > 128:
        return result
    from app.services.task_digest_delivery import read_task_digest_delivery

    delivery = await read_task_digest_delivery({"run_id": run_id, "generation": generation})
    if not isinstance(delivery, dict):
        return result
    state = delivery.get("status")
    if state not in {"sent", "uncertain", "blocked", "partial", "failed", "requires_review", "unavailable"}:
        return result
    safe_delivery: dict[str, Any] = {"status": state}
    for key in ("sent_parts", "total_parts", "uncertain_parts", "blocked_parts", "rejected_parts", "pending_parts"):
        value = delivery.get(key)
        if type(value) is int and 0 <= value <= 100_000:
            safe_delivery[key] = value
    digest["delivery"] = safe_delivery
    if type(safe_delivery.get("sent_parts")) is int:
        digest["delivered"] = safe_delivery["sent_parts"]
    return result


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
    actor = get_current_user()
    can_view_research = has_permission(actor, "source", ActionType.VIEW) and has_permission(
        actor, "aianalytics", ActionType.VIEW
    )
    if can_view_research:
        result["pipeline"] = await task_pipeline_outcome(
            raw,
            can_view_preview=has_permission(actor, "digestrun", ActionType.VIEW),
        )
    from app.jobs.progress import read_snapshot

    progress = read_snapshot(raw, getattr(job, "attempts", None))
    if progress is not None:
        if has_permission(get_current_user(), "source", ActionType.VIEW):
            result["progress"] = progress
        else:
            # Keep useful aggregate status while hiding source identities.
            result["progress"] = {key: progress[key] for key in ("phase", "total", "completed", "updated_at")}
    if not can_view_research:
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
