"""Digest job implementation with explicit facade dependencies."""

from typing import Any


async def handle_digest(payload: dict[str, Any], *, load_task, task_payload) -> dict[str, Any]:
    """Build a digest and publish it to configured channels.

    Payload:
        period: 'day' | 'week' | 'month' (default 'day')
        agent_task_id: int | None — set when triggered by a task (idempotency)
        scenario_id: int | None — explicit scenario override
        group_by: str | None — grouping axis override (themes|sources|entities|sentiment|content_type|intent)
        time_breakdown: bool — enable per-date sub-entries within each group

    Hybrid flow: step 1 builds the algorithmic brief from the task's own sources
    grouped by the `group_by` axis, step 2 asks the LLM for a narrative over
    that brief, step 3 publishes the result to every tenant channel with
    `is_digest_target=True` (plus env-configured channels).

    # TODO(future): per-source digest. If task.sources is non-empty — filter the
    # aggregation by them (source_ids support is wired into ReportAggregator).
    # Empty sources list = the whole workspace (current behaviour).
    """
    from app.services.digest.builder import build_and_publish

    task = await load_task(payload.get("agent_task_id"))
    resolved_task_payload = task_payload(task)

    period = resolved_task_payload.get("period", payload.get("period", "day"))
    if period not in ("day", "week", "month"):
        return {"status": "failed", "error": f"Invalid period: {period}"}
    # The task's own sources scope the aggregation; None = whole workspace (the
    # ReportAggregator reads through the tenant guard either way).
    source_ids = [s.id for s in (task.sources or [])] if task is not None else None

    # The group_by axis tells the aggregator how to group the brief.
    scenario_id = resolved_task_payload.get("scenario_id") or payload.get("scenario_id")
    if not scenario_id and task is not None:
        scenario_id = getattr(task, "agent_scenario_id", None)
    group_by = resolved_task_payload.get("group_by") or payload.get("group_by") or "themes"
    time_breakdown = bool(resolved_task_payload.get("time_breakdown") or payload.get("time_breakdown"))

    # `--force-refresh` re-sends the digest even when it was already sent for
    # this task+period (skips the idempotency check).
    force = bool(resolved_task_payload.get("force_refresh") or payload.get("force_refresh"))
    return await build_and_publish(
        period=period,
        agent_task_id=payload.get("agent_task_id"),
        force=force,
        source_ids=source_ids,
        group_by=group_by,
        time_breakdown=time_breakdown,
        scenario_id=scenario_id,
    )
