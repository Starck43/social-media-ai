"""Digest builder: aggregate analytics for a period, summarize via LLM, publish."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, cast

from app.core.config import settings
from app.services.ai.llm_client import LLMClientFactory
from app.services.ai.reporting import ReportAggregator
from app.services.digest.render import render_digest

logger = logging.getLogger(__name__)


class DigestDeliveryError(RuntimeError):
    """Raised when a digest was built but could not be delivered to a channel.

    Retryable by design: the job queue retries it, and the digest_runs row is
    reused for the same (schedule, period) instead of piling up duplicates.
    """


DIGEST_PROMPT_TEMPLATE = """You write daily/weekly digests for a social media monitoring service owner.

Below is aggregated analytics for the period (JSON). Write a concise summary in Russian:
- 2-4 sentences, business tone, no fluff;
- mention overall sentiment trend, key topics and notable activity/engagement changes;
- if data is empty, say there was little activity.

Aggregated data:
{data}

Return JSON: {{"summary": "..."}}"""


def period_bounds(period: str, today: date | None = None) -> tuple[date, date]:
    """Return inclusive (start, end) dates for 'day' or 'week' ending today."""
    today = today or date.today()
    if period == "week":
        start = today - timedelta(days=6)
    else:
        start = today
    return start, today


async def resolve_model():
    """Pick LLM model for agent chat + digests: AGENT_MODEL by name, else first active text model."""
    from app.models import LLMModel
    from app.models.managers.llm_model_manager import LLMModelManager

    if settings.AGENT_MODEL:
        model = await LLMModel.objects.select_related("provider").get(name=settings.AGENT_MODEL)
        if model:
            return model
        logger.warning(f"AGENT_MODEL {settings.AGENT_MODEL!r} not found, falling back to any active model")
    try:
        return await cast(LLMModelManager, LLMModel.objects).get_model_for_capability("text")
    except Exception as e:
        logger.error(f"No LLM model available: {e}")
        return None


# Backwards-compatible alias for internal callers
_resolve_model = resolve_model


async def _summarize(data: dict[str, Any]) -> tuple[str | None, dict]:
    """LLM summary of aggregated data. Returns (summary_text, llm_info)."""
    model = await _resolve_model()
    if not model:
        return None, {"model": None}
    try:
        client = await LLMClientFactory.create(model)
        prompt = DIGEST_PROMPT_TEMPLATE.format(data=str(data)[:6000])
        result = await client.analyze(prompt, max_tokens=500, temperature=0.3)
        parsed = result.get("parsed") or {}
        summary = parsed.get("summary") or parsed.get("analysis")
        if isinstance(summary, str) and summary.strip():
            return summary.strip(), {"model": model.name}
        return None, {"model": model.name}
    except Exception as e:
        logger.error(f"Digest LLM summary failed: {e}")
        return None, {"model": getattr(model, "name", None), "error": str(e)}


async def aggregate(period: str) -> tuple[dict[str, Any], date, date]:
    """Aggregate analytics for the period via ReportAggregator."""
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


async def build_and_publish(period: str = "day", schedule_id: int | None = None) -> dict[str, Any]:
    """
    Build digest for the period and publish to configured digest channels.

    Idempotent per (schedule_id, period) — sent digest won't be re-sent for
    the same schedule+period. Manual runs (schedule_id=None) always send.

    Raises DigestDeliveryError when the digest was built but not delivered
    (so the job queue retries); returns a result dict otherwise.
    """
    from app.channels.registry import broadcast_digest
    from app.models.managers.digest_run_manager import DigestRunManager

    runs = DigestRunManager()
    start, end = period_bounds(period)
    channel = "auto"

    if schedule_id is not None and await runs.already_sent(schedule_id, start, end):
        logger.info(f"Digest for schedule {schedule_id} period {start}..{end} already sent — skipping")
        return {"status": "skipped", "reason": "already_sent"}

    run = await runs.start_run(
        schedule_id=schedule_id,
        period=period,
        period_start=start,
        period_end=end,
        channel=channel,
    )
    if run is None:
        return {"status": "failed", "error": "Could not create digest run"}

    try:
        data, _start, _end = await aggregate(period)
        summary, llm_info = await _summarize(data)
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
