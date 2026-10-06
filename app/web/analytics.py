"""Analytics `/app/analytics` — the workspace's full analytical view.

Read-only (docs/design/ui.md §4.4): sentiment trend, top topics, engagement,
content mix, per-provider LLM cost and per-day user activity. Everything is
aggregated from stored `ai_analytics` rows by `ReportAggregator` — no LLM calls,
so this page is cheap even over a long window.

The theme chains pages (`/app/analytics/chains`) add the one destructive
affordance the rest of `/app` has: deleting an analysis or a whole chain is
`aianalytics.delete` — the same structured role/permission rule every other
mutation uses (`guard_web` + `perms.can`), never open to a plain viewer.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.services.ai.reporting import ReportAggregator

from .deps import (
    action_tenant_id,
    add_flash,
    ensure_csrf,
    guard_web,
    perms_can,
    render,
    tenant_filter_context,
)

router = APIRouter(prefix="/analytics")

PERIODS = (("7", "7 дней"), ("30", "30 дней"), ("90", "3 месяца"), ("180", "полгода"), ("365", "год"), ("all", "всё"))

# Chain-list sort orders: `desc` is the default ("сначала новые" — the latest
# analysis in the chain decides the position); `asc` flips the timeline.
CHAIN_SORTS = (("desc", "Сначала новые"), ("asc", "Сначала старые"))


async def _analytics(tenant_id: int | None, is_superuser: bool, days: int | None, filter_tenant_id: int | None) -> dict:
    """Aggregate the analytics widgets for the ambient scope.

    A superuser without an active workspace sees global aggregates; a superuser
    with a tenant filter sees exactly that workspace. The manager guard already
    scopes a normal request, and `tenant_filter` narrows a superuser's view.
    """
    agg = ReportAggregator()

    # Superuser global view needs an explicit bypass (never "no tenant = all").
    if is_superuser and tenant_id is None:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            return await _aggregate(agg, days, filter_tenant_id)
    return await _aggregate(agg, days, filter_tenant_id)


async def _aggregate(agg: ReportAggregator, days: int | None, tenant_id: int | None) -> dict:
    # tenant_id narrows the methods that accept it (a superuser previewing one
    # workspace); the rest are scoped by the manager guard to the ambient scope.
    sentiment = await agg.get_sentiment_trends(days=days)
    topics = await agg.get_top_topics(days=days, limit=10, tenant_id=tenant_id)
    content_mix = await agg.get_content_mix(days=days, tenant_id=tenant_id)
    engagement = await agg.get_engagement_metrics(days=days)
    llm = await agg.get_llm_provider_stats(days=days)
    activity = await agg.get_activity_trend(days=days, tenant_id=tenant_id)

    # Chains: group ai_analytics rows by topic_chain_id (Phase 4 display)
    from datetime import date, timedelta
    from app.models import AIAnalytics
    from app.services.ai.chain_resolver import human_chain_label

    chain_query = AIAnalytics.objects.filter(AIAnalytics.topic_chain_id.isnot(None))
    if days is not None:
        cutoff = date.today() - timedelta(days=days)
        chain_query = chain_query.filter(AIAnalytics.analysis_date >= cutoff)
    chain_rows = await chain_query
    chains_map: dict[str, dict[str, Any]] = {}
    for row in chain_rows:
        cid = row.topic_chain_id
        if not cid:
            continue
        if cid not in chains_map:
            chains_map[cid] = {
                "chain_id": cid,
                "chain_label": row.chain_label or human_chain_label(row.summary_data) or cid,
                "entry_count": 0,
                "first_date": None,
                "last_date": None,
                "scores": [],
            }
        entry = chains_map[cid]
        entry["entry_count"] += 1
        if not entry["first_date"] or (row.analysis_date and row.analysis_date < entry["first_date"]):
            entry["first_date"] = row.analysis_date
        if not entry["last_date"] or (row.analysis_date and row.analysis_date > entry["last_date"]):
            entry["last_date"] = row.analysis_date
        sent = agg._extract_sentiment(row.summary_data)
        if sent and sent.get("score") is not None:
            entry["scores"].append(sent["score"])

    chains_result = []
    for cid, entry in chains_map.items():
        scores = entry.pop("scores")
        chains_result.append({
            "chain_id": entry["chain_id"],
            "chain_label": entry["chain_label"],
            "entry_count": entry["entry_count"],
            "date_range": {
                "start": entry["first_date"].isoformat() if entry["first_date"] else None,
                "end": entry["last_date"].isoformat() if entry["last_date"] else None,
            },
            "avg_sentiment": round(sum(scores) / len(scores), 3) if scores else None,
        })
    chains_result.sort(key=lambda c: -c["entry_count"])

    # Roll the sentiment distribution up for a headline widget.
    dist = {"positive": 0, "neutral": 0, "negative": 0}
    total_analyses = 0
    for point in sentiment:
        d = point.get("distribution") or {}
        for key in dist:
            dist[key] += d.get(key, 0) or 0
        total_analyses += point.get("total_analyses", 0) or 0

    # Headline KPIs from the raw activity + engagement data.
    total_posts = sum(a.get("total_posts", 0) for a in activity)
    peak_users = max((a.get("active_users", 0) for a in activity), default=0)

    return {
        "sentiment": sentiment,
        "sentiment_dist": dist,
        "topics": topics,
        "content_mix": content_mix,
        "engagement": engagement,
        "llm": llm,
        "activity": activity,
        "chains": chains_result,
        "kpis": {
            "total_analyses": total_analyses,
            "total_posts": total_posts,
            "peak_users": peak_users,
            "cost_usd": float((llm.get("summary") or {}).get("total_cost_usd") or 0),
        },
    }


# Default analytics period: a month. Chosen via the `days` filter, remembered in
# the session so it survives navigating away and back.
DEFAULT_ANALYTICS_DAYS = "30"
SESSION_DAYS_KEY = "analytics_days"


def _resolve_days(request: Request) -> tuple[int | None, str]:
    """The look-back window for this request: query param → session → default.

    Returns `(days, days_key)`, where `days` is `None` for "всё" and `days_key`
    is the string the template uses to highlight the selected `<option>`. A
    valid `?days=` wins and is remembered; otherwise the remembered choice is
    used; otherwise the default month.
    """
    raw = request.query_params.get("days")
    if raw is None:
        raw = request.session.get(SESSION_DAYS_KEY, DEFAULT_ANALYTICS_DAYS)

    if raw == "all":
        days: int | None = None
        days_key = "all"
    elif raw.isdigit() and 1 <= int(raw) <= 365:
        days = int(raw)
        days_key = str(days)
    else:
        days = None if DEFAULT_ANALYTICS_DAYS == "all" else int(DEFAULT_ANALYTICS_DAYS)
        days_key = DEFAULT_ANALYTICS_DAYS

    request.session[SESSION_DAYS_KEY] = days_key
    return days, days_key


@router.get("")
@router.get("/")
async def analytics_page(request: Request):
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)

    days, days_key = _resolve_days(request)

    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser) if is_superuser else (None, [])

    data = await _analytics(tenant_id, is_superuser, days, filter_tenant_id)
    return render(
        request,
        "web/analytics.html",
        section="analytics",
        analytics=data,
        days=days_key,
        periods=PERIODS,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )


@router.get("/chains")
async def analytics_chains(request: Request):
    """All theme chains with a chronological retrospective per chain.

    A chain groups the analyses of one ongoing theme/source/user over time.
    This page lists every chain the workspace has and, for each, a timeline of
    its analyses — the "удобный просмотр ретроспективы" the user asked for.
    The list sorts by the chain's latest analysis date: `?sort=desc` (default,
    newest first) or `?sort=asc` (oldest first).
    """
    from app.models import AIAnalytics
    from app.services.ai.topic_chain_service import TopicChainService

    sort = request.query_params.get("sort", "desc")
    if sort not in ("desc", "asc"):
        sort = "desc"

    rows = await AIAnalytics.objects.filter(AIAnalytics.topic_chain_id.isnot(None)).order_by(AIAnalytics.analysis_date)
    chain_data = TopicChainService().build_topic_chain(rows)

    # Order chains by their latest analysis (most recent first by default).
    chains = sorted(
        chain_data.values(),
        key=lambda ch: ch.get("date_range", {}).get("end") or "",
        reverse=(sort == "desc"),
    )
    return render(
        request,
        "web/analytics_chains.html",
        section="analytics",
        chains=chains,
        total_chains=len(chains),
        sort=sort,
        sorts=CHAIN_SORTS,
        perms_can=perms_can,
    )


@router.get("/chains/{chain_id}")
async def analytics_chain_detail(request: Request, chain_id: str):
    """One chain's full retrospective: a chronological timeline of analyses."""
    from app.models import AIAnalytics
    from app.services.ai.topic_chain_service import TopicChainService

    rows = await AIAnalytics.objects.filter(topic_chain_id=chain_id).order_by(AIAnalytics.analysis_date)
    if not rows:
        return render(request, "web/not_found.html", status_code=404)

    chain_data = TopicChainService().build_topic_chain(rows).get(chain_id)
    if chain_data is None:
        chain_data = {"chain_id": chain_id, "evolution": [], "total_analyses": 0, "date_range": {}}

    return render(
        request,
        "web/analytics_chain_detail.html",
        section="analytics",
        chain=chain_data,
        perms_can=perms_can,
    )


