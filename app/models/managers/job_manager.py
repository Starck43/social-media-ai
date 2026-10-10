from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt

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

    async def cancel_running(self, claim: "JobClaim") -> bool:
        """Cancel only the running generation observed by an authorized operator.

        This fences the Job write, not external effects or Task summaries. False
        means the snapshot no longer matches; database/commit errors propagate.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.claim_outcomes import JobClaim

        from ..job import Job as JobModel

        if not isinstance(claim, JobClaim):
            raise ValueError("A validated running snapshot is required")
        if not is_bypass() and current_tenant_id() != claim.tenant_id:
            raise TenantContextError("Cancellation snapshot does not match the current tenant")
        async with async_session_maker() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(JobModel)
                        .where(
                            JobModel.id == claim.job_id,
                            JobModel.tenant_id == claim.tenant_id,
                            JobModel.job_type == claim.job_type,
                            JobModel.agent_task_id.is_not_distinct_from(claim.agent_task_id),
                            JobModel.status == "running",
                            JobModel.attempts == claim.attempts,
                            JobModel.started_at == claim.started_at,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return False
                row.status = "failed"
                row.error = "Cancelled by operator"
                row.finished_at = datetime.now(timezone.utc)
            # Return success only after the cancellation commit is acknowledged.
        return True

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

    @staticmethod
    async def _write_task_outcome(
        session: "AsyncSession",
        claim: "JobClaim",
        *,
        status: str,
        error: Optional[str],
        completed_at: datetime,
    ) -> None:
        """Write only the claim's owned Task using the caller-owned transaction.

        No commit, new session or generic permission grant occurs here. A
        missing target cannot be silently accepted as a successful projection.
        """
        from sqlalchemy import select

        from ..agent_task import AgentTask

        task = (
            await session.execute(
                select(AgentTask)
                .where(AgentTask.id == claim.agent_task_id, AgentTask.tenant_id == claim.tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            raise RuntimeError("Job task outcome target unavailable; completion not committed.")
        task.last_run_at = completed_at
        task.last_status = status
        task.last_error = error

    async def _finalize_claim(
        self,
        claim: "JobClaim",
        *,
        result: Optional[dict] = None,
        llm_cost: Optional[float] = None,
        error: Optional[str] = None,
        allow_retry: bool = True,
    ) -> "JobOutcomeReceipt":
        """Commit one claimed Job and its terminal Task summary in one transaction.

        This is not latest-run ordering, an outbox or safe external-effect replay.
        DB exceptions propagate: no lost-claim or rollback inference is made.
        """
        from copy import deepcopy

        from sqlalchemy import select

        from app.core.config import settings
        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt, OutcomeAck

        from ..job import Job as JobModel

        if not isinstance(claim, JobClaim):
            raise ValueError("A validated acquired claim is required")
        if not is_bypass() and current_tenant_id() != claim.tenant_id:
            raise TenantContextError("Outcome claim does not match the current tenant")
        async with async_session_maker() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(JobModel)
                        .where(
                            JobModel.id == claim.job_id,
                            JobModel.tenant_id == claim.tenant_id,
                            JobModel.job_type == claim.job_type,
                            JobModel.agent_task_id.is_not_distinct_from(claim.agent_task_id),
                            JobModel.status == "running",
                            JobModel.attempts == claim.attempts,
                            JobModel.started_at == claim.started_at,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return JobOutcomeReceipt(claim, OutcomeAck.CLAIM_LOST, error="Job claim no longer owned")
                now = datetime.now(timezone.utc)
                if error is None:
                    row.status = "done"
                    row.result = result
                    row.error = None
                    row.finished_at = now
                    ack = OutcomeAck.DONE
                else:
                    row.error = error[:2000]
                    if result is not None:
                        row.result = result
                    if allow_retry and claim.attempts < row.max_attempts:
                        row.status = "pending"
                        row.run_at = now + timedelta(
                            seconds=settings.JOB_RETRY_BACKOFF_SECONDS * (2 ** (claim.attempts - 1))
                        )
                        ack = OutcomeAck.RETRY
                    else:
                        row.status = "failed"
                        row.finished_at = now
                        ack = OutcomeAck.FAILED
                if llm_cost is not None:
                    row.llm_cost = float(llm_cost)
                if ack != OutcomeAck.RETRY and claim.agent_task_id is not None:
                    await self._write_task_outcome(
                        session,
                        claim,
                        status="ok" if ack == OutcomeAck.DONE else "failed",
                        error=row.error,
                        completed_at=now,
                    )
                receipt = JobOutcomeReceipt(claim, ack, result=deepcopy(row.result), error=row.error)
            # A receipt is returned only after BOTH rows' commit is acknowledged.
        return receipt

    async def mark_done(
        self,
        job_id: int,
        result: Optional[dict] = None,
        llm_cost: Optional[float] = None,
        *,
        claim: Optional["JobClaim"] = None,
    ) -> Optional["JobOutcomeReceipt"]:
        """Ordinary workers supply claim; the ID-only form is a legacy API."""
        if claim is not None:
            if job_id != claim.job_id:
                raise ValueError("Job ID does not match the acquired claim")
            return await self._finalize_claim(claim, result=result, llm_cost=llm_cost)
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

    async def mark_failed(
        self,
        job_id: int,
        error: str,
        allow_retry: bool = True,
        *,
        result: Optional[dict] = None,
        llm_cost: Optional[float] = None,
        claim: Optional["JobClaim"] = None,
    ) -> "bool | JobOutcomeReceipt":
        """
        Record failure. Re-schedule with backoff if attempts to remain, else mark failed.
        With a claim, returns a committed/lost receipt; the legacy ID-only form returns
        True if the job will be retried.

        `allow_retry=False` marks it terminal instead. An inline run ("выполнить
        сейчас") must not leave a retry behind: the caller asked for the work to
        happen now and get an answer, and a re-scheduled job would silently repeat
        the whole collection minutes later, on its own, with nobody watching.
        Optional result/cost are stored together with the failure update; they
        do not provide a billing ledger or atomic task/job transaction.
        """
        if claim is not None:
            if job_id != claim.job_id:
                raise ValueError("Job ID does not match the acquired claim")
            return await self._finalize_claim(
                claim,
                error=error,
                allow_retry=allow_retry,
                result=result,
                llm_cost=llm_cost,
            )
        from app.core.config import settings

        job = await self.get(id=job_id)
        if not job:
            return False
        metadata: dict = {}
        if result is not None:
            metadata["result"] = result
        if llm_cost is not None:
            metadata["llm_cost"] = float(llm_cost)
        if allow_retry and job.attempts < job.max_attempts:
            delay = settings.JOB_RETRY_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
            await self.update_by_id(
                job_id,
                status="pending",
                run_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
                error=error[:2000],
                **metadata,
            )
            return True
        now = datetime.now(timezone.utc)
        await self.update_by_id(
            job_id,
            status="failed",
            error=error[:2000],
            finished_at=now,
            **metadata,
        )
        await self._record_task_result(job, status="failed", error=error[:2000])
        return False

    async def reap_stale(self, timeout_minutes: int = 30) -> int:
        """Atomically requeue rows whose running lease is still stale.

        Keep status and lease age in the UPDATE predicate: a previously read
        snapshot must not overwrite a completion or a refreshed heartbeat.
        The queryset retains the caller's tenant guard (or explicit bypass).
        This preserves the existing timeout/replay policy; it does not prove
        that an ordinary long-running handler has stopped its side effects.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        return await self.filter(status="running", locked_at__lt=cutoff).update(status="pending", locked_at=None)

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
