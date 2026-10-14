"""Read-only workspace overview: permitted results and compact task summaries.

No KPI/aggregation work or LLM calls on the home request. Future attention and
mixed-result projections are specified in the canonical development board.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.agent_task import AgentTask
from app.models.ai_analytics import AIAnalytics
from app.models.source import Source

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
    rows = await query.order_by(AIAnalytics.created_at.desc()).limit(5)
    recent = []
    for analysis in rows:
        source_name = analysis.source.name if show_sources and analysis.source else None
        display = render_analysis(analysis.summary_data or {}, source_name=source_name)
        recent.append(
            {
                "id": analysis.id,
                "source_name": source_name,
                "created_at": analysis.created_at,
                "title": display["display_title"],
                "summary": (display["analysis_summary"] or "")[:220],
                "analysis_incomplete": display["coverage"]["incomplete"],
            }
        )
    return recent


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
            return await query.order_by(AgentTask.created_at.desc()).limit(3)

    return await (
        AgentTask.objects.filter(tenant_id=tenant_id)
        .prefetch_related("sources", "agent_scenario")
        .order_by(AgentTask.created_at.desc())
        .limit(3)
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
    }


@router.get("/")
async def dashboard(request: Request):
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)

    filter_tenant_id = None
    tenants = []
    if is_superuser:
        filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    offer = _offers(getattr(request.state, "web_perms", None))
    recent_results = (
        await _recent_results(filter_tenant_id if is_superuser else tenant_id, is_superuser, offer["sources"])
        if offer["analytics"]
        else []
    )
    recent_tasks = (
        await _recent_tasks(tenant_id, is_superuser, filter_tenant_id) if offer["tasks"] else []
    )[:3]
    effective_active = await _effective_active_ids(recent_tasks)

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
        effective_active=effective_active,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
        offer=offer,
        next_step=next_step,
        can_open_chat=bool(getattr(getattr(request.state, "web_perms", None), "can_open_chat", False)),
    )
