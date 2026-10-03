"""Dashboard `/app/` — the operational overview (docs/design/ui.md §4.2).

M2: real KPIs from SQL aggregation (no LLM calls). Sentiment is a placeholder
until the analyzer pipeline writes it to `ai_analytics`.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func

from app.models.agent_task import AgentTask
from app.models.ai_analytics import AIAnalytics
from app.models.source import Source
from app.tasks.cron import cron_to_human

from .deps import render, tenant_filter_context

router = APIRouter()


async def _kpis(tenant_id: int | None, is_superuser: bool = False) -> dict[str, int | float]:
    """Aggregate KPIs for the current tenant (set by TenantUIMiddleware);
    if is_superuser, tenant_id is None → show global aggregates.

    The managers carry the tenant guard, so a normal request needs no manual
    `tenant_id` predicate: it is applied unless the caller holds a bypass.
    Aggregates run through `values()` + `scalar()` rather than a raw session.

    A superuser without an active workspace sees global aggregates, so their
    branch runs under an explicit bypass — never "no tenant means everything".
    """
    today = date.today()

    if is_superuser and tenant_id is None:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            return await _kpis_in_scope(today)

    return await _kpis_in_scope(today)


async def _kpis_in_scope(today: date) -> dict[str, int | float]:
    """KPI aggregates for the ambient scope (tenant context or superuser bypass)."""
    active_sources = await Source.objects.filter(is_active=True).values(func.count(Source.id)).scalar(0)

    posts_today = await AIAnalytics.objects.filter(analysis_date=today).values(func.count(AIAnalytics.id)).scalar(0)

    # LLM cost today (cents → dollars)
    cost_cents = await (
        AIAnalytics.objects.filter(analysis_date=today)
        .values(func.coalesce(func.sum(AIAnalytics.estimated_cost), 0))
        .scalar(0)
    )
    # SUM() over NUMERIC comes back as Decimal — keep the KPI a plain float
    cost_usd = float(cost_cents) / 100

    # Sentiment: average of recent sentiment scores across the tenant's analyses
    avg_sentiment = 0.0
    sentiment_analyzed = False
    recent = await (
        AIAnalytics.objects.filter(analysis_date__gte=today - timedelta(days=7))
        .select_related("source")
        .order_by(AIAnalytics.created_at.desc())
    )
    scores: list[float] = []
    for a in recent:
        sent = _extract_sentiment_score(a.summary_data)
        if sent is not None:
            scores.append(sent)
    if scores:
        avg_sentiment = sum(scores) / len(scores)
        sentiment_analyzed = True

    active_tasks = 0
    due_tasks = await AgentTask.objects.filter(is_active=True).prefetch_related("sources", "agent_scenario")
    if due_tasks:
        from app.models.managers.agent_task_manager import AgentTaskManager

        active_source_ids = await AgentTaskManager().get_workspace_active_source_ids()
        active_tasks = sum(1 for t in due_tasks if AgentTaskManager._task_effectively_active(t, active_source_ids))

    return {
        "active_sources": active_sources,
        "posts_today": posts_today,
        "avg_sentiment": avg_sentiment,
        "sentiment_analyzed": sentiment_analyzed,
        "cost_today_usd": cost_usd,
        "active_tasks": active_tasks,
    }


def _extract_sentiment_score(summary_data: dict | None) -> float | None:
    """Pull a 0.0–1.0 sentiment score from summary_data (v3.0 multi-llm first)."""
    if not summary_data:
        return None
    multi_llm = summary_data.get("multi_llm_analysis", {})
    text_analysis = multi_llm.get("text_analysis", {}) or {}
    score = text_analysis.get("sentiment_score")
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score)))
    ai = summary_data.get("ai_analysis", {})
    sentiment = ai.get("sentiment_analysis", {}) or {}
    overall = sentiment.get("overall_sentiment", {})
    if isinstance(overall, dict) and isinstance(overall.get("score"), (int, float)):
        return max(0.0, min(1.0, float(overall["score"])))
    return None


async def _analytics(tenant_id: int | None, is_superuser: bool = False) -> dict:
    """Aggregated analytics widgets for the dashboard (ReportAggregator, no LLM)."""
    if is_superuser and tenant_id is None:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            return await _analytics_in_scope(None)

    return await _analytics_in_scope(tenant_id)


async def _analytics_in_scope(tenant_filter: int | None) -> dict:
    """Report widgets for the ambient scope; `tenant_filter` narrows explicitly."""
    from app.services.ai.reporting import ReportAggregator

    agg = ReportAggregator()
    top_topics = await agg.get_top_topics(days=7, limit=6, tenant_id=tenant_filter)
    content_mix = await agg.get_content_mix(days=7, tenant_id=tenant_filter)

    recent = []
    rows = await AIAnalytics.objects.select_related("source").order_by(AIAnalytics.created_at.desc()).limit(8)
    for a in rows:
        sd = a.summary_data or {}
        recent.append(
            {
                "id": a.id,
                "source_name": a.source.name if a.source else f"#{a.source_id}",
                "analysis_date": a.analysis_date,
                "title": sd.get("analysis_title") or f"Анализ #{a.id}",
                "summary": (sd.get("analysis_summary") or "")[:220],
                "topics": (sd.get("main_topics") or [])[:4],
                "cost_usd": float(a.estimated_cost or 0) / 100,
            }
        )

    return {
        "top_topics": top_topics,
        "content_mix": content_mix,
        "recent": recent,
    }


async def _recent_tasks(tenant_id: int | None, is_superuser: bool = False, filter_tenant_id: int | None = None):
    """Fetch few most recent tasks.

    Superuser sees all tasks (optionally narrowed to one tenant via
    `filter_tenant_id`); regular users see their own tenant's tasks.
    """
    if is_superuser:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            query = AgentTask.objects.prefetch_related("tenant", "sources", "agent_scenario")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            return await query.order_by(AgentTask.created_at.desc()).limit(20)

    return await (
        AgentTask.objects.filter(tenant_id=tenant_id)
        .prefetch_related("sources", "agent_scenario")
        .order_by(AgentTask.created_at.desc())
        .limit(5)
    )


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
@router.get("/")
async def dashboard(request: Request):
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)

    filter_tenant_id = None
    tenants = []
    if is_superuser:
        filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    kpis = await _kpis(tenant_id, is_superuser)
    recent_tasks = await _recent_tasks(tenant_id, is_superuser, filter_tenant_id)
    effective_active = await _effective_active_ids(recent_tasks)
    analytics = await _analytics(tenant_id, is_superuser)
    return render(
        request,
        "web/dashboard.html",
        section="dashboard",
        kpis=kpis,
        recent_tasks=recent_tasks,
        effective_active=effective_active,
        analytics=analytics,
        cron_to_human=cron_to_human,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )
