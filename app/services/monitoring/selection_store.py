"""Persist collection-time selection evidence without storing rejected text."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.database import new_session
from app.models import CollectionLaneRun, CollectionSeen
from app.services.ai.dedup import item_hash
from app.services.monitoring.selection import CollectionSelection

logger = logging.getLogger(__name__)


async def record_collection_selection(
    source: Any,
    content: list[dict],
    selection: CollectionSelection,
    *,
    run_id: int | None,
    task_id: int | None,
    limit_count: int | None = None,
    limit_reached: bool = False,
) -> None:
    """Write the seen ledger and one per-source lane run.

    Best-effort: a ledger failure must not fail a collection whose raw rows are
    already safely staged. The caller can report the collection counts; the
    failure is bounded in logs, never the content itself.
    """
    outcomes = []
    for decision in selection.decisions:
        item = decision.item
        if not isinstance(item, dict):
            continue
        digest = item_hash(item)
        external_id = item.get("external_id") or item.get("id") or f"hash:{digest}"
        outcomes.append(
            {
                "content_type": item.get("content_type") or "post",
                "external_id": str(external_id),
                "content_hash": digest,
                "status": decision.status,
            }
        )

    limit = source_limit(source)
    session = new_session()
    try:
        async with session.begin():
            await CollectionSeen.objects.record_outcomes(
                session,
                source.id,
                outcomes,
                tenant_id=source.tenant_id,
            )
            await CollectionLaneRun.objects.record(
                session,
                tenant_id=source.tenant_id,
                job_id=run_id,
                task_id=task_id,
                lane_id=f"source:{source.id}",
                lane_type="source_stage",
                query=_source_query(source),
                fetched=sum(
                    1 for item in content
                    if str(item.get("content_type") or "post").lower() != "comment"
                ),
                selected=len(selection.selected),
                needs_review=len(selection.needs_review),
                rejected=len(selection.rejected),
                limit_count=limit_count if limit_count is not None else limit,
                limit_reached=limit_reached,
            )
    except Exception:  # noqa: BLE001 - evidence failure is not a collection failure
        logger.warning(
            "collection_selection_ledger_failed source_id=%s error_code=selection_ledger_failed",
            getattr(source, "id", "?"),
        )
    finally:
        await session.close()


def source_limit(source: Any, task_limit: int | None = None) -> int:
    """Return the parent-post cap, with a source override only able to lower it.

    Task collection intent supplies the run cap. Source settings may narrow
    that request. Without task context, an explicit source cap replaces the
    deployment default, preserving legacy collector behavior. The deployment
    ceiling applies in either case.
    """
    hard_limit = max(1, int(settings.SOCIAL_COLLECT_HARD_LIMIT))
    default_limit = min(max(1, int(settings.SOCIAL_COLLECT_LIMIT)), hard_limit)
    try:
        requested = int(task_limit) if task_limit is not None else default_limit
        if requested < 1:
            requested = default_limit
    except (TypeError, ValueError):
        requested = default_limit

    params = getattr(source, "params", None) or {}
    collection = params.get("collection") if isinstance(params, dict) else None
    source_request = collection.get("limit") if isinstance(collection, dict) else None
    if source_request is None and isinstance(params, dict):
        source_request = params.get("limit")
    if source_request is not None:
        try:
            source_cap = int(source_request)
            if source_cap > 0:
                requested = min(requested, source_cap) if task_limit is not None else source_cap
        except (TypeError, ValueError):
            pass
    return max(1, min(requested, hard_limit))


def limit_reached(content: list[dict], limit: int) -> bool:
    return bool(limit) and len(content) >= limit


def _source_query(source: Any) -> str | None:
    params = getattr(source, "params", None) or {}
    query = params.get("query") if isinstance(params, dict) else None
    return query if isinstance(query, str) and query.strip() else None
