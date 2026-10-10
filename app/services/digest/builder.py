"""Digest builder: aggregate analytics for a period, summarize via LLM, publish."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.core.config import settings
from app.services.ai.llm_client import LLMClientFactory
from app.services.ai.reporting import ReportAggregator, normalize_digest_axis
from app.services.ai.output_contracts import (
    DigestSummary,
    OutputContractError,
    known_cost_usd,
    validate_output,
    response_is_incomplete,
)
from app.services.ai.prompt_sanitizer import frame_untrusted_text
from app.services.digest.coverage import job_coverage_note
from app.services.digest.render import render_digest

logger = logging.getLogger(__name__)


class DigestDeliveryError(RuntimeError):
    """Raised when a digest was built but could not be delivered to a channel."""


DIGEST_PROMPT_TEMPLATE = """Ты — персональный аналитик. На основе брифа (агрегированные данные за период) составь краткую связную сводку-нарратив.

Правила:
- Пиши на русском языке, живым и понятным языком
- Выдели 2-4 самых важных тренда или наблюдения
- Не пересказывай бриф дословно, а обобщай
- Если данных мало, просто скажи об этом
- Учитывай оговорку о неполноте: отсутствие сохранённых записей не означает отсутствие публикаций; не обещай полный охват

Бриф:
{data}

