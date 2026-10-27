"""Claim-fenced collect/analyze/digest execution for versioned task plans."""

from __future__ import annotations

import re
from datetime import date
from typing import Any


class PipelineReviewRequired(RuntimeError):
    """An interrupted external stage has an unknown outcome and needs review."""


def _counts(value: Any, keys: tuple[str, ...]) -> dict[str, int | None]:
    return {key: (value.get(key) if type(value.get(key)) is int and value.get(key) >= 0 else None) for key in keys}


def _safe_collection_result(value: dict) -> dict:
    per_source = []
    truncated = False
    unsupported = False
    result = {
        "status": "partial" if value.get("error") or value.get("rate_limited") else "complete",
        "counts": _counts(value, ("sources", "items", "new_items", "error", "rate_limited", "skipped")),
        "per_source": per_source,
    }
    for row in value.get("per_source", []):
        if not isinstance(row, dict):
            continue
        item = {"source_id": row.get("source_id") if type(row.get("source_id")) is int else None}
        for key in ("items", "new_items", "error", "rate_limited", "skipped"):
            if type(row.get(key)) is int and row[key] >= 0:
                item[key] = row[key]
        if row.get("outcome") in {"collected", "empty", "error", "auth_required", "rate_limited"}:
            item["outcome"] = row["outcome"]
        hashes = row.get("content_hashes")
        if isinstance(hashes, list):
            unique = list(
                dict.fromkeys(
                    value for value in hashes if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                )
            )
            item["content_hashes"] = unique[:2000]
            if len(unique) > 2000:
                item["hashes_truncated"] = True
                truncated = True
        selection = row.get("selection")
        selection = selection if isinstance(selection, dict) else row
        item["selection"] = {
            key: selection[source_key]
            for key, source_key in (
                ("eligible", "selected"),
                ("needs_review", "needs_review"),
                ("rejected", "rejected"),
            )
            if type(selection.get(source_key)) is int and selection[source_key] >= 0
        }
        context = row.get("collection")
        if isinstance(context, dict):
            warnings = context.get("warnings")
            item["collection"] = {
                "platform": context.get("platform") if context.get("platform") in {"vk", "telegram"} else None,
                "warnings": (
                    [
                        warning[:80]
                        for warning in warnings[:20]
                        if isinstance(warning, str) and re.fullmatch(r"[a-z0-9_]{1,80}", warning)
                    ]
                    if isinstance(warnings, list)
                    else []
                ),
            }
            unsupported = unsupported or bool(item["collection"]["warnings"])
        if item["selection"].get("needs_review", 0) or item["selection"].get("rejected", 0):
            unsupported = True
        result["per_source"].append(item)
    if truncated:
        result["hashes_truncated"] = True
    if truncated or unsupported:
        result["status"] = "partial"
    return result


def _safe_analysis_result(value: dict) -> dict:
    result = {
        "status": "partial" if value.get("error") or value.get("staged_errors") else "complete",
        "counts": _counts(value, ("sources", "analyzed", "skipped", "error", "staged_errors", "actions")),
        "per_source": [],
    }
    for row in value.get("per_source", []):
        if not isinstance(row, dict):
            continue
        item = {"source_id": row.get("source_id") if type(row.get("source_id")) is int else None}
        for key in ("analyzed", "actions", "raw_total", "raw_selected", "raw_processed"):
            if type(row.get(key)) is int and row[key] >= 0:
                item[key] = row[key]
        for key in ("error", "staged_error", "collection_error"):
            if row.get(key) is True:
                item[key] = True
        analytics_ids = row.get("analytics_ids")
        if isinstance(analytics_ids, list):
            item["analytics_ids"] = list(
                dict.fromkeys(value for value in analytics_ids if type(value) is int and value > 0)
            )[:100]
        result["per_source"].append(item)
    return result


def _checkpoint_base(*, job_id: int, task_id: int, attempt: int) -> dict:
    return {
        "version": 1,
        "job_id": job_id,
        "task_id": task_id,
        "attempt": attempt,
        "stages": {},
    }


