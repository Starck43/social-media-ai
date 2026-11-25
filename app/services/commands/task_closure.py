"""Confirmed closure and reopening of one canonical task with its schedule.

Closure records completion only after a preview of the exact task version and
an explicit, actor-bound confirmation. It disables future automatic triggers in
the same transaction, preserves every run receipt and reports an already
admitted run instead of cancelling it. Reopening restores open work and asks
explicitly whether a paused schedule should be re-armed.

The scheduler, manual admission and these commands lock the same rows in the
same order (workspace, member, task, trigger), so a close and a due occurrence
cannot both win: the scheduler skips a task that is being closed, and the
closure detects an occurrence admitted after its preview.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.database import with_db_session
from app.models.planner import Task, TaskRun, TaskTrigger
from app.models.planner.execution_plan import TaskPlanRevision
from app.services.execution_plan_contracts import ExecutionPlanError
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerConflict
from app.services.task_execution_plans import execution_plan_service
from app.services.task_run_admission import TaskRunAdmissionError, _digest
from app.services.task_run_triggers import task_trigger_service
from app.types import ActionType

CLOSURE_TTL_SECONDS = 600
OPERATIONS = {"close": "done", "reopen": "todo"}
CLOSED_STATUSES = frozenset({"done", "cancelled"})
TOKEN_FIELDS = 7


@dataclass(frozen=True)
class ClosureEffects:
    """Everything the confirmation is about to change, stated before it does."""

    operation: str
    scope: str
    task_id: int
    version: int
    status: str
    target_status: str
    series_id: int
    creates_occurrence: bool
    successor_task_id: int | None
    successor_due: str | None
    schedules: int
    enabled_schedules: int
    reactivates_schedule: bool
    admitted_run_id: int | None
    admitted_run_status: str | None
    retained_run_receipts: int
    next_due_at: datetime | None


@dataclass(frozen=True)
class ClosurePreview:
    effects: ClosureEffects
    confirmation: str
    expires_in: int = CLOSURE_TTL_SECONDS


@dataclass(frozen=True)
class ClosureOutcome:
    effects: ClosureEffects
    replayed: bool


@dataclass(frozen=True)
class _State:
    """Rows read under the confirmation lock; the effects and token derive from them."""

    trigger: TaskTrigger | None
    stored: TaskPlanRevision | None
    active_run: TaskRun | None
    retained_run_receipts: int
    following: tuple | None
    successor: Task | None
    creates_occurrence: bool
    reactivation_ready: bool
    fingerprint: str


def _serializer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="canonical-task-closure-v1")


def _operation(operation):
    if operation not in OPERATIONS:
        raise ValueError("invalid_closure_operation")
    return operation


def _choice(value):
    if type(value) is not bool:
        raise ValueError("invalid_schedule_choice")
    return value


async def _target(session, task_id, operation, *, confirm):
    identity = await task_service.action_actor(session, ActionType.UPDATE)
    task = await task_service._task(session, task_id, identity, lock=confirm)
    if task.recurrence_source != "planner":
        # Calendar-owned occurrences follow their source; the canonical command
        # must never close a row another writer still owns.
        raise PermissionError("task_closure_denied")
    if not confirm and operation == "close" and task.status in CLOSED_STATUSES:
        raise PlannerConflict("task_already_closed")
    if not confirm and operation == "reopen" and task.status not in CLOSED_STATUSES:
        raise PlannerConflict("task_already_open")
    return identity, task


async def _trigger(session, task, *, lock=False):
    query = select(TaskTrigger).where(TaskTrigger.tenant_id == task.tenant_id, TaskTrigger.task_id == task.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def _active_run(session, task):
    return await session.scalar(
        select(TaskRun)
        .where(
            TaskRun.tenant_id == task.tenant_id,
            TaskRun.task_id == task.id,
            TaskRun.status.in_(("pending", "running")),
        )
        .order_by(TaskRun.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )


def _next_cursor(task, now):
    """The occurrence a human recurrence would produce; `None` when it ends."""
    from app.services.planner.recurrence import occurrence_key

    template, following = task_service.following_occurrence(task, now)
    if following is None:
        return None
    due, index = following
    key = occurrence_key(template.recurrence, template.due_at or template.due_date, due)
    return due, index, key


async def _existing(session, task, *, key=None):
    """An occurrence already linked to this one, or already admitted for the cursor."""
    query = select(Task).where(Task.tenant_id == task.tenant_id, Task.deleted_at.is_(None))
    if key is not None:
        if task.series_id is None:
            return None
        query = query.where(Task.series_id == task.series_id, Task.occurrence_key == key)
    else:
        query = query.where(Task.previous_occurrence_id == task.id)
    return await session.scalar(query.order_by(Task.id).limit(1).execution_options(populate_existing=True))


def _reactivation_ready(identity, trigger, stored):
    """A paused schedule may be re-armed only under T04's own arming rules."""
    if trigger is None or trigger.enabled or stored is None:
        return False
    return stored.revision == trigger.revision and stored.scheduled_principal_id == identity[1]


