"""Tenant-scoped, durable reminders for individual planner task occurrences."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker, with_db_session
from app.core.permissions import get_current_user, permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import NotificationDelivery, Tenant, TenantUser, User, WorkspaceRole
from app.models.planner import PlannerReminder, PlannerTask
from app.services.notifications.reminders import reminder_notifications
from app.services.planner.reminder_contracts import (
    ReminderCreate,
    ReminderOut,
    ReminderReschedule,
    ReminderSnooze,
)
from app.services.planner.tasks import MANAGERS, PlannerConflict, PlannerService, PlannerUnavailable
from app.types import ActionType

UTC = timezone.utc


def _month_before(value: datetime) -> datetime:
    month_index = value.year * 12 + value.month - 2
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def reminder_time(request: ReminderCreate, task: PlannerTask, *, now: datetime) -> datetime:
    """Resolve a preset in the task's timezone; persist the resulting instant in UTC."""
    if getattr(task, "recurrence_source", "planner") != "planner" and request.preset != "custom":
        raise PlannerConflict("calendar_relative_reminder_requires_provider")
    if request.preset == "custom":
        assert request.remind_at is not None
        return request.remind_at.astimezone(UTC)
    zone = ZoneInfo(task.timezone)
    if task.due_at is not None:
        deadline = task.due_at.astimezone(zone)
    elif task.due_date is not None:
        deadline = datetime.combine(task.due_date, time(9), tzinfo=zone)
    else:
        raise ValueError("planner_deadline_required")
    if request.preset == "day":
        selected = deadline - timedelta(days=1)
    elif request.preset == "week":
        selected = deadline - timedelta(weeks=1)
    else:
        selected = _month_before(deadline)
    return selected.astimezone(UTC)


def _projection(row: PlannerReminder, delivery_state=None, delivery_error_code=None) -> ReminderOut:
    excluded = {"delivery_state", "delivery_error_code"}
    values = {name: getattr(row, name) for name in ReminderOut.model_fields if name not in excluded}
    return ReminderOut.model_validate(
        {**values, "delivery_state": delivery_state, "delivery_error_code": delivery_error_code}
    )


