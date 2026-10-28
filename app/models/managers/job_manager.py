from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager
from .job_lifecycle import JobLifecycleMixin

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt

    from ..scheduling.job import Job

# Remove jobs that finished more than this ago to keep the queue readable.
# Running/pending/failed rows survive — they need human attention or are in
# flight. Only "done" rows older than the threshold are pruned.
JOB_CLEANUP_AFTER_HOURS = 24


def web_attention_criteria(job_model):
    """Safe JSON text predicates for completed jobs that still need attention.

    `as_string()` uses JSON text extraction and avoids casts on legacy or
    malformed result values. Unexpected non-empty values are conservatively
    surfaced for a human rather than silently classified as clean success.
    """
    from sqlalchemy import func, or_

    def value(key: str):
        return func.trim(func.lower(func.coalesce(job_model.result[key].as_string(), "")))

    counters = ("error", "errors", "staged_errors", "rate_limited", "collection_errors", "collection_rate_limited")
    review_flags = ("needs_review", "review_required", "operator_review", "delivery_review")
    code_fields = ("status", "error", "diagnostic_code", "delivery_status")
    review_codes = ("partial_delivery_requires_review", "digest_run_in_progress_requires_review")
    return or_(
        value("status").in_(("partial", "partial_success", "failed", "error", "requires_review")),
        *(value(key).notin_(("", "0", "false", "null", "none")) for key in counters),
        *(value(key).in_(("true", "1", "yes")) for key in review_flags),
        *(value(key).contains(code) for key in code_fields for code in review_codes),
    )


def web_page_status_criteria(job_model, statuses: list[str], include_attention: bool | None):
    """Combine status filters with the active/history attention policy."""
    from sqlalchemy import and_, not_, or_

    status_criteria = job_model.status.in_(statuses)
    if include_attention is True and "done" not in statuses:
        return or_(status_criteria, and_(job_model.status == "done", web_attention_criteria(job_model)))
    if include_attention is False and "done" in statuses:
        return and_(status_criteria, not_(web_attention_criteria(job_model)))
    return status_criteria


class JobManager(JobLifecycleMixin, BaseManager["Job"]):
    """Manager for the Job queue: enqueue, atomic claim, retries."""

    def __init__(self):
        from ..scheduling.job import Job

        super().__init__(Job)

    @staticmethod
    def pending_analysis_retry_matches(
        job,
        *,
        tenant_id: int,
        analysis_id: int,
        source_id: int,
        staged_window: dict,
        initiated_by_user_id: int,
        now: datetime | None = None,
    ) -> bool:
        """Whether an old pending row can be explicitly resumed for one analysis."""
        from app.services.ai.analysis_retry import parse_staged_window

        now = now or datetime.now(timezone.utc)
        if (
            type(tenant_id) is not int
            or tenant_id <= 0
            or type(analysis_id) is not int
            or analysis_id <= 0
            or type(source_id) is not int
            or source_id <= 0
            or type(initiated_by_user_id) is not int
            or initiated_by_user_id <= 0
            or job is None
            or job.tenant_id != tenant_id
            or job.job_type != "analyze"
            or job.agent_task_id is not None
            or job.status != "pending"
            or not isinstance(job.run_at, datetime)
            or job.run_at.utcoffset() is None
            or job.run_at > now
            or job.attempts != 0
            or job.initiated_by_user_id not in (None, initiated_by_user_id)
        ):
            return False
        payload = job.payload if isinstance(job.payload, dict) else {}
        marker = payload.get("retry_analytics_id")
        if marker is not None and (type(marker) is not int or marker != analysis_id):
            return False
        source_ids = payload.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) != 1
            or type(source_ids[0]) is not int
            or source_ids[0] != source_id
        ):
            return False
        try:
            return parse_staged_window(payload.get("staged_window")) == staged_window
        except ValueError:
            return False

    async def adopt_pending_analysis_retry(
        self,
        *,
        job_id: int,
        tenant_id: int,
        analysis_id: int,
        source_id: int,
        staged_window: dict,
        initiated_by_user_id: int,
    ) -> bool:
        """Stamp and resume one exact legacy pending retry under admission/row locks."""
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.core.permissions import get_current_user
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

        from ..scheduling.job import Job as JobModel

        actor = get_current_user()
        trusted_actor_id = getattr(actor, "id", None)
        if (
            is_bypass()
            or type(current_tenant_id()) is not int
            or current_tenant_id() != tenant_id
            or type(trusted_actor_id) is not int
            or trusted_actor_id <= 0
            or trusted_actor_id != initiated_by_user_id
        ):
            raise TenantContextError("Pending retry does not match the authenticated workspace actor")
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                await self.lock_source_admission(session, tenant_id)
                job = (
                    await session.execute(
                        select(JobModel)
                        .where(JobModel.id == job_id, JobModel.tenant_id == tenant_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if not self.pending_analysis_retry_matches(
                    job,
                    tenant_id=tenant_id,
                    analysis_id=analysis_id,
                    source_id=source_id,
                    staged_window=staged_window,
                    initiated_by_user_id=initiated_by_user_id,
                    now=now,
                ):
                    return False
                from app.jobs.source_activity import job_source_ids

                active_jobs, task_sources, active_sources = await self._source_activity_context(session, tenant_id)
                if any(
                    current.id != job_id
                    and source_id
                    in job_source_ids(current.payload, current.agent_task_id, task_sources, active_sources)
                    for current in active_jobs
                ):
                    return False
                payload = dict(job.payload)
                payload["retry_analytics_id"] = analysis_id
                job.payload = payload
                if job.initiated_by_user_id is None:
                    job.initiated_by_user_id = initiated_by_user_id
                await session.flush()
        return True

    async def enqueue(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        *,
        agent_task_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
        initiated_by_user_id: Optional[int] = None,
        run_at: Optional[datetime] = None,
        max_attempts: Optional[int] = None,
        session: Optional["AsyncSession"] = None,
    ) -> "Job":
        """Create a pending job.

        `tenant_id` is honoured only in bypass mode (CLI/worker): it lets the
        caller stamp the owning tenant's id explicitly instead of falling back
        to the bootstrap owner workspace. In a normal tenant scope it is
        ignored (the scope wins).
        """
        from app.core.config import settings

        session_kwargs = {} if session is None else {"session": session}
        return await self.create(
            **session_kwargs,
            job_type=job_type,
            payload=payload or {},
            agent_task_id=agent_task_id,
            initiated_by_user_id=initiated_by_user_id,
            tenant_id=tenant_id,
            run_at=run_at or datetime.now(timezone.utc),
            max_attempts=max_attempts or settings.JOB_MAX_ATTEMPTS,
        )

    async def admit_source_job(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        *,
        agent_task_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
        initiated_by_user_id: Optional[int] = None,
        run_at: Optional[datetime] = None,
        max_attempts: Optional[int] = None,
        session: Optional["AsyncSession"] = None,
        supersede_unfinished: bool = False,
    ) -> "Job":
        """Admit an explicit collect/analyze run, or report an occupied source.

        Keep low-level enqueue available for new-input wakeups: coalescing a
        wakeup into an already-running batch could strand those new raw inputs.
        All task/manual run surfaces use this guarded boundary instead.

        `supersede_unfinished` is a manual re-run of this same task: its own
        pending/running jobs are ignored by the conflict check and removed
        before the new row is inserted. A different task's occupying run still
        raises `SourceJobBusy` — and nothing is deleted in that case.
        """
        from app.jobs.source_activity import SOURCE_JOB_TYPES, SourceJobBusy, source_conflict

        if job_type not in SOURCE_JOB_TYPES:
            raise ValueError("Source admission only accepts collect/analyze")
        if session is None:
            from app.core.database import async_session_maker

            async with async_session_maker() as owned:
                async with owned.begin():
                    return await self.admit_source_job(
                        job_type,
                        payload,
                        agent_task_id=agent_task_id,
                        tenant_id=tenant_id,
                        initiated_by_user_id=initiated_by_user_id,
                        run_at=run_at,
                        max_attempts=max_attempts,
                        session=owned,
                        supersede_unfinished=supersede_unfinished,
                    )
        from app.core.tenant_context import current_tenant_id, is_bypass

        if tenant_id is None and not is_bypass():
            tenant_id = current_tenant_id()
        stamped = await self._apply_tenant(session, {"tenant_id": tenant_id})
        tenant_id = stamped["tenant_id"]
        await self.lock_source_admission(session, tenant_id)
        jobs, task_sources, active = await self._source_activity_context(session, tenant_id, agent_task_id)
        existing = source_conflict(
            payload,
            agent_task_id,
            jobs,
            task_sources,
            active,
            ignore_task_id=agent_task_id if supersede_unfinished else None,
        )
        if existing is not None:
            raise SourceJobBusy(existing)
        if supersede_unfinished and agent_task_id is not None:
            await self.delete_unfinished_for_task(session, tenant_id=tenant_id, task_id=agent_task_id)
        return await self.enqueue(
            job_type,
            payload,
            agent_task_id=agent_task_id,
            tenant_id=tenant_id,
            initiated_by_user_id=initiated_by_user_id,
            run_at=run_at,
            max_attempts=max_attempts,
            session=session,
        )

    @staticmethod
    async def lock_source_admission(session: "AsyncSession", tenant_id: int) -> None:
        """Serialize short admission transactions, never handler execution.

        NO KEY UPDATE is exclusive against other admission locks but compatible
        with FK key-share locks held by ordinary tenant-owned inserts. Acquire
        it before task/staged-row locks; nested admission reuses the lock.
        """
        from sqlalchemy import select

        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.models.tenancy.tenant import Tenant

        if type(tenant_id) is not int or tenant_id <= 0 or (not is_bypass() and current_tenant_id() != tenant_id):
            raise TenantContextError("Source admission requires the owning workspace")
        connection = await session.connection()
        if await connection.get_isolation_level() != "READ COMMITTED":
            # A transaction-wide snapshot could miss the winner's inserted Job
            # after waiting for the gate. Refuse rather than claim deduplication.
            raise TenantContextError("Source admission requires READ COMMITTED isolation")
        locked = (
            await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
        ).scalar_one_or_none()
        if locked is None:
            raise TenantContextError("Source admission workspace is unavailable")

    @staticmethod
    async def _source_activity_context(session: "AsyncSession", tenant_id: int, task_id: int | None = None):
        """Batch-load job/task memberships using the worker's source precedence."""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.models.collection.source import Source
        from app.models.scheduling.agent_task import AgentTask
        from app.models.scheduling.job import Job

        jobs = list(
            (
                await session.execute(
                    select(Job)
                    .where(
                        Job.tenant_id == tenant_id,
                        Job.status.in_(["pending", "running"]),
                        Job.job_type.in_(["collect", "analyze"]),
                    )
                    .order_by(Job.id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        task_ids = {job.agent_task_id for job in jobs if job.agent_task_id is not None}
        if task_id is not None:
            task_ids.add(task_id)
        tasks = (
            list(
                (
                    await session.execute(
                        select(AgentTask)
                        .where(
                            AgentTask.tenant_id == tenant_id,
                            AgentTask.id.in_(task_ids),
                        )
                        .options(selectinload(AgentTask.sources))
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            if task_ids
            else []
        )
        task_sources = {
            task.id: {source.id for source in task.sources if source.tenant_id == tenant_id} for task in tasks
        }
        task_payloads = {task.id: task.payload if isinstance(task.payload, dict) else {} for task in tasks}
        for job in jobs:
            if job.job_type == "collect":
                payload = job.payload if isinstance(job.payload, dict) else {}
                task_payload = task_payloads.get(job.agent_task_id, {})
                job._analysis_inline = bool(task_payload.get("analyze_inline", payload.get("analyze_inline", False)))
        active = set(
            (
                await session.execute(
                    select(Source.id).where(
                        Source.tenant_id == tenant_id,
                        Source.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        return jobs, task_sources, active

    async def enqueue_replay(self, job_id: int, *, initiated_by_user_id: int) -> "Job":
        """Admit a safe replay as a new row under the authenticated tenant actor."""
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.core.permissions import get_current_user
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.replay_policy import replay_block_reason
        from app.jobs.source_activity import SOURCE_JOB_TYPES, source_conflict
        from app.models.collection.source import Source
        from app.models.scheduling.job import Job as JobModel

        tenant_id = current_tenant_id()
        actor = get_current_user()
        actor_id = getattr(actor, "id", None)
        if (
            is_bypass()
            or type(tenant_id) is not int
            or type(actor_id) is not int
            or actor_id <= 0
            or actor_id != initiated_by_user_id
        ):
            raise TenantContextError("Job replay requires the authenticated workspace actor")
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            async with session.begin():
                await self.lock_source_admission(session, tenant_id)
                parent = (
                    await session.execute(
                        select(JobModel).where(JobModel.id == job_id, JobModel.tenant_id == tenant_id).with_for_update()
                    )
                ).scalar_one_or_none()
                block_reason = replay_block_reason(parent)
                if block_reason is not None:
                    raise ValueError(block_reason)

                active_replay_id = (
                    await session.execute(
                        select(JobModel.id)
                        .where(
                            JobModel.tenant_id == tenant_id,
                            JobModel.status.in_(("pending", "running")),
                            JobModel.payload["retry_of_job_id"].as_string() == str(parent.id),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if active_replay_id is not None:
                    raise ValueError("Повтор этого запуска уже поставлен в очередь или выполняется.")

                payload = dict(parent.payload) if isinstance(parent.payload, dict) else {}
                if parent.job_type in SOURCE_JOB_TYPES:
                    snapshot = payload.get("_effective_snapshot")
                    task_payload = snapshot.get("task_payload") if isinstance(snapshot, dict) else None
                    source_ids = task_payload.get("source_ids") if isinstance(task_payload, dict) else None
                    if (
                        not isinstance(snapshot, dict)
                        or snapshot.get("version") != 1
                        or not isinstance(source_ids, list)
                        or not source_ids
                        or any(type(source_id) is not int or source_id <= 0 for source_id in source_ids)
                        or len(source_ids) != len(set(source_ids))
                    ):
                        raise ValueError(
                            "У старого запуска нет надёжного снимка источников. Создайте новый запуск из задачи."
                        )
                    if snapshot.get("action_type"):
                        raise ValueError(
                            "У задачи настроено внешнее действие. Проверьте результат и запустите её со страницы задачи."
                        )
                    existing_sources = set(
                        (
                            await session.execute(
                                select(Source.id).where(Source.tenant_id == tenant_id, Source.id.in_(source_ids))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if existing_sources != set(source_ids):
                        raise ValueError("Источник прошлого запуска больше недоступен в этом пространстве.")
                    payload["source_ids"] = list(source_ids)
                    jobs, task_sources, active = await self._source_activity_context(session, tenant_id)
                    if source_conflict(payload, None, jobs, task_sources, active) is not None:
                        raise ValueError("Для источника уже есть ожидающая или выполняющаяся задача.")
                elif parent.agent_task_id is not None:
                    raise ValueError(
                        "Для повтора задачи откройте её страницу: там будут использованы сохранённые настройки."
                    )

                payload.pop("agent_task_id", None)
                payload["retry_of_job_id"] = parent.id
                payload["retry_of_task_id"] = parent.agent_task_id
                if parent.job_type in SOURCE_JOB_TYPES:
                    # A detached replay reads the immutable snapshot, never the
                    # current reusable AgentTask configuration.
                    snapshot = dict(payload["_effective_snapshot"])
                    snapshot["task_payload"] = dict(snapshot["task_payload"])
                    payload["_effective_snapshot"] = snapshot
                    payload["force_refresh"] = False
                    payload["force_reanalyze"] = False
                job = await self.enqueue(
                    parent.job_type,
                    payload,
                    agent_task_id=None,
                    tenant_id=tenant_id,
                    initiated_by_user_id=actor_id,
                    run_at=now,
                    session=session,
                )
                await session.flush()
            return job

    async def source_activity(self, source_id: int) -> dict[str, bool]:
        """Read-only UI hint. Admission still rechecks under its transaction lock."""
        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.source_activity import job_source_ids

        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0 or is_bypass():
            raise TenantContextError("Source activity requires a concrete workspace")
        async with async_session_maker() as session:
            jobs, task_sources, active = await self._source_activity_context(session, tenant_id)
        result = {"collect": False, "analyze": False}
        for job in jobs:
            if source_id in job_source_ids(job.payload, job.agent_task_id, task_sources, active):
                result[job.job_type] = True
        return result

    async def source_progress_jobs(self, source_id: int, limit: int = 10) -> list:
        """Recent jobs relevant to this source, without retroactive history matching."""
        from sqlalchemy import String, and_, cast, func, or_, select
        from sqlalchemy.dialects.postgresql import JSONB

        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.jobs.source_activity import job_source_ids
        from app.models.scheduling.job import Job

        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0 or is_bypass():
            raise TenantContextError("Source progress requires a concrete workspace")
        async with async_session_maker() as session:
            jobs, task_sources, active = await self._source_activity_context(session, tenant_id)
            recorded_source = cast(Job.result, JSONB)
            history = list(
                (
                    await session.execute(
                        select(Job)
                        .where(
                            Job.tenant_id == tenant_id,
                            Job.job_type.in_(("collect", "analyze")),
                            Job.status.not_in(("pending", "running")),
                            or_(
                                and_(
                                    Job.status == "done",
                                    recorded_source.contains({"per_source": [{"source_id": source_id}]}),
                                ),
                                and_(
                                    recorded_source.contains({"progress": {"items": [{"source_id": source_id}]}}),
                                    recorded_source["progress"]["attempt"].astext == cast(Job.attempts, String),
                                ),
                            ),
                        )
                        .order_by(Job.created_at.desc())
                        .limit(max(1, min(limit, 20)))
                    )
                )
                .scalars()
                .all()
            )
        matched = []
        for job in jobs:
            if job.status in ("pending", "running"):
                relevant = source_id in job_source_ids(job.payload, job.agent_task_id, task_sources, active)
            if relevant:
                matched.append(job)
        active_ids = {job.id for job in matched}
        for job in history:
            if job.id not in active_ids:
                matched.append(job)
        return sorted(
            matched,
            key=lambda job: (
                job.status in ("pending", "running"),
                job.created_at or datetime.min.replace(tzinfo=timezone.utc),
                job.id,
            ),
            reverse=True,
        )[: max(1, min(limit, 20))]

    async def cost_today(self, now: Optional[datetime] = None) -> float:
        """Total USD spent on learning/reflect LLM calls today (UTC day) — the daily cap check."""
        now = now or datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self.filter(created_at__gte=day_start)
        return sum(float(row.llm_cost or 0.0) for row in rows)

    async def page_for_web(
        self,
        tenant_id: int,
        *,
        statuses: list[str] | None,
        page: int,
        page_size: int = 50,
        include_attention: bool | None = None,
    ):
        """Load one bounded Jobs page and matching total for the web UI.

        `True` adds completed rows with partial/error/review evidence;
        `False` excludes those rows from clean history; `None` applies only the
        requested statuses (used by the unfiltered all view).
        """
        from app.core.tenant_context import current_tenant_id, is_bypass

        if type(tenant_id) is not int or tenant_id <= 0 or type(page) is not int or page < 1:
            raise ValueError("Invalid Jobs page scope")
        if not is_bypass() and current_tenant_id() != tenant_id:
            raise RuntimeError("Jobs page cannot cross the active tenant scope")
        if page_size < 1 or page_size > 100:
            raise ValueError("Invalid Jobs page size")
        if include_attention is not None and type(include_attention) is not bool:
            raise ValueError("Invalid Jobs attention filter")
        query = self.filter(tenant_id=tenant_id)
        if statuses is not None:
            if not statuses or any(status not in {"pending", "running", "done", "failed"} for status in statuses):
                raise ValueError("Invalid Jobs status filter")
            from ..scheduling.job import Job as JobModel

            query = query.filter(web_page_status_criteria(JobModel, statuses, include_attention))
        total = await query.count()
        rows = (
            await query.order_by(self.model.created_at.desc(), self.model.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return rows, total

    async def overview_for_web(self, tenant_id: int, *, now: datetime | None = None):
        """Aggregate workspace queue counts and health without loading history."""
        from sqlalchemy import func, or_, select

        from app.core.database import async_session_maker
        from app.core.tenant_context import current_tenant_id, is_bypass
        from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
        from app.jobs.recovery_policy import OPERATOR_CANCELLED_ERROR, OUTCOME_UNCONFIRMED_PREFIX

        from ..scheduling.job import Job as JobModel

        if type(tenant_id) is not int or tenant_id <= 0:
            return (
                {"pending": 0, "running": 0, "done": 0, "failed": 0, "attention": 0},
                {
                    "overdue_pending": 0,
                    "oldest_overdue_minutes": None,
                    "stale_running": 0,
                    "unconfirmed_failed": 0,
                    "quarantined": 0,
                },
            )
        if not is_bypass() and current_tenant_id() != tenant_id:
            raise RuntimeError("Jobs overview cannot cross the active tenant scope")
        now = now or datetime.now(timezone.utc)
        overdue_cutoff = now - timedelta(minutes=15)
        stale_cutoff = now - timedelta(minutes=30)
        async with async_session_maker() as session:
            counts = (
                await session.execute(
                    select(JobModel.status, func.count())
                    .where(JobModel.tenant_id == tenant_id)
                    .group_by(JobModel.status)
                )
            ).all()
            overdue, oldest = (
                await session.execute(
                    select(func.count(), func.min(JobModel.run_at)).where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.status == "pending",
                        JobModel.run_at <= overdue_cutoff,
                    )
                )
            ).one()
            stale = (
                await session.execute(
                    select(func.count()).where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.status == "running",
                        JobModel.locked_at < stale_cutoff,
                    )
                )
            ).scalar_one()
            uncertain = (
                await session.execute(
                    select(func.count()).where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.status == "failed",
                        or_(
                            JobModel.error.startswith(OUTCOME_UNCONFIRMED_PREFIX),
                            JobModel.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX),
                            JobModel.error == OPERATOR_CANCELLED_ERROR,
                        ),
                    )
                )
            ).scalar_one()
            quarantined = (
                await session.execute(
                    select(func.count()).where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.status == "failed",
                        JobModel.error.startswith(OUTCOME_UNCONFIRMED_PREFIX),
                    )
                )
            ).scalar_one()
            attention = (
                await session.execute(
                    select(func.count()).where(
                        JobModel.tenant_id == tenant_id,
                        JobModel.status == "done",
                        web_attention_criteria(JobModel),
                    )
                )
            ).scalar_one()
        stats = {status: int(count) for status, count in counts}
        for status in ("pending", "running", "done", "failed"):
            stats.setdefault(status, 0)
        stats["attention"] = int(attention)
        oldest_minutes = int((now - oldest).total_seconds() // 60) if oldest is not None else None
        signals = {
            "overdue_pending": int(overdue),
            "oldest_overdue_minutes": oldest_minutes,
            "stale_running": int(stale),
            "unconfirmed_failed": int(uncertain),
            "quarantined": int(quarantined),
        }
        return stats, signals

    async def cleanup_done(self, older_than_hours: int | None = None) -> int:
        """Legacy done-only cleanup API; production maintenance uses lifecycle service."""
        hours = older_than_hours or JOB_CLEANUP_AFTER_HOURS
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return await self.filter(status="done", finished_at__lt=cutoff).delete()