async def _state(session, identity, task, operation, reactivate, now, *, lock):
    trigger = await _trigger(session, task, lock=lock)
    stored = await execution_plan_service._revision(session, task)
    active = await _active_run(session, task)
    retained = await session.scalar(
        select(func.count()).select_from(TaskRun).where(TaskRun.tenant_id == task.tenant_id, TaskRun.task_id == task.id)
    )
    # The successor this closure already produced, if any, outlives reopening.
    successor = await _existing(session, task)
    following = None
    creates = False
    if operation == "close" and task.status not in CLOSED_STATUSES and trigger is None:
        # A task under automation keeps its future in the schedule: closing it
        # never creates a silent successor occurrence.
        following = _next_cursor(task, now)
        known = await _existing(session, task, key=following[2]) if following else None
        successor = successor or known
        creates = following is not None and known is None
    ready = operation == "reopen" and reactivate and _reactivation_ready(identity, trigger, stored)
    fingerprint = _digest(
        operation,
        task.id,
        task.version,
        task.status,
        [trigger.id, trigger.revision, trigger.enabled, str(trigger.next_due_at)] if trigger else None,
        [stored.revision, stored.scheduled_principal_id] if stored else None,
        [active.id, active.status] if active else None,
        retained,
        following[0].isoformat() if following else None,
        successor.id if successor else None,
        creates,
        ready,
    )
    return _State(trigger, stored, active, retained, following, successor, creates, ready, fingerprint)


def _effects(task, operation, state, *, arms):
    return ClosureEffects(
        operation=operation,
        scope="occurrence",
        task_id=task.id,
        version=task.version,
        status=task.status,
        target_status=OPERATIONS[operation],
        series_id=task.series_id,
        creates_occurrence=state.creates_occurrence,
        successor_task_id=state.successor.id if state.successor else None,
        successor_due=state.following[0].isoformat() if state.following else None,
        schedules=int(state.trigger is not None),
        enabled_schedules=int(bool(state.trigger and state.trigger.enabled)),
        reactivates_schedule=bool(arms),
        admitted_run_id=state.active_run.id if state.active_run else None,
        admitted_run_status=state.active_run.status if state.active_run else None,
        retained_run_receipts=state.retained_run_receipts,
        next_due_at=state.trigger.next_due_at if state.trigger else None,
    )


def _hold(trigger):
    """Stop future automatic occurrences without deleting their schedule definition."""
    trigger.enabled = False
    trigger.next_due_at = None


async def _close(session, identity, task, state, now):
    if state.creates_occurrence and state.following is not None:
        await task_service.advance_occurrence(session, task, identity, now)
    if state.trigger is not None:
        _hold(state.trigger)
    task.status, task.completed_at, task.version = "done", now, task.version + 1
    await session.flush()


