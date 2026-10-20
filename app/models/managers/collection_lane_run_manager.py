"""Persistence for per-source collection lane evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.collection.collection_lane_run import CollectionLaneRun

from .base_manager import BaseManager


class CollectionLaneRunManager(BaseManager[CollectionLaneRun]):
    """Record one lane's counts and bounds after a collection attempt."""

    def __init__(self) -> None:
        super().__init__(CollectionLaneRun)

    async def record(
        self,
        session: Any,
        *,
        tenant_id: int,
        lane_id: str,
        lane_type: str,
        fetched: int,
        selected: int,
        needs_review: int = 0,
        rejected: int = 0,
        job_id: int | None = None,
        task_id: int | None = None,
        query: str | None = None,
        limit_count: int | None = None,
        limit_reached: bool = False,
        total_available: int | None = None,
        error_code: str | None = None,
        unsupported_reason: str | None = None,
    ) -> CollectionLaneRun:
        now = datetime.now(timezone.utc)
        return await self.create(
            session=session,
            tenant_id=tenant_id,
            job_id=job_id,
            task_id=task_id,
            lane_id=lane_id,
            lane_type=lane_type,
            query=query,
            fetched=max(0, int(fetched)),
            selected=max(0, int(selected)),
            needs_review=max(0, int(needs_review)),
            rejected=max(0, int(rejected)),
            limit_count=limit_count,
            limit_reached=bool(limit_reached),
            total_available=total_available,
            error_code=error_code,
            unsupported_reason=unsupported_reason,
            started_at=now,
            finished_at=now,
        )
