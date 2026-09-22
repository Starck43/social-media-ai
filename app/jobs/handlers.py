"""Background job handlers. Heavy work runs here (worker process)."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_collect(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Collect content from sources.

    Payload:
        source_ids: list[int] — specific sources; empty/missing = all active
    """
    from app.models import Source
    from app.services.monitoring.collector import ContentCollector

    source_ids = payload.get("source_ids") or []
    collector = ContentCollector()

    if source_ids:
        sources = []
        for sid in source_ids:
            source = await Source.objects.get(id=sid)
            if source:
                sources.append(source)
    else:
        sources = await Source.objects.filter(is_active=True)

    stats = {"sources": len(sources), "collected": 0, "failed": 0, "items": 0}
    for source in sources:
        try:
            result = await collector.collect_from_source(source)
            if result and result.get("content_count", 0) > 0:
                stats["collected"] += 1
                stats["items"] += result["content_count"]
            else:
                stats["failed"] += 1
        except Exception as e:
            logger.error(f"collect failed for source {source.id}: {e}", exc_info=True)
            stats["failed"] += 1
    return stats


async def handle_digest(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a digest and publish it to configured channels.

    Payload:
        period: 'day' | 'week' (default 'day')
        schedule_id: int | None — set when triggered by a schedule (idempotency)
    """
    from app.services.digest.builder import build_and_publish

    period = payload.get("period", "day")
    if period not in ("day", "week"):
        return {"status": "failed", "error": f"Invalid period: {period}"}
    return await build_and_publish(period=period, schedule_id=payload.get("schedule_id"))


async def handle_prune(payload: dict[str, Any]) -> dict[str, Any]:
    """Retention: trim old finished jobs (default: older than 7 days)."""
    from datetime import datetime, timedelta, timezone

    from app.models import Job

    days = int(payload.get("days", 7))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = await Job.objects.filter(status__in=["done", "failed"]).filter(Job.created_at < cutoff).delete()
    return {"deleted": deleted}


HANDLERS = {
    "collect": handle_collect,
    "digest": handle_digest,
    "prune": handle_prune,
}