async def _arm_paused(session, identity, task, state, now):
    if state.trigger is not None and not state.trigger.enabled and state.stored is not None:
        try:
            await task_trigger_service._arm(session, identity, task, state.trigger, state.stored, now)
        except (PermissionError, ExecutionPlanError, TaskRunAdmissionError):
            # The whole reopening rolls back: a partly armed schedule would be
            # a promise the caller never confirmed.
            raise PlannerConflict("schedule_reactivation_unavailable") from None


async def _reopen(session, identity, task, state, reactivate, now):
    task.status, task.completed_at, task.version = "todo", None, task.version + 1
    if reactivate:
        await _arm_paused(session, identity, task, state, now)
    await session.flush()


def _binding(token, identity, task_id, operation, reactivate):
    """Token fields that must match the caller and the request, before any effect.

    Returns the task version the preview was prepared for.
    """
    if (
        not isinstance(token, list)
        or len(token) != TOKEN_FIELDS
        or any(type(field) is not int for field in token[:3])
        or type(token[4]) is not bool
        or type(token[5]) is not int
        or not isinstance(token[6], str)
    ):
        raise PlannerConflict("task_closure_preview_required")
    if (
        token[:2] != [identity[0], identity[1]]
        or token[2] != task_id
        or token[3] != operation
        or token[4] is not reactivate
    ):
        raise PlannerConflict("task_closure_preview_changed")
    return token[5], token[6]


class TaskClosureService:
    @with_db_session
    async def preview(
        self, task_id: int, *, operation: str, reactivate_schedule: bool = False, session=None
    ) -> ClosurePreview:
        operation, reactivate = _operation(operation), _choice(reactivate_schedule)
        identity, task = await _target(session, task_id, operation, confirm=False)
        now = datetime.now(timezone.utc)
        state = await _state(session, identity, task, operation, reactivate, now, lock=False)
        if reactivate and not state.reactivation_ready:
            raise PlannerConflict("schedule_reactivation_unavailable")
        token = _serializer().dumps(
            [
                identity[0],
                identity[1],
                task_id,
                operation,
                reactivate,
                task.version,
                state.fingerprint,
            ]
        )
        return ClosurePreview(_effects(task, operation, state, arms=state.reactivation_ready), token)

    @with_db_session
    async def confirm(
        self, task_id: int, *, operation: str, reactivate_schedule: bool = False, confirmation: str, session=None
    ) -> ClosureOutcome:
        operation, reactivate = _operation(operation), _choice(reactivate_schedule)
        try:
            token = _serializer().loads(confirmation, max_age=CLOSURE_TTL_SECONDS)
        except (BadData, TypeError, ValueError):
            raise PlannerConflict("task_closure_confirmation_required") from None
        now = datetime.now(timezone.utc)
        try:
            async with session.begin_nested():
                identity, task = await _target(session, task_id, operation, confirm=True)
                version, fingerprint = _binding(token, identity, task_id, operation, reactivate)
                closed = task.status in CLOSED_STATUSES
                replayed = bool(closed) if operation == "close" else not closed
                state = await _state(session, identity, task, operation, reactivate, now, lock=True)
                if replayed:
                    # Converge on the outcome that already landed: no second
                    # occurrence and no second trigger, with the remaining
                    # promise of the confirmed operation still applied.
                    if operation == "close":
                        if state.trigger is not None and state.trigger.enabled:
                            _hold(state.trigger)
                            await session.flush()
                    elif reactivate:
                        await _arm_paused(session, identity, task, state, now)
                elif task.version != version or state.fingerprint != fingerprint:
                    raise PlannerConflict("task_closure_preview_changed")
                elif operation == "close":
                    await _close(session, identity, task, state, now)
                else:
                    await _reopen(session, identity, task, state, reactivate, now)
                state = await _state(session, identity, task, operation, False, now, lock=False)
                await session.flush()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise PlannerConflict("task_closure_worker_busy") from None
            raise
        arms = bool(reactivate and state.trigger is not None and state.trigger.enabled)
        return ClosureOutcome(_effects(task, operation, state, arms=arms), replayed)


task_closure_service = TaskClosureService()
