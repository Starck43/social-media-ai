"""Read-only workspace overview: permitted results and upcoming schedules.

The pipeline strip shows data state only (staged items waiting, stored
analyses, job activity) — never KPI/aggregations or LLM calls on the home
request.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.models.analysis.ai_analytics import AIAnalytics
from app.models.collection.source import Source
from app.models.scheduling.agent_task import AgentTask

from .deps import render, tenant_filter_context

router = APIRouter()


async def _recent_results(tenant_id: int | None, is_superuser: bool, show_sources: bool) -> list[dict]:
    """Existing analysis results only; callers must hold aianalytics.view."""
    if is_superuser:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            return await _recent_results_in_scope(tenant_id, show_sources)
    return await _recent_results_in_scope(tenant_id, show_sources)


async def _recent_results_in_scope(tenant_id: int | None, show_sources: bool) -> list[dict]:
    from app.services.ai.analysis_render import render_analysis

    query = AIAnalytics.objects.filter()
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    if show_sources:
        query = query.select_related("source")
    rows = await query.order_by(AIAnalytics.created_at.desc(), AIAnalytics.id.desc()).limit(5)
    recent = []
    for analysis in rows:
        source_name = analysis.source.name if show_sources and analysis.source else None
        display = render_analysis(analysis.summary_data or {}, source_name=source_name)
        recent.append(
            {
                "id": analysis.id,
                "kind": "analysis",
                "href": f"/app/analytics/{analysis.id}",
                "source_name": source_name,
                "created_at": analysis.created_at,
                "title": display["display_title"],
                "summary": (display["analysis_summary"] or "")[:220],
                "analysis_incomplete": display["coverage"]["incomplete"],
                "period_label": None,
                "delivery_status": None,
            }
        )
    return recent


def _digest_delivery_status(run) -> str:
    """Return a safe summary of delivery evidence without exposing destinations."""
    error = getattr(run, "error", None) or ""
    state = getattr(run, "delivery_state", None)
    if isinstance(state, dict):
        try:
            from app.services.digest.checkpoints import validate_checkpoint
            from app.services.digest.delivery_outcomes import summarize_delivery

            checkpoint = validate_checkpoint(state)
            if checkpoint["run_id"] != getattr(run, "id", None) or checkpoint["tenant_id"] != getattr(
                run, "tenant_id", None
            ):
                return "unknown"
            if "partial_delivery_requires_review" in error:
                return "partial"
            outcome = summarize_delivery(checkpoint)
            if outcome["pending_parts"] == outcome["total_parts"] and getattr(run, "status", None) == "pending":
                return "pending"
            return outcome["status"]
        except (KeyError, TypeError, ValueError):
            return "unknown"

    if "partial_delivery_requires_review" in error:
        return "partial"
    status = getattr(run, "status", None)
    return status if status in {"sent", "failed", "skipped", "pending"} else "unknown"


async def _recent_digests_in_scope(tenant_id: int | None) -> list[dict]:
    from app.models.notifications.digest_run import DigestRun

    query = DigestRun.objects.filter()
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    rows = await query.order_by(DigestRun.created_at.desc(), DigestRun.id.desc()).limit(5)
    labels = {
        "sent": "Доставлен",
        "partial": "Доставлен частично",
        "uncertain": "Доставка не подтверждена",
        "blocked": "Доставка заблокирована",
        "failed": "Ошибка доставки",
        "skipped": "Пропущен",
        "pending": "Ожидает доставки",
        "unknown": "Статус доставки неизвестен",
    }
    return [
        {
            "id": run.id,
            "kind": "digest",
            "href": "/app/digests",
            "source_name": None,
            "created_at": run.created_at,
            "title": f"Дайджест за период {run.period_start:%d.%m.%Y} — {run.period_end:%d.%m.%Y}",
            "summary": (run.content or "")[:220],
            "analysis_incomplete": False,
            "period_label": f"Период {run.period_start:%d.%m.%Y} — {run.period_end:%d.%m.%Y}",
            "delivery_status": labels[_digest_delivery_status(run)],
        }
        for run in rows
    ]


async def _recent_digests(tenant_id: int | None, is_superuser: bool = False) -> list[dict]:
    if is_superuser:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            return await _recent_digests_in_scope(tenant_id)
    return await _recent_digests_in_scope(tenant_id)


def _merge_result_feed(analyses: list[dict], digests: list[dict]) -> list[dict]:
    """Merge independently authorized, bounded result sets into one stable feed."""
    return sorted(
        (*analyses, *digests),
        key=lambda row: (row["created_at"], row["kind"], row["id"]),
        reverse=True,
    )[:5]


async def _recent_tasks(
    tenant_id: int | None,
    is_superuser: bool = False,
    filter_tenant_id: int | None = None,
    *,
    show_sources: bool = True,
):
    """Fetch the next three schedules that remain effectively runnable.

    Source based schedules are excluded before querying source state when the
    caller lacks source.view. The manager applies all other effective activity
    rules in SQL before its limit, so paused or stale rows cannot crowd out the
    actual next runs.
    """
    if tenant_id is None:
        return []

    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    now = datetime.now(timezone.utc)
    with tenant_scope(tenant_id):
        tasks = await AgentTaskManager().upcoming_effective(now=now, include_source_based=show_sources, limit=3)
    return [
        {
            "id": task.id,
            "name": task.name,
            "next_run_at": task.next_run_at,
            "is_due": task.next_run_at <= now,
            "last_status": task.last_status,
        }
        for task in tasks
    ]


async def _effective_active_ids(recent_tasks) -> set[int]:
    """Task ids among `recent_tasks` that are effectively active.

    Groups the (possibly multi-tenant, superuser) batch by workspace so the
    "all active sources" fallback is resolved against the right tenant.
    """
    if not recent_tasks:
        return set()
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    mgr = AgentTaskManager()
    by_tenant: dict[int, list] = {}
    for t in recent_tasks:
        by_tenant.setdefault(t.tenant_id, []).append(t)

    effective: set[int] = set()
    for tid, tasks in by_tenant.items():
        with tenant_scope(tid):
            active = await mgr.get_workspace_active_source_ids()
        for t in tasks:
            if mgr._task_effectively_active(t, active):
                effective.add(t.id)
    return effective


# A path of "" is rejected by FastAPI when the router is included, so the index
# is registered as "/" only. Requests to "/app" (no slash) are redirected here
# by Starlette's redirect_slashes.
async def _pipeline(tenant_id: int | None, is_superuser: bool, offer: dict) -> dict | None:
    """Read each data component only with its own workspace view permission.

    Task/source permissions never imply collected-item, analysis or job access.
    Without a selected workspace there are no data reads, including global mode.
    """
    if not any(offer.get(key) for key in ("collected_items", "analytics", "jobs")):
        return None
    if tenant_id is None:
        return {"scope_all": True} if is_superuser else None

    pipeline = {"scope_all": False}
    if offer.get("collected_items"):
        from app.models.collection.collected_item import CollectedItem

        pipeline["staged"] = await CollectedItem.objects.filter(tenant_id=tenant_id).count()
    if offer.get("analytics"):
        pipeline["analyzed"] = await AIAnalytics.objects.filter(tenant_id=tenant_id).count()
    if offer.get("jobs"):
        from app.models.scheduling.job import Job

        # This is a bounded recent-active preview, never a queue total.
        pipeline["preview_limit"] = 3
        activity_labels = {
            "collect": "Сбор данных выполняется…",
            "analyze": "Анализ выполняется…",
            "digest": "Подготовка дайджеста выполняется…",
            "prune": "Очистка данных выполняется…",
            "learn": "Обучение выполняется…",
            "reflect": "Рефлексия выполняется…",
        }
        active_jobs = (
            await Job.objects.filter(tenant_id=tenant_id, status__in=["pending", "running"])
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(pipeline["preview_limit"])
        )
        pipeline["running"] = [
            {"job_type": j.job_type, "label": activity_labels.get(j.job_type, "Задание выполняется…")}
            for j in active_jobs
            if getattr(j, "status", None) == "running"
        ]
        pipeline["queued_count"] = sum(1 for j in active_jobs if getattr(j, "status", None) == "pending")
        pipeline["job_count"] = len(active_jobs)
    return pipeline


def _offers(perms) -> dict[str, bool]:
    """What the dashboard may point at: a link only where the caller's level opens the page.

    Each entry is "may open the target page" (its section's view right) plus,
    for a call to create something, the right to create it — the section gate
    in `TenantUIMiddleware` would refuse the page anyway, the dashboard just
    does not lead there.
    """

    def can(model_name: str, *actions: str) -> bool:
        return perms is not None and all(perms.can(model_name, action) for action in actions)

    return {
        "sources": can("source", "view"),
        "add_source": can("source", "view", "create"),
        "add_scenario": can("agentscenario", "view", "create"),
        "tasks": can("agenttask", "view"),
        "add_task": can("agenttask", "view", "create"),
        "digests": can("digestrun", "view"),
        "analytics": can("aianalytics", "view"),
        "collected_items": can("collecteditem", "view"),
        "jobs": can("job", "view"),
    }


@router.get("/")
async def dashboard(request: Request):
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = False

    filter_tenant_id = None
    tenants = []
    if is_superuser:
        filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    offer = _offers(getattr(request.state, "web_perms", None))
    analyses = (
        await _recent_results(filter_tenant_id if is_superuser else tenant_id, is_superuser, offer["sources"])
        if offer["analytics"]
        else []
    )
    digests = (
        await _recent_digests(filter_tenant_id if is_superuser else tenant_id, is_superuser) if offer["digests"] else []
    )
    recent_results = _merge_result_feed(analyses, digests)
    recent_tasks = (
        await _recent_tasks(tenant_id, is_superuser, filter_tenant_id, show_sources=offer["sources"])
        if offer["tasks"]
        else []
    )

    pipeline = await _pipeline(filter_tenant_id if is_superuser else tenant_id, is_superuser, offer)

    # A single first-value step, never a permanent command panel. Only query a
    # model when the caller can both view and create it in the active workspace.
    next_step = None
    if not is_superuser and tenant_id is not None:
        if offer["add_source"] and await Source.objects.filter(tenant_id=tenant_id).count() == 0:
            next_step = {"label": "Подключите первый источник", "href": "/app/sources"}
        elif offer["add_task"] and await AgentTask.objects.filter(tenant_id=tenant_id).count() == 0:
            next_step = {"label": "Настройте первую задачу", "href": "/app/tasks"}

    return render(
        request,
        "web/dashboard.html",
        section="dashboard",
        recent_results=recent_results,
        recent_tasks=recent_tasks,
        source_schedule_status_unknown=bool(offer["tasks"] and not offer["sources"]),
        pipeline=pipeline,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
        offer=offer,
        next_step=next_step,
        can_open_chat=bool(getattr(getattr(request.state, "web_perms", None), "can_open_chat", False)),
    )
