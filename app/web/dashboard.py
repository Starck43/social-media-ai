"""Dashboard `/app/` — the operational overview (docs/design/ui.md §4.2).

M2: real KPIs from SQL aggregation (no LLM calls). Sentiment is a placeholder
until the analyzer pipeline writes it to `ai_analytics`.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.core.database import new_session
from app.models.ai_analytics import AIAnalytics
from app.models.source import Source

from .deps import render

router = APIRouter()


async def _kpis() -> dict[str, int | float]:
    """Aggregate KPIs for the current tenant (set by TenantUIMiddleware)."""
    today = date.today()

    async with new_session() as session:
        # Active sources count
        sources_q = await session.execute(
            select(func.count(Source.id)).where(Source.is_active == True)  # noqa: E712
        )
        active_sources = sources_q.scalar() or 0

        # Posts analyzed today
        posts_today_q = await session.execute(
            select(func.count(AIAnalytics.id)).where(AIAnalytics.analysis_date == today)
        )
        posts_today = posts_today_q.scalar() or 0

        # LLM cost today (cents → dollars)
        cost_today_q = await session.execute(
            select(func.coalesce(func.sum(AIAnalytics.estimated_cost), 0)).where(
                AIAnalytics.analysis_date == today
            )
        )
        cost_cents = cost_today_q.scalar() or 0
        cost_usd = cost_cents / 100.0

        # Sentiment: placeholder (no data yet)
        avg_sentiment = 0.0

    return {
        "active_sources": active_sources,
        "posts_today": posts_today,
        "avg_sentiment": avg_sentiment,
        "cost_today_usd": cost_usd,
    }


# A path of "" is rejected by FastAPI when the router is included, so the index
# is registered as "/" only. Requests to "/app" (no slash) are redirected here
# by Starlette's redirect_slashes.
@router.get("/")
async def dashboard(request: Request):
    kpis = await _kpis()
    return render(request, "web/dashboard.html", section="dashboard", kpis=kpis)