@router.post("/chains/{chain_id}/delete")
async def analytics_chain_delete(
    request: Request,
    chain_id: str,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Delete a whole theme chain: every analysis sharing `topic_chain_id`.

    Gated on `aianalytics.delete` like the single-analysis delete — a viewer
    sees the list but never the button, and a crafted POST is refused by
    `guard_web` regardless.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/analytics/chains", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "aianalytics", "delete", back="/app/analytics/chains")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics

    with tenant_scope(tenant_id):
        count = await AIAnalytics.objects.delete(topic_chain_id=chain_id)

    if count:
        add_flash(request, "success", f"Цепочка удалена: {count} анализ(ов)")
    else:
        add_flash(request, "error", "Цепочка не найдена")
    return RedirectResponse("/app/analytics/chains", status_code=302)


# Registered last so the static `/chains` routes win over the dynamic
# `{analysis_id}` (a request to `/analytics/chains` must not be captured as an
# analysis id — FastAPI would answer 422 instead of the chains page).
@router.post("/{analysis_id}/delete")
async def analytics_delete(
    request: Request,
    analysis_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Delete a single analysis row, then return to its chain (or the list).

    The chain detail's timeline offers one delete per record; the row is looked
    up inside the action tenant so a superuser acting on another workspace
    cannot delete what it does not see.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/analytics/chains", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "aianalytics", "delete", back="/app/analytics/chains")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics

    with tenant_scope(tenant_id):
        row = await AIAnalytics.objects.get(id=analysis_id)
        if row is None:
            add_flash(request, "error", "Анализ не найден")
            return RedirectResponse("/app/analytics/chains", status_code=302)
        chain_id = row.topic_chain_id
        await AIAnalytics.objects.delete(id=analysis_id)
        # The chain detail page 404s once its last analysis is gone, so "back"
        # must not point there: fall back to the list when the chain emptied.
        chain_emptied = bool(chain_id) and not await AIAnalytics.objects.filter(topic_chain_id=chain_id)

    add_flash(
        request,
        "success",
        f"Анализ #{analysis_id} удалён" + (" — цепочка опустела и удалена из списка" if chain_emptied else ""),
    )
    back = f"/app/analytics/chains/{chain_id}" if chain_id and not chain_emptied else "/app/analytics/chains"
    return RedirectResponse(back, status_code=302)


# Registered last so the static `/chains` routes win over the dynamic
# `{analysis_id}` (a request to `/analytics/chains` must not be captured as an
# analysis id — FastAPI would answer 422 instead of the chains page).
@router.get("/{analysis_id}")
async def analytics_detail(request: Request, analysis_id: int):
    """One saved analysis, rendered from its stored summary_data.

    This is the link the dashboard "Последние анализы" cards and the source
    page rows point at, so a user can open a single result and read the AI
    summary, topics, mood and statistics instead of hunting through the
    aggregate page. Rendering reuses `analysis_render.render_analysis`, the
    same helper the sqladmin detail template uses.
    """
    from app.models import AIAnalytics
    from app.services.ai.analysis_render import render_analysis

    row = await AIAnalytics.objects.select_related("source").get(id=analysis_id)
    if row is None:
        return render(request, "web/not_found.html", status_code=404)

    display = render_analysis(row.summary_data or {})

    # All analytics sharing this row's chain, for the "next/previous in chain"
    # navigation and the retrospective timeline.
    chain = []
    if row.topic_chain_id:
        chain = await AIAnalytics.objects.filter(topic_chain_id=row.topic_chain_id).order_by(AIAnalytics.analysis_date)
        chain = [
            {
                "id": c.id,
                "analysis_date": c.analysis_date,
                "title": (c.summary_data or {}).get("analysis_title") or f"Анализ #{c.id}",
            }
            for c in chain
        ]

    return render(
        request,
        "web/analytics_detail.html",
        section="analytics",
        analysis=row,
        display=display,
        chain=chain,
    )
