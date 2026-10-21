"""Write the per-run evidence rows `collection_lane_runs` exists for.

The row answers "what did this lane actually do in this run": fetched,
selected / needs-review / rejected counts and the effective bound — never the
rejected text. One call writes exactly one row through the ORM and returns its
id, so a caller can reference the row from job results.
"""

from __future__ import annotations

from datetime import datetime, timezone


async def record_lane_run(
    *,
    tenant_id: int,
    job_id: int | None,
    task_id: int | None,
    lane_id: str,
    lane_type: str,
    query: str | None = None,
    fetched: int = 0,
    selected: int = 0,
    needs_review: int = 0,
    rejected: int = 0,
    limit_count: int | None = None,
    limit_reached: bool = False,
    error_code: str | None = None,
    unsupported_reason: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> int:
    """Persist ONE lane-run row for `tenant` and return its id.

    Job and task ids stay logical references (the table has no FKs), so the
    row survives a job prune. `started_at`/`finished_at` default to "now" so a
    caller may time the step itself; absent timestamps still mean "this run".
    """
    from app.core.database import new_session
    from app.models import CollectionLaneRun

    now = datetime.now(timezone.utc)
    session = new_session()
    try:
        async with session.begin():
            row = await CollectionLaneRun.objects.create(
                session=session,
                tenant_id=int(tenant_id),
                job_id=job_id,
                task_id=task_id,
                lane_id=str(lane_id or "")[:100],
                lane_type=str(lane_type or "")[:30],
                query=query,
                fetched=max(0, int(fetched or 0)),
                selected=max(0, int(selected or 0)),
                needs_review=max(0, int(needs_review or 0)),
                rejected=max(0, int(rejected or 0)),
                limit_count=limit_count,
                limit_reached=bool(limit_reached),
                error_code=str(error_code)[:50] if error_code else None,
                unsupported_reason=str(unsupported_reason)[:255] if unsupported_reason else None,
                started_at=started_at or now,
                finished_at=finished_at or now,
            )
            return int(row.id)
    finally:
        await session.close()
