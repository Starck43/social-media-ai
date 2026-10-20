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

    limit = _source_limit(source)
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
                fetched=len(content),
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


def source_limit(source: Any) -> int:
    """The effective per-source run ceiling used by the legacy collector."""
    return _source_limit(source)


def limit_reached(content: list[dict], limit: int) -> bool:
    return bool(limit) and len(content) >= limit


def _source_limit(source: Any) -> int:
    params = getattr(source, "params", None) or {}
    collection = params.get("collection") if isinstance(params, dict) else None
    requested = (collection or {}).get("limit") if isinstance(collection, dict) else None
    if requested is None and isinstance(params, dict):
        requested = params.get("limit")
    try:
        value = int(requested) if requested else settings.SOCIAL_COLLECT_LIMIT
    except (TypeError, ValueError):
        value = settings.SOCIAL_COLLECT_LIMIT
    return max(1, min(value, settings.SOCIAL_COLLECT_HARD_LIMIT))


def _source_query(source: Any) -> str | None:
    params = getattr(source, "params", None) or {}
    query = params.get("query") if isinstance(params, dict) else None
    return query if isinstance(query, str) and query.strip() else None
