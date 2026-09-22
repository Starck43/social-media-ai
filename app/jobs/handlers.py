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
    """Build and send a digest to configured channels. Implemented in M2 (channels+digest)."""
    logger.info(f"digest job payload={payload} (handler lands in M2)")
    return {"status": "skipped", "reason": "digest handler arrives in M2"}


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