class PlannerReminderService:
    def __init__(self):
        self.tasks = PlannerService()

    async def _identity(self, session, action=ActionType.UPDATE):
        identity = await self.tasks._actor(session, action)
        return identity, get_current_user()

    async def _owned(self, session, reminder_id: int, identity, *, lock=True) -> PlannerReminder:
        tenant_id, actor_id, _ = identity
        if type(reminder_id) is not int or reminder_id <= 0:
            raise PlannerUnavailable("planner_reminder_unavailable")
        query = select(PlannerReminder).where(
            PlannerReminder.tenant_id == tenant_id,
            PlannerReminder.id == reminder_id,
            PlannerReminder.recipient_user_id == actor_id,
        )
        row = await session.scalar(query)
        if row is None:
            raise PlannerUnavailable("planner_reminder_unavailable")
        await self.tasks._task(session, row.task_id, identity, lock=True)
        if lock:
            row = await session.scalar(query.with_for_update())
            if row is None:
                raise PlannerUnavailable("planner_reminder_unavailable")
        return row

    @with_db_session
    async def create(self, task_id: int, request: ReminderCreate, session):
        identity, actor = await self._identity(session)
        task = await self.tasks._task(session, task_id, identity, lock=True)
        if task.status in {"done", "cancelled"}:
            raise PlannerConflict("planner_task_not_remindable")
        remind_at = reminder_time(request, task, now=datetime.now(UTC))
        if remind_at.tzinfo is None:
            raise ValueError("reminder_timezone_required")
        existing = await session.scalar(
            select(PlannerReminder.id).where(
                PlannerReminder.tenant_id == identity[0],
                PlannerReminder.task_id == task.id,
                PlannerReminder.recipient_user_id == actor.id,
                PlannerReminder.channel == request.channel,
            )
        )
        if existing is not None:
            raise PlannerConflict("planner_reminder_exists")
        row = PlannerReminder(
            tenant_id=identity[0],
            task_id=task.id,
            recipient_user_id=actor.id,
            channel=request.channel,
            preset=request.preset,
            remind_at=remind_at,
            next_attempt_at=remind_at,
            state="pending",
            version=1,
            generation=1,
        )
        session.add(row)
        await session.flush()
        return _projection(row)

    @with_db_session
    async def list(self, task_id: int, session):
        identity, actor = await self._identity(session, ActionType.VIEW)
        await self.tasks._task(session, task_id, identity)
        rows = list(
            (
                await session.scalars(
                    select(PlannerReminder)
                    .where(
                        PlannerReminder.tenant_id == identity[0],
                        PlannerReminder.task_id == task_id,
                        PlannerReminder.recipient_user_id == actor.id,
                    )
                    .order_by(PlannerReminder.remind_at, PlannerReminder.id)
                )
            ).all()
        )
        delivery_states = {}
        for row in rows:
            if row.notification_id is not None and row.channel != "inbox":
                delivery_states[row.id] = (
                    await session.execute(
                        select(NotificationDelivery.state, NotificationDelivery.error_code).where(
                            NotificationDelivery.notification_id == row.notification_id,
                            NotificationDelivery.user_id == actor.id,
                            NotificationDelivery.channel == row.channel,
                        )
                    )
                ).one_or_none()
        return [
            _projection(row, *(delivery_states[row.id] if row.id in delivery_states else (None, None))) for row in rows
        ]

    async def _change(self, reminder_id, expected_version, session, *, require_open_task=False):
        identity, _ = await self._identity(session)
        row = await self._owned(session, reminder_id, identity)
        if type(expected_version) is not int or expected_version <= 0 or row.version != expected_version:
            raise PlannerConflict("planner_reminder_stale")
        if row.state == "cancelled":
            raise PlannerConflict("planner_reminder_cancelled")
        if require_open_task:
            task = await self.tasks._task(session, row.task_id, identity)
            if task.status in {"done", "cancelled"}:
                raise PlannerConflict("planner_task_not_remindable")
        return row

    @with_db_session
    async def snooze(self, reminder_id: int, request: ReminderSnooze, session):
        row = await self._change(reminder_id, request.expected_version, session, require_open_task=True)
        now = datetime.now(UTC)
        delta = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[request.preset]
        until = now + delta
        row.snoozed_until = until
        row.remind_at = until
        row.next_attempt_at = until
        row.state = "pending"
        row.sent_at = None
        row.notification_id = None
        row.last_error = None
        row.late = False
        row.generation += 1
        row.version += 1
        await session.flush()
        return _projection(row)

    @with_db_session
    async def reschedule(self, reminder_id: int, request: ReminderReschedule, session):
        row = await self._change(reminder_id, request.expected_version, session, require_open_task=True)
        remind_at = request.remind_at.astimezone(UTC)
        row.remind_at = remind_at
        row.snoozed_until = None
        row.next_attempt_at = remind_at
        row.state = "pending"
        row.sent_at = None
        row.notification_id = None
        row.last_error = None
        row.late = False
        row.preset = "custom"
        row.generation += 1
        row.version += 1
        await session.flush()
        return _projection(row)

    @with_db_session
    async def cancel(self, reminder_id: int, expected_version: int, session):
        row = await self._change(reminder_id, expected_version, session)
        row.state = "cancelled"
        row.sent_at = None
        row.snoozed_until = None
        row.next_attempt_at = datetime.now(UTC)
        row.version += 1
        await session.flush()
        return _projection(row)

    async def dispatch_due(self, *, limit: int = 20, now: datetime | None = None) -> dict[str, int]:
        """Publish due reminders once, one short transaction per candidate.

        Provider delivery is queued by the shared notification service and runs
        separately. This transaction only commits the inbox receipt and queue row.
        """
        tenant_id = current_tenant_id()
        if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
            raise PermissionError("planner_reminder_tenant_required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_reminder_limit")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("reminder_now_must_be_aware")
        current = current.astimezone(UTC)
        result = {"sent": 0, "deferred": 0, "skipped": 0}
        async with async_session_maker() as session:
            candidates = list(
                (
                    await session.scalars(
                        select(PlannerReminder.id)
                        .where(
                            PlannerReminder.tenant_id == tenant_id,
                            PlannerReminder.state.in_(("pending", "deferred")),
                            PlannerReminder.next_attempt_at <= current,
                        )
                        .order_by(PlannerReminder.next_attempt_at, PlannerReminder.id)
                        .limit(limit)
                    )
                ).all()
            )

        for reminder_id in candidates:
            status = await self._dispatch_one(tenant_id, reminder_id, current)
            result[status] += 1
        return result

    async def _dispatch_one(self, tenant_id: int, reminder_id: int, now: datetime) -> str:
        async with async_session_maker() as session:
            user_id = await session.scalar(
                select(PlannerReminder.recipient_user_id).where(
                    PlannerReminder.tenant_id == tenant_id, PlannerReminder.id == reminder_id
                )
            )
            if user_id is None:
                return "skipped"
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update(read=True))
            tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update(read=True))
            member = await session.scalar(
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.user_id == user_id,
                    TenantUser.channel == "web",
                    TenantUser.is_active.is_(True),
                    TenantUser.role_id.is_not(None),
                )
                .with_for_update(read=True)
            )
            role = (
                await session.scalar(
                    select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
                )
                if member is not None
                else None
            )
            task_id = await session.scalar(
                select(PlannerReminder.task_id).where(
                    PlannerReminder.id == reminder_id, PlannerReminder.tenant_id == tenant_id
                )
            )
            if task_id is None:
                return "skipped"
            task = await session.scalar(
                select(PlannerTask)
                .where(PlannerTask.tenant_id == tenant_id, PlannerTask.id == task_id)
                .with_for_update(read=True)
            )
            with permission_scope(user, member_grants=workspace_grants(role) if member is not None else frozenset()):
                reminder = await session.scalar(
                    select(PlannerReminder)
                    .where(
                        PlannerReminder.tenant_id == tenant_id,
                        PlannerReminder.id == reminder_id,
                        PlannerReminder.recipient_user_id == user_id,
                        PlannerReminder.state.in_(("pending", "deferred")),
                        PlannerReminder.next_attempt_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
                if reminder is None:
                    return "skipped"
                if (
                    user is None
                    or not user.is_active
                    or tenant is None
                    or not tenant.is_active
                    or member is None
                    or ("plannertask", ActionType.VIEW) not in workspace_grants(role)
                    or task is None
                    or (user_id not in {task.owner_user_id, task.assignee_id} and role not in MANAGERS)
                ):
                    reminder.state = "deferred"
                    reminder.next_attempt_at = now + timedelta(hours=1)
                    reminder.last_error = "recipient_unavailable"
                    reminder.version += 1
                    await session.commit()
                    return "deferred"
                if task.status in {"done", "cancelled"}:
                    reminder.state = "cancelled"
                    reminder.last_error = "task_closed"
                    reminder.version += 1
                    await session.commit()
                    return "skipped"

                schedule_task = task
                if task.recurrence_source == "calendar":
                    try:
                        if reminder.preset != "custom":
                            raise PlannerConflict("calendar_relative_reminder_requires_provider")
                        from types import SimpleNamespace

                        from app.services.calendar.service import authoritative_task_projection
                        from app.services.planner.tasks import _projection as task_projection

                        schedule = await authoritative_task_projection(
                            task, task_projection(task), session, (tenant_id, user.id, role)
                        )
                        schedule_task = SimpleNamespace(
                            tenant_id=tenant_id,
                            id=task.id,
                            due_at=schedule.due_at,
                            due_date=schedule.due_date,
                            timezone=schedule.timezone,
                        )
                    except (PlannerConflict, PlannerUnavailable, PermissionError):
                        reminder.state = "deferred"
                        reminder.next_attempt_at = now + timedelta(hours=1)
                        reminder.last_error = "calendar_source_schedule_unavailable"
                        reminder.version += 1
                        await session.commit()
                        return "deferred"

                deferred_to, reason = await reminder_notifications.allowed_at(
                    session, user.id, schedule_task.timezone, now
                )
                if deferred_to is not None:
                    reminder.state = "deferred"
                    reminder.next_attempt_at = deferred_to.astimezone(UTC)
                    reminder.last_error = reason
                    reminder.version += 1
                    await session.commit()
                    return "deferred"

                notification = await reminder_notifications.publish(
                    session=session, reminder=reminder, task=schedule_task, now=now
                )
                reminder.state = "delivered"
                reminder.sent_at = now
                reminder.notification_id = notification.id
                reminder.last_error = None
                reminder.late = self._is_late(schedule_task, now)
                reminder.version += 1
                await session.commit()
                return "sent"

    @staticmethod
    def _is_late(task: PlannerTask, now: datetime) -> bool:
        if task.due_at is not None:
            return now > task.due_at.astimezone(UTC)
        if task.due_date is not None:
            return now.astimezone(ZoneInfo(task.timezone)).date() > task.due_date
        return False


reminder_service = PlannerReminderService()
