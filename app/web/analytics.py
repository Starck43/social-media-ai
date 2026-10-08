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

import re
from collections import Counter
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.services.ai.grouping import _extract_entities
from app.services.ai.reporting import MEDIA_FILTERS, REMOVED_GROUPING_AXES, SENTIMENT_FILTERS, ReportAggregator

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
CHAIN_SORTS = (
    ("desc", "Сначала новые"),
    ("asc", "Сначала старые"),
    ("sentiment_asc", "Сначала негативные — репутационный риск"),
)


NAV_FILTERS = (
    "days",
    "tenant_id",
    "source_id",
    "entity_type",
    "sentiment",
    "media",
    "group_by_period",
    "sort",
    "entity_name",
    "content_type",
    "intent",
    "chain_id",
)
DRILL_LABELS = {
    "themes": "По темам",
    "sources": "По источникам",
    "entities": "По сущностям",
    "sentiment": "По тональности",
    "content_type": "По типу контента",
    "intent": "По намерению",
    "days": "Хронология",
}


def _safe_analytics_return(value: str | None) -> str | None:
    """Only local read-only analytics entry points may be used as return URLs."""
    if not value or len(value) > 4096 or any(ord(char) < 32 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    path = unquote(parsed.path)
    if "\\" in path or any(ord(char) < 32 for char in path):
        return None
    static = {"/app", "/app/analytics", "/app/analytics/", "/app/analytics/group", "/app/analytics/chains"}
    if path not in static and not re.fullmatch(
        r"/app/(?:analytics/[0-9]+|sources/[0-9]+|analytics/chains/[\w-]+)", path
    ):
        return None
    return value


def _analysis_back_label(url: str, fallback: str = "К списку анализов") -> str:
    path = urlsplit(url).path
    if re.fullmatch(r"/app/analytics/[0-9]+", path):
        return "К анализу"
    if path.startswith("/app/analytics/chains/"):
        return "К цепочке"
    if re.fullmatch(r"/app/sources/[0-9]+", path):
        return "К источнику"
    if path == "/app":
        return "На главную"
    return fallback


def _analytics_origin(request: Request, days_key: str) -> str:
    """Keep the whole origin query, including axis/value and its parent return."""
    params = dict(request.query_params)
    params["days"] = days_key
    # Never forward untrusted origins through another level of navigation.
    if "return_to" in params and not _safe_analytics_return(params["return_to"]):
        params.pop("return_to")
    return request.url.path + "?" + urlencode(params)


def _analytics_url(request: Request, path: str, *, days_key: str | None = None, **extra) -> str:
    """Encode navigation once; never interpolate untrusted query strings in HTML."""
    params = {key: request.query_params[key] for key in NAV_FILTERS if key in request.query_params}
    if days_key is not None:
        params["days"] = days_key
    params.update({key: value for key, value in extra.items() if value is not None})
    return path + (("?" + urlencode(params)) if params else "")


async def _scoped_analytics_rows(
    request: Request, days: int | None, filter_tenant_id: int | None, *, chain_id: str | None = None
):
    """One queryset/window/source scope for groups, drill-down and chain detail."""
    from datetime import date, timedelta

    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics

    qs = AIAnalytics.objects.all()
    if days is not None:
        qs = qs.filter(analysis_date__gte=date.today() - timedelta(days=days))
    effective_tenant_id = (
        filter_tenant_id if filter_tenant_id is not None else getattr(request.state, "tenant_id", None)
    )
    if effective_tenant_id is not None:
        qs = qs.filter(tenant_id=effective_tenant_id)
    source_id = request.query_params.get("source_id")
    if source_id:
        try:
            source_id = int(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="source_id must be an integer") from exc
        qs = qs.filter(source_id=source_id)
    if chain_id is not None:
        qs = qs.filter(topic_chain_id=chain_id)
    user = getattr(request.state, "web_user", None)
    if user and user.is_superuser and getattr(request.state, "tenant_id", None) is None:
        with tenant_scope(bypass=True):
            return list(await qs)
    return list(await qs)


async def _analytics(
    tenant_id: int | None,
    is_superuser: bool,
    days: int | None,
    filter_tenant_id: int | None,
    source_id: int | None = None,
) -> dict:
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
            return await _aggregate(
                agg, days, filter_tenant_id if filter_tenant_id is not None else tenant_id, source_id
            )
    return await _aggregate(agg, days, filter_tenant_id if filter_tenant_id is not None else tenant_id, source_id)


async def _aggregate(
    agg: ReportAggregator, days: int | None, tenant_id: int | None, source_id: int | None = None
) -> dict:
    # tenant_id narrows the methods that accept it (a superuser previewing one
    # workspace); the rest are scoped by the manager guard to the ambient scope.
    sentiment = await agg.get_sentiment_trends(days=days)
    topics = await agg.get_top_topics(days=days, limit=10, tenant_id=tenant_id)
    entities = await agg.get_entity_mentions(days=days, limit=20, tenant_id=tenant_id)
    engagement = await agg.get_engagement_metrics(days=days)
    llm = await agg.get_llm_provider_stats(days=days)
    activity = await agg.get_activity_trend(days=days, tenant_id=tenant_id)
    sentiment_by_user = await agg.get_sentiment_by_user(days=days, limit=20, tenant_id=tenant_id)
    activity_by_user = await agg.get_activity_by_user(days=days, limit=20, tenant_id=tenant_id)

    # Chains: group ai_analytics rows by topic_chain_id (Phase 4 display)
    from datetime import date, timedelta

    from app.models import AIAnalytics
    from app.services.ai.chain_resolver import human_chain_label

    chain_query = AIAnalytics.objects.filter(AIAnalytics.topic_chain_id.isnot(None))
    if tenant_id is not None:
        chain_query = chain_query.filter(tenant_id=tenant_id)
    if source_id is not None:
        chain_query = chain_query.filter(source_id=source_id)
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
        chains_result.append(
            {
                "chain_id": entry["chain_id"],
                "chain_label": entry["chain_label"],
                "entry_count": entry["entry_count"],
                "date_range": {
                    "start": entry["first_date"].strftime("%d.%m.%Y") if entry["first_date"] else None,
                    "end": entry["last_date"].strftime("%d.%m.%Y") if entry["last_date"] else None,
                },
                "avg_sentiment": round(sum(scores) / len(scores), 3) if scores else None,
            }
        )
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
    peak_users_display = "—" if peak_users == 0 else peak_users

    return {
        "sentiment": sentiment,
        "sentiment_dist": dist,
        "topics": topics,
        "entities": entities,
        "engagement": engagement,
        "llm": llm,
        "activity": activity,
        "sentiment_by_user": sentiment_by_user,
        "activity_by_user": activity_by_user,
        "chains": chains_result,
        "kpis": {
            "total_analyses": total_analyses,
            "total_posts": total_posts,
            "peak_users": peak_users_display,
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

    source_id = request.query_params.get("source_id")
    if source_id:
        try:
            source_id = int(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="source_id must be an integer") from exc
    else:
        source_id = None
    data = await _analytics(tenant_id, is_superuser, days, filter_tenant_id, source_id)
    # Grouped analytics — axis switcher on the page.
    grouped = await _fetch_grouped(days, filter_tenant_id, is_superuser, tenant_id, request)

    for chain in data.get("chains", []):
        chain["url"] = _analytics_url(
            request,
            "/app/analytics/chains/" + quote(chain["chain_id"], safe=""),
            days_key=days_key,
            return_to=_analytics_origin(request, days_key),
        )

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
        grouped_data=grouped,
        chains_url=_analytics_url(request, "/app/analytics/chains", days_key=days_key),
    )


@router.get("/group")
async def analytics_group(request: Request, axis: str, value: str, entity_type: str | None = None):
    """Read-only, flat membership list; chain-less rows are intentionally included."""
    from datetime import date

    from app.services.ai.grouping import _digest_value, _extract_sentiment, filter_analytics, matches_analytics_group

    denied = guard_web(request, "aianalytics", "view", back="/app")
    if denied is not None:
        return denied
    if axis not in DRILL_LABELS:
        raise HTTPException(status_code=400, detail=f"Use axis: {', '.join(DRILL_LABELS)}")
    if entity_type is not None and entity_type not in ("person", "brand", "org"):
        raise HTTPException(status_code=400, detail="Use entity_type: person, brand, org")
    if axis == "sources":
        try:
            int(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Source group value must be an integer ID") from exc
    if axis == "sentiment" and value not in SENTIMENT_FILTERS:
        raise HTTPException(status_code=400, detail=f"Use sentiment value: {', '.join(SENTIMENT_FILTERS)}")
    if axis == "content_type" and value not in MEDIA_FILTERS:
        raise HTTPException(status_code=400, detail=f"Use content_type value: {', '.join(MEDIA_FILTERS)}")
    days, days_key = _resolve_days(request)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _ = await tenant_filter_context(request, is_superuser) if is_superuser else (None, [])
    rows = await _scoped_analytics_rows(request, days, filter_tenant_id)
    chain_counts = Counter(row.topic_chain_id for row in rows if row.topic_chain_id)
    group_origin = _analytics_origin(request, days_key)
    try:
        rows = filter_analytics(
            rows,
            sentiment=request.query_params.get("sentiment") or None,
            media=request.query_params.get("media") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = [
        row
        for row in rows
        if matches_analytics_group(
            row, axis, value, entity_type=entity_type, group_by_period=request.query_params.get("group_by_period")
        )
    ]
    rows.sort(key=lambda row: (row.analysis_date or date.min, row.id), reverse=True)
    names = await _source_names({row.source_id for row in rows})
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "url": _analytics_url(request, f"/app/analytics/{row.id}", days_key=days_key, return_to=group_origin),
                "date": row.analysis_date,
                "source_name": names.get(row.source_id, f"Источник #{row.source_id}"),
                "title": _digest_value(row.summary_data, ("analysis_title",)) or f"Анализ #{row.id}",
                "sentiment": _extract_sentiment(row.summary_data),
                "chain_url": (
                    _analytics_url(
                        request,
                        "/app/analytics/chains/" + quote(row.topic_chain_id, safe=""),
                        days_key=days_key,
                        return_to=group_origin,
                    )
                    if row.topic_chain_id and chain_counts[row.topic_chain_id] > 1
                    else None
                ),
                "chain_count": chain_counts.get(row.topic_chain_id, 0),
            }
        )
    # Old sentiment/content_type groups are filters, not resurrected tabs.
    back_axis = request.query_params.get("group_by") or (axis if axis not in REMOVED_GROUPING_AXES else "themes")
    if back_axis not in ("days", "themes", "sources", "entities", "intent", "topic_chains"):
        back_axis = "themes"
    return render(
        request,
        "web/analytics_group.html",
        section="analytics",
        items=items,
        back_label=_analysis_back_label(
            _safe_analytics_return(request.query_params.get("return_to")) or "/app/analytics", "К группам"
        ),
        axis_label=DRILL_LABELS[axis],
        value=names.get(int(value), value) if axis == "sources" else value,
        back_url=_safe_analytics_return(request.query_params.get("return_to"))
        or _analytics_url(request, "/app/analytics", days_key=days_key, group_by=back_axis),
    )


async def _fetch_grouped(
    days: int | None,
    filter_tenant_id: int | None,
    is_superuser: bool,
    tenant_id: int | None,
    request: Request,
) -> dict:
    """Fetch grouped analytics for the axis switcher.

    Reads group_by / entity_type from query params, applies the same tenant
    scoping as the main analytics data, and returns a dict suitable for
    template rendering. The `days` axis gives the per-date chronology, so no
    separate time_breakdown toggle here.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics
    from app.services.ai.grouping import group_analytics
    from app.types.enums.bot_types import GroupingAxis

    group_by = request.query_params.get("group_by", "themes")
    if group_by in REMOVED_GROUPING_AXES:
        raise HTTPException(
            status_code=400,
            detail="Use: days, themes, sources, entities, intent; topic_chains has its own /app/analytics/chains page",
        )
    sentiment = request.query_params.get("sentiment") or None
    media = request.query_params.get("media") or None
    if sentiment is not None and sentiment not in SENTIMENT_FILTERS:
        raise HTTPException(status_code=400, detail=f"Use sentiment: {', '.join(SENTIMENT_FILTERS)}")
    if media is not None and media not in MEDIA_FILTERS:
        raise HTTPException(status_code=400, detail=f"Use media: {', '.join(MEDIA_FILTERS)}")
    entity_type = request.query_params.get("entity_type")
    group_by_period = request.query_params.get("group_by_period", "day")
    if group_by_period not in ("day", "week", "month"):
        group_by_period = "day"

    try:
        axis = GroupingAxis(group_by)
    except ValueError:
        axis = GroupingAxis.THEMES

    # Entity type filter is only valid for ENTITIES axis.
    if entity_type and axis != GroupingAxis.ENTITIES:
        entity_type = None
    if entity_type and entity_type not in {"person", "brand", "org"}:
        raise HTTPException(status_code=400, detail="Use entity_type: person, brand, org")

    rows = await _scoped_analytics_rows(request, days, filter_tenant_id)

    try:
        result = await group_analytics(
            rows,
            axis=axis,
            entity_type=entity_type if axis == GroupingAxis.ENTITIES else None,
            group_by_period=group_by_period if axis == GroupingAxis.DAYS else None,
            sentiment=sentiment,
            media=media,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Compute group counts for all axes to drive tab visibility and count badges.
    axes_for_counts = [
        ("days", GroupingAxis.DAYS),
        ("themes", GroupingAxis.THEMES),
        ("sources", GroupingAxis.SOURCES),
        ("entities", GroupingAxis.ENTITIES),
        ("intent", GroupingAxis.INTENT),
    ]
    group_counts = {}
    for ax_val, ax_enum in axes_for_counts:
        if ax_val == axis.value:
            group_counts[ax_val] = len(result.get("groups", []))
        else:
            try:
                entity_type_filter = entity_type if ax_enum == GroupingAxis.ENTITIES else None
                period = group_by_period if ax_enum == GroupingAxis.DAYS else None
                res = await group_analytics(
                    rows,
                    axis=ax_enum,
                    entity_type=entity_type_filter,
                    group_by_period=period,
                    sentiment=sentiment,
                    media=media,
                )
                group_counts[ax_val] = len(res.get("groups", []))
            except ValueError:
                group_counts[ax_val] = 0

    source_names = {}
    if axis == GroupingAxis.DAYS and result.get("groups"):
        source_ids = {
            entry.get("source_id")
            for group in result.get("groups", [])
            for entry in group.get("entries", [])
            if entry.get("source_id")
        }
        source_names = await _source_names(source_ids)

    days_key = "all" if days is None else str(days)
    for group in result.get("groups", []):
        if axis == GroupingAxis.TOPIC_CHAINS:
            group["url"] = _analytics_url(
                request,
                "/app/analytics/chains/" + quote(str(group["chain_id"]), safe=""),
                days_key=days_key,
                return_to=_analytics_origin(request, days_key),
            )
        else:
            value = group["source_id"] if axis == GroupingAxis.SOURCES else group["key"]
            group["url"] = _analytics_url(
                request,
                "/app/analytics/group",
                days_key=days_key,
                axis=axis.value,
                value=value,
                return_to=_analytics_origin(request, days_key),
            )

    if axis == GroupingAxis.DAYS:
        for group in result.get("groups", []):
            for entry in group.get("entries", []):
                entry["url"] = _analytics_url(
                    request,
                    f"/app/analytics/{entry['id']}",
                    days_key=days_key,
                    return_to=_analytics_origin(request, days_key),
                )

    return {
        "groups": result.get("groups", []),
        "axis": result.get("axis", "themes"),
        "entity_type": entity_type if axis == GroupingAxis.ENTITIES else None,
        "group_counts": group_counts,
        "visible_axes": [value for value, _ in axes_for_counts if group_counts[value] >= 2 or value == axis.value],
        "sentiment": sentiment,
        "media": media,
        "axis_urls": {value: str(request.url.include_query_params(group_by=value)) for value, _ in axes_for_counts},
        "entity_urls": {
            value: str(request.url.include_query_params(group_by="entities", entity_type=value))
            for value in ("brand", "person", "org")
        },
        "period_urls": {
            value: str(request.url.include_query_params(group_by="days", group_by_period=value))
            for value in ("day", "week", "month")
        },
        "group_by_period": group_by_period,
        "source_names": source_names,
    }


async def _source_names(source_ids: set[int]) -> dict[int, str]:
    """id → name for a set of source ids, so chain views link by name."""
    if not source_ids:
        return {}
    from app.models import Source

    rows = await Source.objects.filter(Source.id.in_(source_ids))
    return {s.id: s.name for s in rows}


@router.get("/chains")
async def analytics_chains(request: Request):
    """All theme chains with a chronological retrospective per chain.

    A chain groups the analyses of one ongoing theme/source/user over time.
    This page lists every chain the workspace has and, for each, a timeline of
    its analyses — the "удобный просмотр ретроспективы" the user asked for.
    Can be filtered by entity mention (entity_name, entity_type), source (source_id),
    sentiment (sentiment), content type (content_type), intent (intent), and chain id (chain_id).
    Inherits the same date range and tenant context as the main analytics page.
    The list sorts by the chain's latest analysis date: `?sort=desc` (default,
    newest first) or `?sort=asc` (oldest first).
    """
    from app.models import AIAnalytics
    from app.services.ai.grouping import _extract_entities, _extract_intent, _extract_sentiment, filter_analytics
    from app.services.ai.topic_chain_service import TopicChainService

    # Resolve days and tenant context like the main analytics page
    days, days_key = _resolve_days(request)
    sort = request.query_params.get("sort", "desc")
    if sort not in dict(CHAIN_SORTS):
        sort = "desc"
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _ = await tenant_filter_context(request, is_superuser) if is_superuser else (None, [])

    # Get optional filters
    entity_name = request.query_params.get("entity_name")
    entity_type = request.query_params.get("entity_type")
    source_id = request.query_params.get("source_id")
    sentiment = request.query_params.get("sentiment") or None
    media = request.query_params.get("media") or request.query_params.get("content_type") or None
    intent = request.query_params.get("intent")
    chain_id = request.query_params.get("chain_id")

    # Build base query: rows with topic_chain_id not null
    qs = AIAnalytics.objects.filter(AIAnalytics.topic_chain_id.isnot(None))

    # Apply date filter if days is specified
    if days is not None:
        from datetime import date, timedelta

        qs = qs.filter(analysis_date__gte=date.today() - timedelta(days=days))

    # Apply tenant filter
    if is_superuser and tenant_id is None:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            rows = list(await qs)
    else:
        rows = list(await qs)

    if filter_tenant_id is not None:
        rows = [row for row in rows if row.tenant_id == filter_tenant_id]
    try:
        rows = filter_analytics(rows, sentiment=sentiment, media=media)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Apply additional filters in Python
    filtered_rows = []
    for row in rows:
        match = True

        # source_id filter
        if source_id is not None and str(row.source_id) != source_id:
            match = False

        # intent filter
        if match and intent is not None:
            intnt = _extract_intent(row.summary_data or {})
            if intnt != intent:
                match = False

        # chain_id filter (topic_chain_id)
        if match and chain_id is not None and row.topic_chain_id != chain_id:
            match = False

        # entity filter
        if match and entity_name and entity_type:
            entities = _extract_entities(row.summary_data or {})
            entity_found = False
            for ent in entities:
                if ent["name"] == entity_name and ent["type"] == entity_type:
                    entity_found = True
                    break
            if not entity_found:
                match = False

        if match:
            filtered_rows.append(row)

    rows = filtered_rows

    # Build chains from the (possibly filtered) rows
    chain_data = TopicChainService().build_topic_chain(rows)

    # Source names for every source a chain's steps touch, so the list can show
    # "по источнику" вместо bare #id.
    source_ids = {
        step["source_info"]["source_id"]
        for ch in chain_data.values()
        for step in ch.get("evolution", [])
        if step.get("source_info", {}).get("source_id")
    }
    names = await _source_names(source_ids)

    scores_by_chain = {}
    for row in rows:
        extracted = _extract_sentiment(row.summary_data)
        if extracted:
            scores_by_chain.setdefault(row.topic_chain_id, []).append(extracted["score"])
    for key, chain in chain_data.items():
        scores = scores_by_chain.get(key, [])
        chain["avg_sentiment"] = sum(scores) / len(scores) if scores else None
    if sort == "sentiment_asc":
        chains = sorted(
            chain_data.values(),
            key=lambda ch: (
                ch["avg_sentiment"] is None,
                ch["avg_sentiment"] if ch["avg_sentiment"] is not None else 0,
                ch.get("chain_id") or "",
            ),
        )
    else:
        chains = sorted(
            chain_data.values(), key=lambda ch: ch.get("date_range", {}).get("end") or "", reverse=(sort == "desc")
        )
    for chain in chains:
        chain["url"] = _analytics_url(
            request,
            "/app/analytics/chains/" + quote(chain["chain_id"], safe=""),
            days_key=days_key,
            return_to=_analytics_origin(request, days_key),
        )

    chain_filters = {
        key: value
        for key, value in request.query_params.items()
        if key
        in {
            "days",
            "tenant_id",
            "source_id",
            "entity_name",
            "entity_type",
            "sentiment",
            "media",
            "content_type",
            "intent",
            "chain_id",
        }
    }
    chain_filters["days"] = days_key
    return render(
        request,
        "web/analytics_chains.html",
        section="analytics",
        chains=chains,
        source_names=names,
        total_chains=len(chains),
        sort=sort,
        sorts=CHAIN_SORTS,
        chain_filters=chain_filters,
        analytics_url=_analytics_url(request, "/app/analytics", days_key=days_key),
        perms_can=perms_can,
    )


@router.get("/chains/{chain_id}")
async def analytics_chain_detail(request: Request, chain_id: str):
    """One chain's full retrospective: a chronological timeline of analyses."""
    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics
    from app.services.ai.topic_chain_service import TopicChainService

    # Resolve days and tenant context like the main analytics page
    days, days_key = _resolve_days(request)
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _ = await tenant_filter_context(request, is_superuser) if is_superuser else (None, [])

    rows = await _scoped_analytics_rows(request, days, filter_tenant_id, chain_id=chain_id)

    if not rows:
        return render(request, "web/not_found.html", status_code=404)

    chain_data = TopicChainService().build_topic_chain(rows).get(chain_id)
    if chain_data is None:
        chain_data = {"chain_id": chain_id, "evolution": [], "total_analyses": 0, "date_range": {}}

    # The chain service's legacy title lookup is top-level only. Read the same
    # nested/flat title contract as the drill-down list for every timeline entry.
    from app.services.ai.grouping import _digest_value

    rows_by_id = {row.id: row for row in rows}
    for step in chain_data.get("evolution", []):
        row = rows_by_id.get(step.get("id"))
        if row is not None:
            step["analysis_title"] = _digest_value(row.summary_data, ("analysis_title",)) or step.get("analysis_title")

    source_ids = {
        step["source_info"]["source_id"]
        for step in chain_data.get("evolution", [])
        if step.get("source_info", {}).get("source_id")
    }
    names = await _source_names(source_ids)

    chains_url = _analytics_url(request, "/app/analytics/chains", days_key=days_key)
    back_url = _safe_analytics_return(request.query_params.get("return_to")) or chains_url
    back_label = {
        "/app/analytics/group": "К группе",
        "/app/analytics": "К аналитике",
        "/app/analytics/": "К аналитике",
        "/app/analytics/chains": "Все цепочки",
    }.get(urlsplit(back_url).path, "Все цепочки")

    if re.fullmatch(r"/app/analytics/[0-9]+", urlsplit(back_url).path):
        back_label = "К анализу"
    for step in chain_data.get("evolution", []):
        if step.get("id"):
            step["analysis_url"] = _analytics_url(
                request,
                f"/app/analytics/{step['id']}",
                days_key=days_key,
                return_to=_analytics_origin(request, days_key),
            )

    return render(
        request,
        "web/analytics_chain_detail.html",
        section="analytics",
        chain=chain_data,
        chains_url=chains_url,
        back_url=back_url,
        back_label=back_label,
        source_names=names,
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
    """One stored analysis with honest metric states and origin-aware navigation."""
    from datetime import date

    from app.models import AIAnalytics, Platform
    from app.services.ai.analysis_render import render_analysis
    from app.utils.date_parsing import universal_date_parser

    denied = guard_web(request, "aianalytics", "view", back="/app")
    if denied is not None:
        return denied
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _ = await tenant_filter_context(request, is_superuser) if is_superuser else (None, [])
    tenant_id = filter_tenant_id if filter_tenant_id is not None else getattr(request.state, "tenant_id", None)
    query = AIAnalytics.objects.select_related("source")
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    row = await query.get(id=analysis_id)
    if row is None:
        return render(request, "web/not_found.html", status_code=404)
    display = render_analysis(row.summary_data or {})
    # A direct historic permalink should not lose its own chain to the default
    # current-month session window. Navigation links carry an explicit period.
    days, days_key = _resolve_days(request) if request.query_params.get("days") is not None else (None, "all")
    origin = _analytics_origin(request, days_key)
    back_url = _safe_analytics_return(request.query_params.get("return_to")) or _analytics_url(
        request, "/app/analytics/group", days_key=days_key, axis="sources", value=row.source_id, tenant_id=row.tenant_id
    )
    chain = []
    if row.topic_chain_id:
        rows = await _scoped_analytics_rows(request, days, row.tenant_id, chain_id=row.topic_chain_id)
        for entry in sorted(rows, key=lambda entry: (entry.analysis_date or date.min, entry.id)):
            rendered = render_analysis(entry.summary_data or {})
            chain.append(
                {
                    "id": entry.id,
                    "analysis_date": entry.analysis_date,
                    "title": rendered["analysis_title"] or entry.chain_label or f"Анализ #{entry.id}",
                    "url": _analytics_url(request, f"/app/analytics/{entry.id}", days_key=days_key, return_to=origin),
                }
            )
    chain_url = (
        _analytics_url(
            request,
            "/app/analytics/chains/" + quote(row.topic_chain_id, safe=""),
            days_key=days_key,
            tenant_id=row.tenant_id,
            return_to=origin,
        )
        if len(chain) > 1
        else None
    )
    platform_name = display["source_metadata"].get("platform")
    platform = await Platform.objects.get(id=row.source.platform_id) if row.source else None
    if not platform_name:
        platform_name = platform.name if platform else None
    from app.utils.enum_helpers import get_enum_value

    vk_reactions = bool(platform and get_enum_value(platform.platform_type) in ("vk", "vkontakte"))
    platform_name = {"vk": "VK", "vkontakte": "VK", "telegram": "Telegram", "max": "MAX"}.get(
        str(platform_name).casefold(), platform_name
    )
    topics = [
        {
            "label": topic,
            "url": _analytics_url(
                request,
                "/app/analytics/group",
                days_key=days_key,
                axis="themes",
                value=topic,
                tenant_id=row.tenant_id,
                return_to=origin,
            ),
        }
        for topic in display["main_topics"]
    ]
    return render(
        request,
        "web/analytics_detail.html",
        section="analytics",
        analysis=row,
        display=display,
        chain=chain,
        chain_url=chain_url,
        back_url=back_url,
        topics=topics,
        back_label=_analysis_back_label(
            back_url,
            "К аналитике" if urlsplit(back_url).path in ("/app/analytics", "/app/analytics/") else "К списку анализов",
        ),
        platform_name=platform_name or "Не сохранена",
        vk_reactions=vk_reactions,
        window_start=universal_date_parser(display["content_window_start"]),
        window_end=universal_date_parser(display["content_window_end"]),
        analyzed_at=universal_date_parser(display["analysis_metadata"].get("analysis_timestamp")),
    )
