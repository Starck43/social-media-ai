"""Job claims, fenced lifecycle writes and recovery; existing transaction boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt

    from ..scheduling.job import Job


class JobLifecycleMixin:
    """Lifecycle behavior for JobManager; caller/owned sessions remain explicit."""

    @staticmethod
    async def _claim(where_clause, now: Optional[datetime] = None) -> Optional["Job"]:
        """Atomically flip one matching pending job to `running`.

        Shared by `claim_next` (worker loop) and `claim_job` (operator runs a
        specific job). Cross-tenant by nature: a worker must be able to pick up
        any workspace's job, and the CLI must be able to run any job at all.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.jobs.attempt_budget import available_attempt_budget

        from ..scheduling.job import Job as JobModel

        now = now or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                stmt = (
                    select(JobModel)
                    .where(
                        JobModel.status == "pending",
                        JobModel.run_at <= now,
                        available_attempt_budget(JobModel),
                        where_clause,
                    )
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
        from ..scheduling.job import Job as JobModel

        return await cls._claim(JobModel.id == job_id, now=now)


    async def renew_claim(self, claim: "JobClaim", now: Optional[datetime] = None) -> bool:
        """Acknowledge a heartbeat for exactly this running generation.

        Only locked_at advances; late heartbeats cannot move it backwards.
        updated_at and outcome/attempt evidence survive.
        False means no matching claim, not proof that the handler had no effect.
        DB/commit errors propagate. This primitive does not start a timer,
        change stale replay policy or fence a provider/intermediate write.
        """
        from sqlalchemy import func, update

        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.claim_outcomes import JobClaim

        from ..scheduling.job import Job as JobModel

        if not isinstance(claim, JobClaim):
            raise ValueError("A validated acquired claim is required")
        tenant_id = current_tenant_id()
        if not is_bypass() and (type(tenant_id) is not int or tenant_id != claim.tenant_id):
            raise TenantContextError("Heartbeat claim does not match the current tenant")
        if now is None:
            now = datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("Heartbeat requires an aware timestamp")
        async with async_session_maker() as session:
            async with session.begin():
                changed = (
                    await session.execute(
                        update(JobModel)
                        .where(
                            JobModel.id == claim.job_id,
                            JobModel.tenant_id == claim.tenant_id,
                            JobModel.job_type == claim.job_type,
                            JobModel.agent_task_id.is_not_distinct_from(claim.agent_task_id),
                            JobModel.status == "running",
                            JobModel.attempts == claim.attempts,
                            JobModel.started_at == claim.started_at,
                        )
                        .values(locked_at=func.greatest(JobModel.locked_at, now), updated_at=JobModel.updated_at)
                        .returning(JobModel.id)
                    )
                ).scalar_one_or_none()
        return changed == claim.job_id


    @staticmethod
    def _claim_predicates(model, claim: "JobClaim") -> tuple:
        return (
            model.id == claim.job_id,
            model.tenant_id == claim.tenant_id,
            model.job_type == claim.job_type,
            model.agent_task_id.is_not_distinct_from(claim.agent_task_id),
            model.attempts == claim.attempts,
            model.started_at == claim.started_at,
        )


    @staticmethod
    def _require_claim_tenant(claim: "JobClaim", message: str) -> None:
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.claim_outcomes import JobClaim

        if not isinstance(claim, JobClaim):
            raise ValueError("A validated acquired claim is required")
        if not is_bypass() and current_tenant_id() != claim.tenant_id:
            raise TenantContextError(message)


    async def record_progress(self, claim: "JobClaim", progress: dict) -> bool:
        """Store a bounded progress snapshot for exactly this running generation.

        Display data only: other result keys survive; locked_at, updated_at and
        outcome evidence are untouched. False means no matching running claim
        (cancelled, reaped or finished); DB/commit errors propagate.
        """
        from sqlalchemy import select, update

        from app.core.database import async_session_maker

        from ..scheduling.job import Job as JobModel

        self._require_claim_tenant(claim, "Progress claim does not match the current tenant")
        if not isinstance(progress, dict):
            raise ValueError("Progress snapshot must be a mapping")
        predicates = (*self._claim_predicates(JobModel, claim), JobModel.status == "running")
        async with async_session_maker() as session:
            async with session.begin():
                current = (
                    await session.execute(select(JobModel.id, JobModel.result).where(*predicates).with_for_update())
                ).one_or_none()
                if current is None:
                    return False
                result = dict(current.result) if isinstance(current.result, dict) else {}
                result["progress"] = {**progress, "attempt": claim.attempts}
                await session.execute(
                    update(JobModel)
                    .where(*predicates)
                    .values(result=result, updated_at=JobModel.updated_at)
                    .execution_options(synchronize_session=False)
                )
        return True


    async def record_pipeline_checkpoint(self, claim: "JobClaim", checkpoint: dict) -> bool:
        """Persist a bounded composed-task stage receipt under the live claim."""
        from sqlalchemy import select, update

        from app.core.database import async_session_maker

        from ..scheduling.job import Job as JobModel

        self._require_claim_tenant(claim, "Pipeline checkpoint does not match the current tenant")
        if claim.job_type != "analyze" or not isinstance(checkpoint, dict):
            raise ValueError("A composed analyze claim and checkpoint are required")
        predicates = (*self._claim_predicates(JobModel, claim), JobModel.status == "running")
        async with async_session_maker() as session:
            async with session.begin():
                current = (
                    await session.execute(select(JobModel.id, JobModel.result).where(*predicates).with_for_update())
                ).one_or_none()
                if current is None:
                    return False
                result = dict(current.result) if isinstance(current.result, dict) else {}
                result["pipeline"] = checkpoint
                await session.execute(
                    update(JobModel)
                    .where(*predicates)
                    .values(result=result, updated_at=JobModel.updated_at)
                    .execution_options(synchronize_session=False)
                )
        return True


    async def delete_unfinished_for_task(self, session: "AsyncSession", *, tenant_id: int, task_id: int) -> int:
        """Remove this task's queued/running rows before a manual re-run.

        A manual "run now" is a replacement, not a second copy of the same
        one-off work: the caller holds the workspace admission lock, so no
        other admission can insert a competing row while these are removed.
        Row locks settle a claim racing with the delete. A running handler
        loses its claim with the row and stops at the next heartbeat/progress
        write; terminal rows are history and are left alone.

        Deletion is deliberate: keeping the superseded rows as `failed` made
        the queue read as if several runs of the same task were still pending.
        """
        from sqlalchemy import select

        from ..scheduling.job import Job as JobModel

        rows = (
            (
                await session.execute(
                    select(JobModel)
                    .where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.agent_task_id == task_id,
                        JobModel.status.in_(("pending", "running")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await session.delete(row)
        return len(rows)


    async def delete_pending_for_task(self, session: "AsyncSession", *, tenant_id: int, task_id: int) -> int:
        """Remove this task's queued occurrences that never started.

        A scheduled task has one queued occurrence at a time; its payload was
        snapshotted from the schedule and definition that existed when it was
        admitted. When that definition changes, or the task is paused/deleted,
        the queued row must not run the old plan. Rows whose previous attempt
        left an unconfirmed external outcome are retained for review. Claimed
        (`running`) and terminal rows are history and are left alone.

        The caller owns the transaction: scheduling the new definition and
        dropping the stale occurrence commit or roll back together.
        """
        from sqlalchemy import select

        from app.jobs.recovery_policy import outcome_unconfirmed

        from ..scheduling.job import Job as JobModel

        rows = (
            (
                await session.execute(
                    select(JobModel)
                    .where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.agent_task_id == task_id,
                        JobModel.status == "pending",
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        removed = 0
        for row in rows:
            if outcome_unconfirmed(row.error):
                continue
            await session.delete(row)
            removed += 1
        return removed


    async def cancel_pending(self, job_id: int) -> bool:
        """Cancel a queued row that no worker has claimed yet (it never ran).

        Conditional on `pending` with zero attempts, so a concurrent claim wins
        and this returns False. Retry-scheduled rows are not touched here.
        """
        from sqlalchemy import update

        from app.core.database import async_session_maker
        from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR

        from ..scheduling.job import Job as JobModel

        scope = self._recovery_scope(JobModel)
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                changed = (
                    await session.execute(
                        update(JobModel)
                        .where(JobModel.id == job_id, JobModel.status == "pending", JobModel.attempts == 0, *scope)
                        .values(status="failed", error=CANCELLED_BEFORE_START_ERROR, finished_at=now)
                        .returning(JobModel.id)
                        .execution_options(synchronize_session=False)
                    )
                ).scalar_one_or_none()
        return changed == job_id


    async def settle_cancelled_discard(self, claim: "JobClaim") -> Optional[int]:
        """After a cancelled collect drained: remove rows it staged after the cancel.

        Runs once per cancellation (`settled`), only for the generation that an
        operator cancelled with "discard". Returns rows removed, or None.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.jobs.recovery_policy import OPERATOR_CANCELLED_ERROR

        from ..scheduling.job import Job as JobModel
        from .collected_item_manager import CollectedItemManager

        self._require_claim_tenant(claim, "Cancellation claim does not match the current tenant")
        if claim.job_type != "collect":
            return None
        async with async_session_maker() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(JobModel)
                        .where(
                            *self._claim_predicates(JobModel, claim),
                            JobModel.status == "failed",
                            JobModel.error == OPERATOR_CANCELLED_ERROR,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None or not isinstance(row.result, dict):
                    return None
                cancellation = row.result.get("cancellation")
                if (
                    not isinstance(cancellation, dict)
                    or cancellation.get("discard_collected") is not True
                    or cancellation.get("settled") is True
                ):
                    return None
                removed = await CollectedItemManager.delete_run(session, claim.job_id)
                earlier = cancellation.get("discarded")
                row.result = {
                    **row.result,
                    "cancellation": {
                        **cancellation,
                        "discarded": (earlier if type(earlier) is int and earlier >= 0 else 0) + removed,
                        "settled": True,
                    },
                }
        return removed


    async def cancel_running(self, claim: "JobClaim", *, discard_collected: Optional[bool] = None) -> bool:
        """Cancel only the running generation observed by an authorized operator.

        This fences the Job write, not external effects or Task summaries. False
        means the snapshot no longer matches; database/commit errors propagate.
        `discard_collected` (run-status modal) records the operator's choice in
        `result["cancellation"]`; for a collect run `True` also deletes the rows
        this run staged, in the same transaction. None keeps the legacy write.
        """
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.claim_outcomes import JobClaim

        from ..scheduling.job import Job as JobModel

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
                now = datetime.now(timezone.utc)
                row.status = "failed"
                row.error = "Cancelled by operator"
                row.finished_at = now
                if discard_collected is not None:
                    discard = discard_collected is True and claim.job_type == "collect"
                    cancellation: dict = {"requested_at": now.isoformat(), "discard_collected": discard}
                    if discard:
                        from .collected_item_manager import CollectedItemManager

                        cancellation["discarded"] = await CollectedItemManager.delete_run(session, claim.job_id)
                        cancellation["settled"] = False
                    previous = row.result if isinstance(row.result, dict) else {}
                    row.result = {**previous, "cancellation": cancellation}
            # Return success only after the cancellation commit is acknowledged.
        return True


    async def _record_task_result(self, job: "Job", status: str, error: Optional[str] = None) -> None:
        """Mirror the job outcome onto its AgentTask (if any) so the task's
        `last_status`/`last_error`/`last_run_at` reflect the real run result."""
        if not job.agent_task_id:
            return
        from app.models.managers.agent_task_manager import AgentTaskManager

        await AgentTaskManager().record_result(job.agent_task_id, status=status, error=error)


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
        claimed by a worker, finished, or its attempt budget is unavailable).
        """
        from sqlalchemy import update

        from app.core.database import async_session_maker
        from app.jobs.attempt_budget import available_attempt_budget

        from ..scheduling.job import Job as JobModel

        now = now or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(JobModel)
                    .where(
                        JobModel.id == job_id,
                        JobModel.status == "pending",
                        available_attempt_budget(JobModel),
                    )
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

        from ..scheduling.agent_task import AgentTask

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
        from app.models.managers.agent_task_manager import AgentTaskManager

        if status == "ok" and AgentTaskManager.wants_deactivate_after_success(task.payload):
            # A trial task asked to stop after its first success. Same
            # transaction as the outcome: the task never reads "ok" while
            # still scheduled to fire again.
            task.is_active = False
            task.next_run_at = None


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

        from ..scheduling.job import Job as JobModel

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
            await self._stop_trial_task(job.agent_task_id)


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


    async def _stop_trial_task(self, task_id: Optional[int]) -> None:
        """Close a trial task after its success in the legacy ID-only path.

        The claimed path does this in `_write_task_outcome`'s transaction; this
        covers the compatibility entry points that write separately.
        """
        if not task_id:
            return
        from app.models.managers.agent_task_manager import AgentTaskManager
        from app.models.scheduling.agent_task import AgentTask

        task = await AgentTask.objects.get(id=task_id)
        if task is None or not AgentTaskManager.wants_deactivate_after_success(task.payload):
            return
        await AgentTask.objects.update_by_id(task_id, is_active=False, next_run_at=None)


    async def reap_stale(self, timeout_minutes: int = 30) -> int:
        """Stop unavailable budgets and quarantine stale owned executions.

        Both writes retain eligibility/budget/tenant predicates and evidence.
        A stale lease does not prove no external effect: never requeue it.
        Return the actual number of below-budget stale rows quarantined.
        Budget-stop count remains separate; running evidence is not cleared.
        Each stopped row's owning Task summary is written in the SAME transaction
        unless the Task already records a later trigger or outcome.
        """
        from sqlalchemy import and_, func, or_, update

        from app.core.database import async_session_maker
        from app.jobs.attempt_budget import (
            ATTEMPT_BUDGET_STOP_PREFIX,
            available_attempt_budget,
            unavailable_attempt_budget,
        )
        from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX

        from ..scheduling.job import Job as JobModel

        scope = self._recovery_scope(JobModel)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=timeout_minutes)
        stop_eligible = or_(
            and_(JobModel.status == "running", JobModel.locked_at < cutoff),
            and_(JobModel.status == "pending", JobModel.run_at <= now),
        )
        stopped_evidence = (
            JobModel.tenant_id,
            JobModel.agent_task_id,
            func.coalesce(JobModel.started_at, JobModel.created_at),
            JobModel.error,
        )
        async with async_session_maker() as session:
            async with session.begin():
                budget_stopped = (
                    await session.execute(
                        update(JobModel)
                        .where(stop_eligible, unavailable_attempt_budget(JobModel), *scope)
                        .values(
                            status="failed",
                            finished_at=now,
                            error=func.concat(ATTEMPT_BUDGET_STOP_PREFIX, func.coalesce(JobModel.error, "")),
                        )
                        .returning(*stopped_evidence)
                        .execution_options(synchronize_session=False)
                    )
                ).all()
                quarantined = (
                    await session.execute(
                        update(JobModel)
                        .where(
                            available_attempt_budget(JobModel),
                            JobModel.status == "running",
                            JobModel.locked_at < cutoff,
                            *scope,
                        )
                        .values(
                            status="failed",
                            finished_at=now,
                            error=func.concat(OUTCOME_UNCONFIRMED_PREFIX, func.coalesce(JobModel.error, "")),
                        )
                        .returning(*stopped_evidence)
                        .execution_options(synchronize_session=False)
                    )
                ).all()
                for tenant_id, task_id, attempt_started_at, error in (*budget_stopped, *quarantined):
                    await self._write_stopped_task_outcome(
                        session, tenant_id, task_id, attempt_started_at=attempt_started_at, error=error, now=now
                    )
            # The count is returned only after the Job and Task writes commit together.
        return len(quarantined)


    @staticmethod
    def _recovery_scope(model) -> list:
        """Tenant predicate for recovery writes; explicit bypass is cross-workspace.

        A missing or malformed tenant fails closed before any session is opened.
        """
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

        if is_bypass():
            return []
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise TenantContextError("Stale recovery requires a tenant scope or an explicit bypass")
        return [model.tenant_id == tenant_id]


    @staticmethod
    async def _write_stopped_task_outcome(
        session: "AsyncSession",
        tenant_id: int,
        task_id: Optional[int],
        *,
        attempt_started_at: datetime,
        error: Optional[str],
        now: datetime,
    ) -> None:
        """Project one stopped attempt onto its owned Task in the caller's transaction.

        A Task whose last trigger or outcome is later than this attempt's start
        keeps that newer evidence: an old quarantine never hides a newer run.
        A missing or newer Task is not an error; the Job row stays authoritative.
        """
        if task_id is None:
            return
        from sqlalchemy import or_, update

        from ..scheduling.agent_task import AgentTask

        await session.execute(
            update(AgentTask)
            .where(
                AgentTask.id == task_id,
                AgentTask.tenant_id == tenant_id,
                or_(AgentTask.last_run_at.is_(None), AgentTask.last_run_at <= attempt_started_at),
            )
            .values(last_status="failed", last_error=error, last_run_at=now)
            .execution_options(synchronize_session=False)
        )

