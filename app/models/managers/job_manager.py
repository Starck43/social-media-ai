from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..job import Job

# Remove jobs that finished more than this ago to keep the queue readable.
# Running/pending/failed rows survive — they need human attention or are in
# flight. Only "done" rows older than the threshold are pruned.
JOB_CLEANUP_AFTER_HOURS = 24


class JobManager(BaseManager["Job"]):
    """Manager for the Job queue: enqueue, atomic claim, retries."""

    def __init__(self):
        from ..job import Job

        super().__init__(Job)

    async def enqueue(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        *,
        agent_task_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
        run_at: Optional[datetime] = None,
        max_attempts: Optional[int] = None,
    ) -> "Job":
        """Create a pending job.

        `tenant_id` is honoured only in bypass mode (CLI/worker): it lets the
        caller stamp the owning tenant's id explicitly instead of falling back
        to the bootstrap owner workspace. In a normal tenant scope it is
        ignored (the scope wins).
        """
        from app.core.config import settings

        return await self.create(
            job_type=job_type,
            payload=payload or {},
            agent_task_id=agent_task_id,
            tenant_id=tenant_id,
            run_at=run_at or datetime.now(timezone.utc),
            max_attempts=max_attempts or settings.JOB_MAX_ATTEMPTS,
        )

    @staticmethod
    async def _claim(where_clause, now: Optional[datetime] = None) -> Optional["Job"]:
        """Atomically flip one matching pending job to `running`.

        Shared by `claim_next` (worker loop) and `claim_job` (operator runs a
        specific job). Cross-tenant by nature: a worker must be able to pick up
        any workspace's job, and the CLI must be able to run any job at all.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker

        from ..job import Job as JobModel

        now = now or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                stmt = (
                    select(JobModel)
                    .where(JobModel.status == "pending", JobModel.run_at <= now, where_clause)
                    .order_by(JobModel.run_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                job = (await session.execute(stmt)).scalar_one_or_none()
                if not job:
                    return None
                job.status = "running"
                job.locked_at = now
                job.started_at = now
                job.attempts = (job.attempts or 0) + 1
                await session.flush()
                # Detach-safe: return after commit, expire_on_commit handles state
                return job

    @classmethod
    async def claim_next(cls, now: Optional[datetime] = None) -> Optional["Job"]:
        """
        Atomically claim the oldest due pending job (FOR UPDATE SKIP LOCKED)
        and mark it running. Safe for multiple workers.
        """
        from sqlalchemy import and_

        return await cls._claim(and_(True), now=now)

    @classmethod
    async def claim_job(cls, job_id: int, now: Optional[datetime] = None) -> Optional["Job"]:
        """Claim one specific pending job by id.

        The operator path: "run *this* task" must execute the job it just
        enqueued. `claim_next` cannot be used for it — it takes the globally
        oldest pending job, which may belong to a different workspace.
        """
        from ..job import Job as JobModel

        return await cls._claim(JobModel.id == job_id, now=now)

    async def _record_task_result(self, job: "Job", status: str, error: Optional[str] = None) -> None:
        """Mirror the job outcome onto its AgentTask (if any) so the task's
        `last_status`/`last_error`/`last_run_at` reflect the real run result."""
        if not job.agent_task_id:
            return
        from app.models.managers.agent_task_manager import AgentTaskManager

        await AgentTaskManager().record_result(job.agent_task_id, status=status, error=error)

    async def cost_today(self, now: Optional[datetime] = None) -> float:
        """Total USD spent on learning/reflect LLM calls today (UTC day) — the daily cap check."""
        now = now or datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self.filter(created_at__gte=day_start)
        return sum(float(row.llm_cost or 0.0) for row in rows)

    @classmethod
    async def start_running(cls, job_id: int, now: Optional[datetime] = None) -> bool:
        """Atomically flip one `pending` job to `running`, without claiming it.

        The direct-run path ("Выполнить сейчас" must not queue) needs the row to
        leave `pending` before anyone else can take it, but it is already
        *running* — the caller is the one about to execute it. So unlike
        `claim_job`, which returns the row, this is a single conditional UPDATE:

            UPDATE jobs SET status='running' ... WHERE id=:id AND status='pending'

        One statement, so there is no window in which a worker could observe the
        row as claimable. Returns False if the job was not pending (already
        claimed by a worker, or finished).
        """
        from sqlalchemy import update

        from app.core.database import async_session_maker

        from ..job import Job as JobModel

        now = now or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(JobModel)
                    .where(JobModel.id == job_id, JobModel.status == "pending")
                    .values(
                        status="running",
                        locked_at=now,
                        started_at=now,
                        attempts=JobModel.attempts + 1,
                        updated_at=now,
                    )
                )
                return bool(result.rowcount)

    async def mark_done(self, job_id: int, result: Optional[dict] = None, llm_cost: Optional[float] = None) -> None:
        job = await self.get(id=job_id)
        now = datetime.now(timezone.utc)
        updates: dict = {
            "status": "done",
            "result": result,
            "error": None,
            "finished_at": now,
        }
        if llm_cost is not None:
            updates["llm_cost"] = float(llm_cost)
        await self.update_by_id(job_id, **updates)
        if job:
            await self._record_task_result(job, status="ok")

    async def mark_failed(self, job_id: int, error: str, allow_retry: bool = True) -> bool:
        """
        Record failure. Re-schedule with backoff if attempts to remain, else mark failed.
        Returns True if the job will be retried.

        `allow_retry=False` marks it terminal instead. An inline run ("выполнить
        сейчас") must not leave a retry behind: the caller asked for the work to
        happen now and get an answer, and a re-scheduled job would silently repeat
        the whole collection minutes later, on its own, with nobody watching.
        """
        from app.core.config import settings

        job = await self.get(id=job_id)
        if not job:
            return False
        if allow_retry and job.attempts < job.max_attempts:
            delay = settings.JOB_RETRY_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
            await self.update_by_id(
                job_id,
                status="pending",
                run_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
                error=error[:2000],
            )
            return True
        now = datetime.now(timezone.utc)
        await self.update_by_id(
            job_id,
            status="failed",
            error=error[:2000],
            finished_at=now,
        )
        await self._record_task_result(job, status="failed", error=error[:2000])
        return False

    async def reap_stale(self, timeout_minutes: int = 30) -> int:
        """Requeue jobs stuck in 'running' longer than timeout (crashed worker)."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        stale = await self.filter(status="running", locked_at__lt=cutoff)
        for job in stale:
            await self.update_by_id(job.id, status="pending", locked_at=None)
        return len(stale)

    async def cleanup_done(self, older_than_hours: int | None = None) -> int:
        """Delete successfully finished jobs older than `older_than_hours`.

        Keeps the queue page readable — a few hundred "done" rows makes it
        impossible to spot new failures. `running`/`pending`/`failed` rows
        survive because they need human attention or are in flight.
        """
        hours = older_than_hours or JOB_CLEANUP_AFTER_HOURS
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        done = await self.filter(status="done", finished_at__lt=cutoff)
        count = len(done)
        for job in done:
            await self.delete(id=job.id)
        return count
