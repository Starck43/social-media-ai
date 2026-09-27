"""Digest builder: aggregate analytics for a period, summarize via LLM, publish."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.core.config import settings
from app.services.ai.llm_client import LLMClientFactory, resolve_model
from app.services.ai.reporting import ReportAggregator
from app.services.digest.render import render_digest

logger = logging.getLogger(__name__)


class DigestDeliveryError(RuntimeError):
    """Raised when a digest was built but could not be delivered to a channel."""


DIGEST_PROMPT_TEMPLATE = """Ты — персональный аналитик. Составь краткую сводку на основе агрегированных данных.

Правила:
- Пиши на русском языке, живым и понятным языком
- Выдели 2-4 самых важных тренда или наблюдения
- Если данных мало, просто скажи об этом

Данные:
{data}

Верни JSON: {{\"summary\": \"...\"}}"""


def period_bounds(period: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "week":
        start = today - timedelta(days=6)
    else:
        start = today
    return start, today


async def _summarize(data: dict[str, Any]) -> tuple[str | None, dict]:
    from app.services.tenancy.resolver import current_daily_cost_limit, daily_cost_today

    # The daily cap is checked before the model is touched: past the limit the
    # digest still ships, just rendered from raw aggregates without a summary.
    limit = await current_daily_cost_limit()
    if limit and await daily_cost_today() >= limit:
        logger.warning("Daily LLM cost cap reached — digest summary skipped")
        return None, {"model": None, "cost_cap": True}

    model = await resolve_model()
    if not model:
        return None, {"model": None}
    try:
        client = LLMClientFactory.create(model)
        prompt = DIGEST_PROMPT_TEMPLATE.format(data=str(data)[:6000])
        result = await client.analyze(prompt, max_tokens=500, temperature=0.3)
        cost = float((result.get("usage") or {}).get("cost") or 0.0)
        parsed = result.get("parsed") or {}
        summary = parsed.get("summary") or parsed.get("analysis")
        if isinstance(summary, str) and summary.strip():
            return summary.strip(), {"model": model.name, "cost": cost}
        return None, {"model": model.name, "cost": cost}
    except Exception as e:
        logger.error(f"Digest LLM summary failed: {e}")
        return None, {"model": getattr(model, "name", None), "error": str(e)}


async def aggregate(period: str) -> tuple[dict[str, Any], date, date]:
    start, end = period_bounds(period)
    days = (end - start).days + 1
    agg = ReportAggregator()

    sentiment = await agg.get_sentiment_trends(days=days)
    topics = await agg.get_top_topics(days=days, limit=8)
    content_mix = await agg.get_content_mix(days=days)
    engagement = await agg.get_engagement_metrics(days=days)
    llm_stats = await agg.get_llm_provider_stats(days=days)

    dist = {"positive": 0, "neutral": 0, "negative": 0}
    total_analyses = 0
    for point in sentiment:
        d = point.get("distribution") or {}
        for key in dist:
            dist[key] += d.get(key, 0) or 0
        total_analyses += point.get("total_analyses", 0) or 0

    data: dict[str, Any] = {
        "title": "📊 Дайджест" if period == "day" else "📊 Недельный дайджест",
        "period": period,
        "period_start": start,
        "period_end": end,
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


async def build_and_publish(period: str = "day", agent_task_id: int | None = None) -> dict[str, Any]:
    from app.channels.registry import broadcast_digest
    from app.models.managers.digest_run_manager import DigestRunManager

    runs = DigestRunManager()
    start, end = period_bounds(period)
    channel = "auto"

    if agent_task_id is not None and await runs.already_sent(agent_task_id, start, end):
        logger.info(f"Digest for task {agent_task_id} period {start}..{end} already sent — skipping")
        return {"status": "skipped", "reason": "already_sent"}

    run = await runs.start_run(
        agent_task_id=agent_task_id, period=period, period_start=start, period_end=end, channel=channel
    )
    if run is None:
        return {"status": "failed", "error": "Could not create digest run"}

    try:
        data, _start, _end = await aggregate(period)
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