def _valid_checkpoint(value: Any, *, job_id: int, task_id: int) -> dict | None:
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("job_id") != job_id
        or value.get("task_id") != task_id
        or not isinstance(value.get("stages"), dict)
    ):
        return None
    stages = {}
    for name, row in value["stages"].items():
        if name not in {"collect", "analyze", "digest"} or not isinstance(row, dict):
            continue
        state = row.get("state")
        if state not in {"started", "completed", "review_required"}:
            continue
        stages[name] = row
    return {
        "version": 1,
        "job_id": job_id,
        "task_id": task_id,
        "attempt": value.get("attempt") if type(value.get("attempt")) is int else 0,
        "stages": stages,
    }


async def _stage(checkpoint: dict, name: str, operation, *, attempt: int) -> tuple[dict, dict]:
    from app.jobs import progress

    previous = checkpoint["stages"].get(name)
    if isinstance(previous, dict):
        if previous.get("state") == "completed":
            return checkpoint, previous.get("result") if isinstance(previous.get("result"), dict) else {}
        if previous.get("state") in {"started", "review_required"}:
            checkpoint["attempt"] = attempt
            checkpoint["stages"][name] = {
                "state": "review_required",
                "attempt": attempt,
                "error": "pipeline_stage_outcome_requires_review",
            }
            await progress.pipeline_checkpoint(checkpoint)
            raise PipelineReviewRequired("pipeline_stage_outcome_requires_review")

    checkpoint["attempt"] = attempt
    checkpoint["stages"][name] = {"state": "started", "attempt": attempt}
    await progress.pipeline_checkpoint(checkpoint)
    result = await operation()
    safe_result = result if isinstance(result, dict) else {}
    checkpoint["stages"][name] = {"state": "completed", "attempt": attempt, "result": safe_result}
    await progress.pipeline_checkpoint(checkpoint)
    return checkpoint, safe_result


async def _authorized_task(task, payload: dict, *, stage: str) -> int:
    """Resolve a current workspace member and recheck stage grants and task payer."""
    from app.core.permissions import has_permission, permission_scope
    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, User
    from app.models.managers.tenant_manager import tenant_users
    from app.types import ActionType

    tenant_id = current_tenant_id()
    if tenant_id != task.tenant_id:
        raise PermissionError("pipeline_workspace_mismatch")
    live_task = await AgentTask.objects.filter(id=task.id, is_active=True).first()
    if live_task is None or live_task.tenant_id != tenant_id:
        raise PermissionError("pipeline_task_inactive")
    from app.core.personal_catalog_context import current_catalog_owner_id
    from app.services.billing.payer import resolve_scheduled_task_payer

    payer = await resolve_scheduled_task_payer(task_id=task.id, tenant_id=task.tenant_id)
    if current_catalog_owner_id() != payer.user_id:
        raise PermissionError("pipeline_payer_changed")
    actor_id = payload.get("_trusted_initiated_by_user_id") or payer.user_id
    if type(actor_id) is not int or actor_id <= 0:
        raise PermissionError("pipeline_actor_unavailable")
    actor = await User.objects.get(id=actor_id, is_active=True)
    memberships = await tenant_users.web_memberships(actor_id) if actor is not None else []
    member = next((row for row in memberships if row.tenant_id == task.tenant_id and row.is_active), None)
    if actor is None or member is None:
        raise PermissionError("pipeline_membership_revoked")
    grants = await tenant_users.member_grants(member)
    needed = {("agenttask", ActionType.VIEW), ("agentscenario", ActionType.VIEW)}
    if stage == "digest":
        needed.update(
            {
                ("digestrun", ActionType.CREATE),
                ("source", ActionType.VIEW),
                ("aianalytics", ActionType.VIEW),
            }
        )
    else:
        needed.update({("agenttask", ActionType.UPDATE), ("source", ActionType.ANALYZE)})
    with permission_scope(actor, member_grants=grants):
        if any(not has_permission(actor, entity, action) for entity, action in needed):
            raise PermissionError("pipeline_permission_revoked")
    return actor_id


