from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..job import Job


class JobManager(BaseManager):
    """Manager for the Job queue: enqueue, atomic claim, retries."""

    def __init__(self):
        from ..job import Job

        super().__init__(Job)

    async def enqueue(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        *,
        schedule_id: Optional[int] = None,
        run_at: Optional[datetime] = None,
        max_attempts: Optional[int] = None,
    ) -> "Job":
        """Create a pending job."""
        from app.core.config import settings

        return await self.create(
            job_type=job_type,
            payload=payload or {},
            schedule_id=schedule_id,
            run_at=run_at or datetime.now(timezone.utc),
            max_attempts=max_attempts or settings.JOB_MAX_ATTEMPTS,
        )

    async def claim_next(self, now: Optional[datetime] = None) -> Optional["Job"]:
        """
        Atomically claim the oldest due pending job (FOR UPDATE SKIP LOCKED)
        and mark it running. Safe for multiple workers.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker

        from ..job import Job as JobModel

        now = now or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                stmt = (
                    select(JobModel)
                    .where(JobModel.status == "pending", JobModel.run_at <= now)
                    .order_by(JobModel.run_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                job = (await session.execute(stmt)).scalar_one_or_none()
                if not job:
                    return None
                job.status = "running"
                job.locked_at = now
                job.attempts = (job.attempts or 0) + 1
                await session.flush()
                # Detach-safe: return after commit, expire_on_commit handles state
                return job

    async def mark_done(self, job_id: int, result: Optional[dict] = None) -> None:
        await self.update_by_id(job_id, status="done", result=result, error=None)

    async def mark_failed(self, job_id: int, error: str) -> bool:
        """
        Record failure. Re-schedule with backoff if attempts remain, else mark failed.
        Returns True if the job will be retried.
        """
        from app.core.config import settings

        job = await self.get(id=job_id)
        if not job:
            return False
        if job.attempts < job.max_attempts:
            delay = settings.JOB_RETRY_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
            await self.update_by_id(
                job_id,
                status="pending",
                run_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
                error=error[:2000],
            )
            return True
        await self.update_by_id(job_id, status="failed", error=error[:2000])
        return False

    async def reap_stale(self, timeout_minutes: int = 30) -> int:
        """Requeue jobs stuck in 'running' longer than timeout (crashed worker)."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        stale = await self.filter(status="running", locked_at__lt=cutoff)
        for job in stale:
            await self.update_by_id(job.id, status="pending", locked_at=None)
        return len(stale)