Верни JSON: {{\"summary\": \"...\"}}"""


def period_bounds(period: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "week":
        start = today - timedelta(days=6)
    elif period == "month":
        start = today - timedelta(days=29)
    else:
        start = today
    return start, today


async def _summarize(data: dict[str, Any]) -> tuple[str | None, dict]:
    from app.services.tenancy.resolver import current_daily_cost_limit, daily_cost_today

    limit = await current_daily_cost_limit()
    if limit and await daily_cost_today() >= limit:
        logger.warning("Daily LLM cost cap reached — digest summary skipped")
        return None, {"model": None, "cost_cap": True}

    from app.models import LLMModel

    model = await LLMModel.objects.resolve_default_model("text", strategy="quality")
    if not model:
        return None, {"model": None}
    try:
        client = LLMClientFactory.create(model)
        context = data.get("brief") or data
        # Keep the deterministic caveat first so the context cap cannot omit it.
        coverage_note = data.get("coverage_note")
        if coverage_note:
            context = f"{coverage_note}\n\n{context}"
        prompt = DIGEST_PROMPT_TEMPLATE.format(data=frame_untrusted_text(str(context)[:6000]))
        result = await client.analyze(prompt, max_tokens=500, temperature=0.3)
    except Exception:
        logger.error("Digest LLM summary call failed")
        return None, {"model": getattr(model, "name", None), "cost": None, "error": "llm_call_failed"}

    info = {"model": model.name, "cost": known_cost_usd(result)}
    if not isinstance(result, dict):
        return None, {**info, "error": "invalid_structured_output"}
    if response_is_incomplete(result):
        return None, {**info, "error": "incomplete_structured_output"}
    response = result.get("response")
    if isinstance(response, dict) and response.get("error"):
        return None, {**info, "error": "summary_provider_failed"}
    try:
        summary = validate_output(result.get("parsed"), DigestSummary)
    except OutputContractError:
        logger.warning("Digest summary output contract rejected")
        return None, {**info, "error": "invalid_structured_output"}
    return summary.summary, info


async def aggregate(
    period: str,
    source_ids: list[int] | None = None,
    group_by: str = "themes",
    time_breakdown: bool = False,
    scenario_id: int | None = None,
) -> tuple[dict[str, Any], date, date]:
    # "days" is a web-only grouping for the Chronology view; it must not be used
    # for digest generation (the API and CLI should reject it before reaching here).
    group_by = normalize_digest_axis(group_by)
    if group_by == "days":
        raise ValueError("group_by='days' is web-only; use themes, sources, entities, intent, or topic_chains")

    start, end = period_bounds(period)
    days = (end - start).days + 1
    agg = ReportAggregator()

    sentiment = await agg.get_sentiment_trends(days=days)
    topics = await agg.get_top_topics(days=days, limit=8)
    content_mix = await agg.get_content_mix(days=days)
    engagement = await agg.get_engagement_metrics(days=days)
    llm_stats = await agg.get_llm_provider_stats(days=days)

    # Hybrid step 1: the algorithmic brief, grouped by group_by axis
    # and extended with the scenario's analysis_types sections. No LLM here.
    brief = await agg.generate_digest_brief(
        period=period, source_ids=source_ids, group_by=group_by, time_breakdown=time_breakdown, scenario_id=scenario_id
    )

    # Persist a WEEKLY/MONTHLY rollup row per source so `period_type` reflects
    # the digest period (reads like get_by_date_range(period_type=WEEKLY) work).
    if period in ("week", "month"):
        from app.models.managers.ai_analytics_manager import AIAnalyticsManager
        from app.types import PeriodType

        period_type = PeriodType.WEEK if period == "week" else PeriodType.MONTH
        await AIAnalyticsManager().build_period_rollups(period_type, start, end)

    dist = {"positive": 0, "neutral": 0, "negative": 0}
    total_analyses = 0
    for point in sentiment:
        d = point.get("distribution") or {}
        for key in dist:
            dist[key] += d.get(key, 0) or 0
        total_analyses += point.get("total_analyses", 0) or 0

    data: dict[str, Any] = {
        "title": {
            "day": "📊 Дайджест",
            "week": "📊 Недельный дайджест",
            "month": "📊 Месячный дайджест",
        }.get(period, "📊 Дайджест"),
        "period": period,
        "period_start": start,
        "period_end": end,
        "brief": brief,
        "coverage_note": await job_coverage_note(start, end, source_ids, scenario_id),
        "stats": {
            "analyses": total_analyses,
            "content_items": content_mix.get("total", None) if isinstance(content_mix, dict) else content_mix,
        },
        "sentiment": {"distribution": dist},
        "topics": topics,
        "engagement": engagement,
        "content_mix": content_mix,
        "llm": llm_stats,
    }
    return data, start, end


async def build_and_publish(
    period: str = "day",
    agent_task_id: int | None = None,
    force: bool = False,
    source_ids: list[int] | None = None,
    group_by: str = "themes",
    time_breakdown: bool = False,
    scenario_id: int | None = None,
) -> dict[str, Any]:
    """Resolve and scope the entire run, including manual operator execution."""
    from app.channels.registry import resolve_digest_tenant
    from app.core.tenant_context import tenant_scope

    tenant = await resolve_digest_tenant()
    with tenant_scope(tenant.id):
        return await _build_and_publish_scoped(
            period, agent_task_id, force, source_ids, group_by, time_breakdown, scenario_id
        )


async def _build_and_publish_scoped(
    period: str,
    agent_task_id: int | None,
    force: bool,
    source_ids: list[int] | None,
    group_by: str,
    time_breakdown: bool,
    scenario_id: int | None,
) -> dict[str, Any]:
    from app.channels.registry import broadcast_digest
    from app.models.managers.digest_run_manager import DigestRunManager
    from app.services.digest.delivery_outcomes import DeliveryFailure
    from app.services.digest.job_delivery import assert_legacy_allowed, enabled

    if enabled():
        raise DeliveryFailure("checkpoint_delivery_requires_claimed_job")
    await assert_legacy_allowed(agent_task_id=agent_task_id, period=period)

    runs = DigestRunManager()
    start, end = period_bounds(period)
    channel = "auto"

    if agent_task_id is not None and not force and await runs.already_sent(agent_task_id, start, end):
        logger.info(f"Digest for task {agent_task_id} period {start}..{end} already sent — skipping")
        return {"status": "skipped", "reason": "already_sent"}

    run = await runs.start_run(
        agent_task_id=agent_task_id, period=period, period_start=start, period_end=end, channel=channel
    )
    if run is None:
        return {"status": "failed", "error": "Could not create digest run"}

    try:
        data, _start, _end = await aggregate(period, source_ids, group_by, time_breakdown, scenario_id)
        summary, llm_info = await _summarize(data)
        if llm_info.get("cost"):
            # Accumulate: a retried run really paid for every summary attempt.
            await runs.update_by_id(run.id, llm_cost=(run.llm_cost or 0.0) + float(llm_info["cost"]))
        data["llm"] = {**(data.get("llm") or {}), "model": llm_info.get("model")}
        text = render_digest(data, summary=summary)

        results = await broadcast_digest(text)
        if not results:
            await runs.update_by_id(run.id, status="skipped", content=text, error="No digest channels configured")
            return {"status": "skipped", "reason": "no_channels", "text": text}

        ok = all(r.get("success") for r in results.values())
        message_id = next((str(r.get("message_id")) for r in results.values() if r.get("message_id")), None)
        errors = [f"{k}: {r.get('error')}" for k, r in results.items() if not r.get("success")]

        updated = await runs.update_by_id(
            run.id,
            status="sent" if ok else "failed",
            message_id=message_id,
            content=text,
            error="\n".join(errors) or None,
        )
        if not updated:
            return {"status": "failed", "error": "Digest run not found after update"}
        run = updated
    except DigestDeliveryError:
        raise
    except Exception as e:
        await runs.update_by_id(run.id, status="failed", error=str(e)[:2000])
        logger.exception("Digest build/publish failed")
        return {"status": "failed", "error": str(e)}

    if not ok:
        logger.error(f"Digest {period} delivery failed: {errors}")
        raise DigestDeliveryError("; ".join(errors))

    logger.info(f"Digest {period} published: {results}")
    return {"status": run.status, "results": results, "text": text}