async def _require_active_scenario(tenant_id: int, task_payload: dict) -> None:
    plan = task_payload.get("task_plan")
    scenario_id = plan.get("scenario_id") if isinstance(plan, dict) else None
    if type(scenario_id) is not int:
        raise PermissionError("pipeline_scenario_unavailable")
    from app.models import AgentScenario

    scenario = await AgentScenario.objects.filter(id=scenario_id, is_active=True).first()
    if scenario is None or scenario.tenant_id != tenant_id:
        raise PermissionError("pipeline_scenario_unavailable")


async def _admit_entitlements(task_payload: dict, sources: list, job_id: int) -> None:
    from app.core.database import async_session_maker
    from app.models.managers.entitlement_manager import EntitlementManager
    from app.services.collection.entitlement_policy import required_capabilities

    async with async_session_maker() as session:
        required = await required_capabilities(session, task_payload, sources=sources)
    await EntitlementManager().admit_execution(required, job_id=job_id)


def _task_view(task, task_payload: dict, snapshot: dict):
    """Keep live task identity/relations while binding execution to admitted fields."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=task.id,
        tenant_id=task.tenant_id,
        payload=task_payload,
        sources=task.sources,
        agent_scenario=None,  # The admitted scenario_id is resolved explicitly.
        trigger_type=snapshot.get("trigger_type"),
        trigger_config=dict(snapshot.get("trigger_config") or {}),
        dry_run=bool(snapshot.get("dry_run")),
        action_type=snapshot.get("action_type"),
        rate_limit_per_hour=snapshot.get("rate_limit_per_hour"),
        cooldown_seconds=snapshot.get("cooldown_seconds"),
    )


def _report_window(plan: dict, task_payload: dict) -> tuple[date, date]:
    raw = task_payload.get("digest_effective_window")
    if not isinstance(raw, dict):
        digest = plan.get("digest") if isinstance(plan.get("digest"), dict) else {}
        raw = digest.get("window")
    if not isinstance(raw, dict) or raw.get("type") != "fixed":
        raise ValueError("pipeline_digest_window_not_materialized")
    try:
        start, end = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("pipeline_digest_window_invalid") from None
    if start > end or (end - start).days > 365:
        raise ValueError("pipeline_digest_window_invalid")
    return start, end


async def _generate_digest(
    task,
    plan: dict,
    task_payload: dict,
    source_ids: list[int],
    *,
    collection: dict,
    analysis: dict,
) -> dict:
    from app.core.tenant_context import current_tenant_id
    from app.services.ai.reporting import ReportScope, normalize_digest_axis
    from app.services.digest.builder import _summarize, aggregate, render_digest

    digest = plan.get("digest")
    if not isinstance(digest, dict):
        raise ValueError("pipeline_digest_definition_invalid")
    start, end = _report_window(plan, task_payload)
    if not source_ids:
        return {
            "status": "skipped",
            "error": "pipeline_digest_no_authorized_sources",
            "report_start": start.isoformat(),
            "report_end": end.isoformat(),
            "generated": False,
            "delivered": 0,
        }
    group_by = normalize_digest_axis(digest.get("group_by", "themes"))
    if group_by not in {"themes", "sources", "entities", "intent", "topic_chains"}:
        raise ValueError("pipeline_digest_group_invalid")
    time_breakdown = digest.get("time_breakdown") is True
    scenario_id = plan.get("scenario_id")
    scope = ReportScope(
        start=start,
        end=end,
        source_ids=frozenset(source_ids),
        scenario_id=scenario_id if type(scenario_id) is int else None,
        tenant_id=current_tenant_id(),
    )
    days = scope.days
    period = "day" if days == 1 else "week" if days == 7 else "month" if days == 30 else "custom"
    data, _, _ = await aggregate(
        period,
        source_ids,
        group_by,
        time_breakdown,
        scope.scenario_id,
        report_scope=scope,
    )
    coverage = []
    collection_counts = collection.get("counts", {}) if isinstance(collection, dict) else {}
    analysis_counts = analysis.get("counts", {}) if isinstance(analysis, dict) else {}
    if collection.get("status") == "partial":
        coverage.append(
            "Collection was partial: "
            f"{collection_counts.get('error') or 0} errors and "
            f"{collection_counts.get('rate_limited') or 0} rate-limited sources/items."
        )
    if analysis.get("status") == "partial":
        coverage.append(
            "Analysis was partial: "
            f"{analysis_counts.get('error') or 0} source errors and "
            f"{analysis_counts.get('staged_errors') or 0} staged-item errors."
        )
    unsupported = (
        sorted(
            {
                warning
                for row in collection.get("per_source", [])
                if isinstance(row, dict) and isinstance(row.get("collection"), dict)
                for warning in row["collection"].get("warnings", [])
                if isinstance(warning, str)
            }
        )
        if isinstance(collection, dict)
        else []
    )
    if unsupported:
        coverage.append("Unsupported collection operations: " + ", ".join(unsupported[:12]) + ".")
    selection_counts = (
        [
            row.get("selection", {})
            for row in collection.get("per_source", [])
            if isinstance(row, dict) and isinstance(row.get("selection"), dict)
        ]
        if isinstance(collection, dict)
        else []
    )
    rejected = sum(row.get("rejected", 0) for row in selection_counts)
    needs_review = sum(row.get("needs_review", 0) for row in selection_counts)
    if rejected or needs_review:
        coverage.append(f"Collection selection excluded {rejected} items and held {needs_review} for review.")
    if collection.get("hashes_truncated"):
        coverage.append("Collection item provenance exceeded the bounded receipt; only recorded items were analyzed.")
    if coverage:
        existing = data.get("coverage_note")
        data["coverage_note"] = "\n".join(([existing] if isinstance(existing, str) and existing else []) + coverage)
    summary, llm = await _summarize(data)
    data["llm"] = {**(data.get("llm") or {}), "model": llm.get("model")}
    text = render_digest(data, summary=summary)
    return {
        "status": "complete",
        "task_id": task.id,
        "plan_version": plan.get("version"),
        "scenario_id": scope.scenario_id,
        "source_ids": sorted(scope.source_ids),
        "report_start": start.isoformat(),
        "report_end": end.isoformat(),
        "group_by": group_by,
        "time_breakdown": time_breakdown,
        "generated": True,
        "delivered": 0,
        "summary_status": "complete" if summary else "omitted",
        "coverage_status": "partial" if coverage else "complete",
        "llm_cost": llm.get("cost") if isinstance(llm.get("cost"), (int, float)) else None,
        "analysis_count": data.get("stats", {}).get("analyses"),
        "text": text,
        "preview_text": text[:5000],
    }


async def execute_task_pipeline(payload: dict) -> dict:
    """Execute admitted v2 stages with durable no-blind-replay receipts."""
    from app.core.tenant_context import current_tenant_id
    from app.jobs import handlers

    task_id, job_id, attempt = payload.get("agent_task_id"), payload.get("job_id"), payload.get("attempts")
    if type(task_id) is not int or type(job_id) is not int or type(attempt) is not int:
        raise ValueError("pipeline_job_identity_invalid")
    snapshot = payload.get("_effective_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1 or snapshot.get("task_id") != task_id:
        raise ValueError("pipeline_admission_snapshot_missing")
    task = await handlers._load_task(task_id)
    if task is None or task.tenant_id != current_tenant_id():
        raise PermissionError("pipeline_task_unavailable")
    task_payload = snapshot.get("task_payload")
    if not isinstance(task_payload, dict):
        raise ValueError("pipeline_admission_snapshot_invalid")
    plan = task_payload.get("task_plan")
    pipeline = plan.get("pipeline") if isinstance(plan, dict) else None
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
    if not isinstance(plan, dict) or plan.get("version") != 2:
        raise ValueError("pipeline_definition_invalid")
    if stages not in (["analyze", "digest"], ["collect", "analyze", "digest"]):
        raise ValueError("pipeline_definition_invalid")
    task_view = _task_view(task, task_payload, snapshot)
    run_payload = dict(payload)
    run_payload.update(task_payload)
    for key in (
        "agent_task_id",
        "job_id",
        "attempts",
        "_trusted_initiated_by_user_id",
        "_effective_snapshot",
        "_prior_result",
    ):
        if key in payload:
            run_payload[key] = payload[key]
    run_payload["agent_task_id"] = task_id
    run_payload["job_id"] = job_id
    run_payload["source_ids"] = list(task_payload.get("source_ids") or [])
    run_payload["task_plan"] = plan
    checkpoint = _valid_checkpoint(
        (
            (payload.get("_prior_result") or {}).get("pipeline")
            if isinstance(payload.get("_prior_result"), dict)
            else None
        ),
        job_id=job_id,
        task_id=task_id,
    ) or _checkpoint_base(job_id=job_id, task_id=task_id, attempt=attempt)

    collection = checkpoint["stages"].get("collect", {}).get("result", {})
    if "collect" in stages:
        await _authorized_task(task, payload, stage="collect")
        initial_sources = await handlers._resolve_sources(task_view, run_payload)
        await _admit_entitlements(task_payload, initial_sources, job_id)

        async def collect():
            raw = await handlers.handle_collect(
                run_payload, stage_only=True, _resolved_context=(None, task_view, task_payload)
            )
            return _safe_collection_result(raw)

        checkpoint, collection = await _stage(checkpoint, "collect", collect, attempt=attempt)

    await _authorized_task(task, payload, stage="analyze")
    await _require_active_scenario(task.tenant_id, task_payload)
    sources = (
        await handlers._resolve_sources(
            task_view,
            run_payload,
            discovered_source_ids=[
                row["source_id"]
                for row in collection.get("per_source", [])
                if isinstance(row, dict) and type(row.get("source_id")) is int
            ],
        )
        if isinstance(collection, dict)
        else await handlers._resolve_sources(task_view, run_payload)
    )
    if not sources:
        raise PermissionError("pipeline_sources_unavailable")
    await _admit_entitlements(task_payload, sources, job_id)

    async def analyze():
        raw_collection = None
        if "collect" in stages:
            # Keep source resolution to the collection results admitted by this run.
            raw_collection = {
                "per_source": collection.get("per_source", []),
                "sources": len(collection.get("per_source", [])),
                "items": collection.get("counts", {}).get("items", 0),
                "new_items": collection.get("counts", {}).get("new_items", 0),
                "error": collection.get("counts", {}).get("error", 0),
                "rate_limited": collection.get("counts", {}).get("rate_limited", 0),
                "skipped": collection.get("counts", {}).get("skipped", 0),
            }
        raw = await handlers.handle_analyze(
            run_payload,
            _resolved_context=(None, task_view, task_payload),
            collection_stats_override=raw_collection,
        )
        return _safe_analysis_result(raw)

    checkpoint, analysis = await _stage(checkpoint, "analyze", analyze, attempt=attempt)
    await _authorized_task(task, payload, stage="digest")
    await _require_active_scenario(task.tenant_id, task_payload)
    await _admit_entitlements(task_payload, sources, job_id)

    digest_result = checkpoint["stages"].get("digest", {}).get("result", {})
    if "digest" in stages:
        checkpoint, digest_result = await _stage(
            checkpoint,
            "digest",
            lambda: _generate_digest(
                task_view,
                plan,
                task_payload,
                [source.id for source in sources],
                collection=collection,
                analysis=analysis,
            ),
            attempt=attempt,
        )

    partial = any(isinstance(result, dict) and result.get("status") == "partial" for result in (collection, analysis))
    if digest_result.get("status") != "complete":
        partial = True
    llm_cost = digest_result.get("llm_cost") if isinstance(digest_result, dict) else None
    analysis_counts = analysis.get("counts", {}) if isinstance(analysis, dict) else {}
    collection_counts = collection.get("counts", {}) if isinstance(collection, dict) else {}
    return {
        "status": "partial" if partial else "ok",
        "pipeline": checkpoint,
        "sources": analysis_counts.get("sources"),
        "items": collection_counts.get("items"),
        "collected": collection_counts.get("new_items"),
        "analyzed": analysis_counts.get("analyzed"),
        "skipped": analysis_counts.get("skipped"),
        "error": (analysis_counts.get("error") or 0) + (collection_counts.get("error") or 0),
        "per_source": analysis.get("per_source", []) if isinstance(analysis, dict) else [],
        "digest": digest_result,
        "llm_cost": llm_cost,
    }
