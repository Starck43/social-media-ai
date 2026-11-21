"""Configure and consume cron occurrences in the canonical task scheduler."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import async_session_maker, with_db_session
from app.core.permissions import get_current_user, permission_scope
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models import Tenant, User
from app.models.planner import Task
from app.models.planner.task_run import TaskTrigger
from app.services.execution_plan_contracts import ExecutionPlanError
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerUnavailable
from app.services.task_execution_plans import _permits, execution_plan_service
from app.services.task_run_admission import TaskRunAdmissionError, task_run_service
from app.tasks.cron import next_run_at
from app.types import ActionType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskTriggerReceipt:
    task_id: int
    revision: int
    task_version: int
    trigger_id: int | None
    enabled: bool
    cron_expression: str | None
    timezone_name: str | None
    next_due_at: datetime | None


@dataclass(frozen=True)
class _DueTrigger:
    id: int
    task_id: int
    principal_id: int | None
    occurrence_at: datetime


def _next_occurrence(cron: str, zone: str, now: datetime) -> datetime:
    try:
        due = next_run_at(cron, zone, after=now)
    except (ValueError, KeyError, OverflowError):
        raise ExecutionPlanError("invalid_schedule") from None
    if due <= now:
        raise ExecutionPlanError("invalid_schedule")
    return due


def _deactivate(trigger: TaskTrigger) -> None:
    trigger.enabled = False
    trigger.next_due_at = None


class TaskTriggerService:
    @with_db_session
    async def configure(
        self,
        task_id: int,
        *,
        revision: int,
        expected_version: int,
        enabled: bool,
        session=None,
    ) -> TaskTriggerReceipt:
        """Arm the exact published schedule or turn it off without launching work."""
        if type(enabled) is not bool or type(revision) is not int or revision < 1:
            raise ExecutionPlanError("invalid_schedule")
        identity = await task_service._actor(session, ActionType.UPDATE, member_channel="web")
        if not _permits(identity, "plannertask", ActionType.UPDATE):
            raise PermissionError("task_update_denied")
        task = await task_service._task(session, task_id, identity, lock=True)
        task_service._editable(task, expected_version)
        stored = await execution_plan_service._revision(session, task)
        if stored is None or stored.revision != revision:
            raise ExecutionPlanError("plan_revision_unavailable")

        trigger = await session.scalar(
            select(TaskTrigger)
            .where(TaskTrigger.tenant_id == task.tenant_id, TaskTrigger.task_id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if enabled:
            if stored.scheduled_principal_id != identity[1]:
                raise PermissionError("scheduled_principal_unavailable")
            plan = (await execution_plan_service.load_for_admission(task.id, revision, session=session)).plan
            schedule = plan.trigger
            if schedule.type != "schedule" or not schedule.cron or not schedule.timezone:
                raise ExecutionPlanError("schedule_unavailable")

            if trigger is None or not trigger.enabled or trigger.revision != revision:
                due = _next_occurrence(schedule.cron, schedule.timezone, datetime.now(timezone.utc))
                if trigger is None:
                    trigger = TaskTrigger(tenant_id=task.tenant_id, task_id=task.id)
                    session.add(trigger)
                trigger.revision = revision
                trigger.principal_id = identity[1]
                trigger.cron_expression = schedule.cron
                trigger.timezone_name = schedule.timezone
                trigger.enabled = True
                trigger.next_due_at = due
                task.version += 1
        elif trigger is not None and trigger.enabled:
            _deactivate(trigger)
            task.version += 1
        await session.flush()
        return TaskTriggerReceipt(
            task.id,
            revision,
            task.version,
            trigger.id if trigger else None,
            bool(trigger and trigger.enabled),
            trigger.cron_expression if trigger and trigger.revision == revision else None,
            trigger.timezone_name if trigger and trigger.revision == revision else None,
            trigger.next_due_at if trigger else None,
        )

    async def tick(self, tenant_id: int, now: datetime) -> dict[str, int]:
        """Process due canonical triggers for one workspace without an ambient actor."""
        if (
            is_bypass()
            or type(tenant_id) is not int
            or tenant_id <= 0
            or current_tenant_id() != tenant_id
            or get_current_user() is not None
        ):
            raise TenantContextError("Canonical scheduling requires a normal workspace and no actor")
        if now.utcoffset() is None:
            raise ValueError("Canonical scheduling requires an aware timestamp")

        async with async_session_maker() as session:
            rows = await session.execute(
                select(TaskTrigger.id, TaskTrigger.task_id, TaskTrigger.principal_id, TaskTrigger.next_due_at)
                .where(
                    TaskTrigger.tenant_id == tenant_id,
                    TaskTrigger.enabled.is_(True),
                    TaskTrigger.next_due_at <= now,
                )
                .order_by(TaskTrigger.next_due_at, TaskTrigger.id)
            )
            due = [_DueTrigger(*row) for row in rows]

        stats = {"due": len(due), "enqueued": 0, "failed": 0}
        for observed in due:
            try:
                async with async_session_maker() as session:
                    async with session.begin():
                        outcome = await self._consume(session, tenant_id, observed, now)
                if outcome in {"enqueued", "failed"}:
                    stats[outcome] += 1
            except Exception as exc:
                # Keep unexpected failures retryable without exposing provider or plan details.
                logger.error(
                    "canonical_trigger_error tenant_id=%s trigger_id=%s error_type=%s",
                    tenant_id,
                    observed.id,
                    type(exc).__name__,
                )
                stats["failed"] += 1
        return stats

    async def _consume(self, session, tenant_id: int, observed: _DueTrigger, now: datetime) -> str | None:
        # Lock order matches task edits and admission: user, workspace, member,
        # role, task, trigger. The cursor is reread after locking the task.
        principal = None
        if observed.principal_id is not None:
            principal = await session.scalar(
                select(User)
                .where(User.id == observed.principal_id, User.is_active.is_(True))
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        workspace = await session.scalar(
            select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update(read=True)
        )
        if workspace is None:
            return None

        principal_authorized = False
        if principal is not None:
            with permission_scope(principal):
                try:
                    await task_service._actor(session, ActionType.VIEW, member_channel="web")
                except PermissionError:
                    pass
                else:
                    principal_authorized = True

        task = await session.scalar(
            select(Task)
            .where(Task.tenant_id == tenant_id, Task.id == observed.task_id)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None
        trigger = await session.scalar(
            select(TaskTrigger)
            .where(
                TaskTrigger.tenant_id == tenant_id,
                TaskTrigger.id == observed.id,
                TaskTrigger.task_id == task.id,
                TaskTrigger.enabled.is_(True),
                TaskTrigger.next_due_at == observed.occurrence_at,
                TaskTrigger.principal_id == observed.principal_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if trigger is None:
            return None

        stored = await execution_plan_service._revision(session, task)
        if (
            task.status in {"done", "cancelled"}
            or stored is None
            or stored.revision != trigger.revision
            or not principal_authorized
            or stored.scheduled_principal_id != trigger.principal_id
        ):
            _deactivate(trigger)
            logger.info("canonical_trigger_deactivated tenant_id=%s trigger_id=%s", tenant_id, trigger.id)
            return "failed"

        try:
            following = _next_occurrence(trigger.cron_expression, trigger.timezone_name, now)
        except ExecutionPlanError:
            _deactivate(trigger)
            logger.warning("canonical_trigger_invalid tenant_id=%s trigger_id=%s", tenant_id, trigger.id)
            return "failed"

        try:
            async with session.begin_nested():
                receipt = await task_run_service.admit_scheduled(
                    trigger, occurrence_at=observed.occurrence_at, now=now, session=session
                )
        except (TaskRunAdmissionError, ExecutionPlanError, PermissionError, PlannerUnavailable):
            # A denied occurrence is not replayed on every poll. A restored
            # grant can be used at the next configured occurrence.
            trigger.next_due_at = following
            logger.info("canonical_trigger_admission_denied tenant_id=%s trigger_id=%s", tenant_id, trigger.id)
            return "failed"

        # Advance once for both a job and a durable skip due to overlap. After
        # downtime, consume the persisted occurrence and resume future fires.
        trigger.next_due_at = following
        if receipt.job_id is not None and not receipt.replayed:
            logger.info(
                "canonical_trigger_enqueued tenant_id=%s trigger_id=%s run_id=%s",
                tenant_id,
                trigger.id,
                receipt.run_id,
            )
            return "enqueued"
        return None


task_trigger_service = TaskTriggerService()
