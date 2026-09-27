"""Dashboard `/app/` — the operational overview (docs/design/ui.md §4.2).

M2: real KPIs from SQL aggregation (no LLM calls). Sentiment is a placeholder
until the analyzer pipeline writes it to `ai_analytics`.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.database import new_session
from app.models.agent_task import AgentTask
from app.models.ai_analytics import AIAnalytics
from app.models.source import Source
from app.tasks.cron import cron_to_human

from .deps import render

router = APIRouter()


async def _kpis(tenant_id: int | None, is_superuser: bool = False) -> dict[str, int | float]:
    """Aggregate KPIs for the current tenant (set by TenantUIMiddleware);
    if is_superuser, tenant_id is None → show global aggregates."""
    today = date.today()

    async with new_session() as session:
        # Active sources count (tenant-scoped unless superuser)
        if is_superuser and tenant_id is None:
            sources_q = await session.execute(
                select(func.count(Source.id)).where(Source.is_active == True)  # noqa: E712
            )
        else:
            sources_q = await session.execute(
                select(func.count(Source.id)).where(
                    Source.tenant_id == tenant_id, Source.is_active == True  # noqa: E712
                )
            )
        active_sources = sources_q.scalar() or 0

        # Posts analyzed today
        posts_today_q = await session.execute(
            select(func.count(AIAnalytics.id)).where(AIAnalytics.analysis_date == today)
        )
        posts_today = posts_today_q.scalar() or 0

        # LLM cost today (cents → dollars)
        cost_today_q = await session.execute(
            select(func.coalesce(func.sum(AIAnalytics.estimated_cost), 0)).where(AIAnalytics.analysis_date == today)
        )
        cost_cents = cost_today_q.scalar() or 0
        # SUM() over NUMERIC comes back as Decimal — keep the KPI a plain float
        cost_usd = float(cost_cents) / 100

        # Sentiment: placeholder (no data yet)
        avg_sentiment = 0.0

        # Active tasks count (tenant-scoped unless superuser)
        if is_superuser and tenant_id is None:
            tasks_q = await session.execute(
                select(func.count(AgentTask.id)).where(AgentTask.is_active == True)  # noqa: E712
            )
        else:
            tasks_q = await session.execute(
                select(func.count(AgentTask.id)).where(
                    AgentTask.tenant_id == tenant_id, AgentTask.is_active == True  # noqa: E712
                )
            )
        active_tasks = tasks_q.scalar() or 0

    return {
        "active_sources": active_sources,
        "posts_today": posts_today,
        "avg_sentiment": avg_sentiment,
        "cost_today_usd": cost_usd,
        "active_tasks": active_tasks,
    }


async def _recent_tasks(tenant_id: int | None, is_superuser: bool = False, filter_tenant_id: int | None = None):
    """Fetch few most recent tasks.

    Superuser sees all tasks (optionally narrowed to one tenant via
    `filter_tenant_id`); regular users see their own tenant's tasks.
    """
    async with new_session() as session:
        if is_superuser:
            stmt = select(AgentTask).options(selectinload(AgentTask.tenant))
            if filter_tenant_id is not None:
                stmt = stmt.where(AgentTask.tenant_id == filter_tenant_id)
            q = await session.execute(stmt.order_by(AgentTask.created_at.desc()).limit(20))
        else:
            q = await session.execute(
                select(AgentTask).where(AgentTask.tenant_id == tenant_id).order_by(AgentTask.created_at.desc()).limit(5)
            )
        return q.scalars().all()


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
        raw_tenant = request.query_params.get("tenant_id")
        filter_tenant_id = int(raw_tenant) if raw_tenant and raw_tenant.isdigit() else None
        async with new_session() as session:
            from app.models import Tenant

            result = await session.execute(select(Tenant).order_by(Tenant.name))
            tenants = result.scalars().all()

    kpis = await _kpis(tenant_id, is_superuser)
    recent_tasks = await _recent_tasks(tenant_id, is_superuser, filter_tenant_id)
    return render(
        request,
        "web/dashboard.html",
        section="dashboard",
        kpis=kpis,
        recent_tasks=recent_tasks,
        cron_to_human=cron_to_human,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )
