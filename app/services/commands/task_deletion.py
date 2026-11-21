"""Version-bound deletion of local tasks while retaining execution receipts."""

from dataclasses import dataclass
from datetime import datetime, timezone

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import with_db_session
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR
from app.models import Job
from app.models.planner import Task, TaskRun, TaskTrigger
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerConflict
from app.services.task_run_admission import _digest
from app.types import ActionType

DELETE_TTL_SECONDS = 600


@dataclass(frozen=True)
class DeleteEffects:
    scope: str
    occurrences: int
    enabled_schedules: int
    queued_jobs: int
    running_jobs: int
    retained_run_receipts: int
    child_links_hidden: int
    next_occurrence: str | None


@dataclass(frozen=True)
class DeletePreview:
    effects: DeleteEffects
    confirmation: str
    expires_in: int = DELETE_TTL_SECONDS


@dataclass(frozen=True)
class DeleteOutcome:
    effects: DeleteEffects
    in_flight_uncertain: int


def _serializer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="canonical-task-delete-v1")


def _query(model, tenant_id, ids):
    return (
        select(model)
        .where(model.tenant_id == tenant_id, model.task_id.in_(ids))
        .order_by(model.id)
        .execution_options(populate_existing=True)
    )


async def _targets(session, task_id, scope, *, confirm=False):
    if scope not in {"occurrence", "series"}:
        raise ValueError("invalid_delete_scope")
    identity = await task_service._actor(session, ActionType.DELETE if confirm else ActionType.VIEW)
    if ("plannertask", ActionType.DELETE) not in workspace_grants(identity[2]):
        raise PermissionError("task_delete_denied")
    task = await task_service._task(session, task_id, identity, lock=confirm)
    rows = [task]
    if scope == "series" and task.series_id is not None:
        query = (
            select(Task)
            .where(Task.tenant_id == identity[0], Task.series_id == task.series_id, Task.deleted_at.is_(None))
            .order_by(Task.id)
            .execution_options(populate_existing=True)
        )
        rows = list(await session.scalars(query.with_for_update() if confirm else query))
    if any(row.recurrence_source != "planner" or not task_service._can_share(row, identity) for row in rows):
        raise PermissionError("task_delete_denied")
    return identity, task, rows


async def _snapshot(session, identity, task, rows, scope, now, *, lock=False):
    ids = [row.id for row in rows]
    tenant = task.tenant_id
    trigger_query = _query(TaskTrigger, tenant, ids)
    run_query = _query(TaskRun, tenant, ids)
    active_ids = run_query.with_only_columns(TaskRun.id).where(TaskRun.status.in_(("pending", "running")))
    job_query = (
        select(Job)
        .where(Job.tenant_id == tenant, Job.task_run_id.in_(active_ids.order_by(None)))
        .order_by(Job.id)
        .execution_options(populate_existing=True)
    )
    # Workers already hold Job -> Run before checking workspace authorization.
    # Refuse a busy snapshot instead of waiting while holding the workspace lock.
    jobs = list(await session.scalars(job_query.with_for_update(nowait=True) if lock else job_query))
    runs = list(
        await session.scalars(
            run_query.where(TaskRun.status.in_(("pending", "running"))).with_for_update(nowait=True)
            if lock
            else run_query.where(TaskRun.status.in_(("pending", "running")))
        )
    )
    triggers = list(await session.scalars(trigger_query.with_for_update() if lock else trigger_query))
    retained_run_receipts = await session.scalar(
        select(func.count()).select_from(TaskRun).where(TaskRun.tenant_id == tenant, TaskRun.task_id.in_(ids))
    )
    child_query = select(Task.id, Task.version).where(
        Task.tenant_id == tenant,
        Task.parent_id.in_(ids),
        Task.id.not_in(ids),
        task_service.visibility(identity[1], identity[2]),
    )
    children = (await session.execute(child_query.order_by(Task.id))).all()
    following = None
    if scope == "occurrence" and task.status in {"todo", "in_progress"}:
        _, following = task_service.following_occurrence(task, now)
    effects = DeleteEffects(
        scope=scope,
        occurrences=len(rows),
        enabled_schedules=sum(trigger.enabled for trigger in triggers),
        queued_jobs=sum(job.status == "pending" for job in jobs),
        running_jobs=sum(job.status == "running" for job in jobs),
        retained_run_receipts=retained_run_receipts,
        child_links_hidden=len(children),
        next_occurrence=following[0].isoformat() if following else None,
    )
    fingerprint = _digest(
        [(row.id, row.version) for row in rows],
        [(trigger.id, trigger.revision, trigger.enabled, str(trigger.next_due_at)) for trigger in triggers],
        [(run.id, run.status, run.result) for run in runs],
        [(job.id, job.status, job.attempts) for job in jobs],
        retained_run_receipts,
        [tuple(child) for child in children],
        effects.next_occurrence,
    )
    return effects, fingerprint, jobs, runs, triggers


def _stop_runs(jobs, runs, now):
    jobs_by_id = {job.id: job for job in jobs}
    uncertain = 0
    for run in runs:
        job = jobs_by_id.get(run.job_id)
        running = run.status == "running" or (job is not None and job.status == "running")
        uncertain += running
        run.status = "unknown" if running else "cancelled"
        run.result = {**(run.result or {}), "code": "task_delete_in_flight" if running else "task_deleted"}
        run.checkpoint_encrypted = None
        if job is not None and job.status in {"pending", "running"}:
            job.error = OPERATOR_CANCELLED_ERROR if running else CANCELLED_BEFORE_START_ERROR
            job.status = "failed"
            job.finished_at = now
    return uncertain


class TaskDeletionService:
    @with_db_session
    async def preview(self, task_id: int, *, scope: str, session=None) -> DeletePreview:
        identity, task, rows = await _targets(session, task_id, scope)
        effects, fingerprint, *_ = await _snapshot(session, identity, task, rows, scope, datetime.now(timezone.utc))
        token = _serializer().dumps([identity[0], identity[1], task_id, scope, fingerprint])
        return DeletePreview(effects, token)

    @with_db_session
    async def confirm(self, task_id: int, *, scope: str, confirmation: str, session=None) -> DeleteOutcome:
        try:
            token = _serializer().loads(confirmation, max_age=DELETE_TTL_SECONDS)
        except (BadData, TypeError, ValueError):
            raise PlannerConflict("task_delete_confirmation_required") from None
        try:
            async with session.begin_nested():
                identity, task, rows = await _targets(session, task_id, scope, confirm=True)
                now = datetime.now(timezone.utc)
                effects, fingerprint, jobs, runs, triggers = await _snapshot(
                    session, identity, task, rows, scope, now, lock=True
                )
                if token != [identity[0], identity[1], task_id, scope, fingerprint]:
                    raise PlannerConflict("task_delete_preview_changed")
                if effects.next_occurrence is not None:
                    await task_service.advance_occurrence(session, task, identity, now)
                uncertain = _stop_runs(jobs, runs, now)
                for trigger in triggers:
                    trigger.enabled = False
                    trigger.next_due_at = None
                for row in rows:
                    row.deleted_at = now
                    row.version += 1
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise PlannerConflict("task_delete_worker_busy") from None
            raise
        return DeleteOutcome(effects, uncertain)


task_deletion_service = TaskDeletionService()
