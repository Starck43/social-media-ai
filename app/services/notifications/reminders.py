"""Planner producer adapter and durable external notification delivery.

The inbox is committed with the producer. External dispatch commits its attempt
before HTTP: a crash leaves an unknown outcome, never an automatic duplicate.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationReceipt,
    PersonalConnectorAuthorization,
    Tenant,
    TenantChannel,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.communications.followups import adjust_for_quiet_hours
from app.services.notifications.messenger import messenger_service
from app.services.notifications.preferences import DEFAULTS, validate
from app.types import ActionType, NotificationType

UTC = timezone.utc


def _scope():
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise PermissionError("notification_workspace_required")
    return tenant_id


def _now(value):
    value = value or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_time_required")
    return value.astimezone(UTC)


class ReminderNotifications:
    async def destination(self, event, user_id):
        from app.models.planner import PlannerReminder
        from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service

        async with async_session_maker() as session:
            task_id = await session.scalar(
                select(PlannerReminder.task_id).where(
                    PlannerReminder.tenant_id == _scope(),
                    PlannerReminder.id == event.related_entity_id,
                    PlannerReminder.recipient_user_id == user_id,
                )
            )
        if task_id is None:
            return None
        try:
            await planner_service.get(task_id)
        except (PlannerConflict, PlannerUnavailable, PermissionError):
            return None
        return f"/app/planner/{task_id}/edit#reminders"

    async def allowed_at(self, session, user_id, timezone, now):
        now = _now(now)
        row = await session.get(NotificationPreference, user_id)
        prefs = DEFAULTS if row is None else validate({key: getattr(row, key) for key in DEFAULTS})
        if prefs is None:
            return now + timedelta(hours=1), "invalid_preferences"
        if not prefs["tasks"]:
            return now + timedelta(hours=1), "tasks_disabled"
        if not prefs["quiet"]:
            return None, None
        local = now.astimezone(ZoneInfo(timezone))
        start_hour, start_minute = map(int, prefs["start"].split(":"))
        end_hour, end_minute = map(int, prefs["end"].split(":"))
        allowed = adjust_for_quiet_hours(local, start_hour, end_hour, start_minute=start_minute, end_minute=end_minute)
        if allowed == local:
            return None, None
        # A nonexistent local end time round-trips forward across the DST gap.
        # An ambiguous end uses the current fold; either occurrence is outside
        # the configured wall-clock interval. Never move an instant backwards.
        return max(allowed.astimezone(UTC), now), "quiet_hours"

    async def publish(self, *, session, reminder, task, now):
        tenant_id, now = _scope(), _now(now)
        if reminder.tenant_id != tenant_id or task.tenant_id != tenant_id:
            raise PermissionError("notification_workspace_required")
        if task.due_at is not None:
            deadline = task.due_at
        elif task.due_date is not None:
            deadline = datetime.combine(task.due_date, datetime.min.time(), ZoneInfo(task.timezone)) + timedelta(days=1)
        else:
            deadline = None
        late = deadline is not None and now > deadline
        # Generic text remains safe if task visibility is later revoked. Actual
        # content is read only through the freshly authorized planner page.
        message = ("Late reminder. " if late else "Task reminder. ") + f"/app/planner/{task.id}/edit#reminders"
        if reminder.channel in {"vk", "email", "max"}:
            message += f" External channel {reminder.channel} is unavailable; the reminder is in your inbox."
        event = await NotificationReceipt.objects.publish(
            session=session,
            recipient_user_ids=[reminder.recipient_user_id],
            title="Planner reminder",
            message=message,
            notification_type=NotificationType.SYSTEM_UPDATE,
            related_entity_type="planner_reminder",
            related_entity_id=reminder.id,
            severity="warning" if late or reminder.channel in {"vk", "email", "max"} else "info",
            dedup_key=f"planner-reminder:{reminder.id}:{reminder.generation}",
            expires_at=now + timedelta(days=30),
        )
        if reminder.channel != "inbox":
            session.add(
                NotificationDelivery(
                    tenant_id=tenant_id,
                    notification_id=event.id,
                    user_id=reminder.recipient_user_id,
                    channel=reminder.channel,
                    state="pending" if reminder.channel == "telegram" else "blocked",
                    next_attempt_at=now,
                    error_code=None if reminder.channel == "telegram" else "channel_not_implemented",
                )
            )
        reminder.late = late
        await session.flush()
        return event

    async def _rights(self, session, delivery):
        from app.models.planner import PlannerReminder, PlannerTask
        from app.services.planner.tasks import MANAGERS

        # Same actor -> workspace -> membership -> role -> task -> reminder
        # order as planner writes, held until the delivery outcome is persisted.
        user = await session.scalar(
            select(User).where(User.id == delivery.user_id, User.is_active.is_(True)).with_for_update(read=True)
        )
        tenant = await session.scalar(
            select(Tenant.id).where(Tenant.id == _scope(), Tenant.is_active.is_(True)).with_for_update(read=True)
        )
        member = await session.scalar(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == _scope(),
                TenantUser.user_id == delivery.user_id,
                TenantUser.channel == "web",
                TenantUser.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
        role = (
            None
            if member is None
            else await session.scalar(
                select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
            )
        )
        event = await session.get(Notification, delivery.notification_id)
        if event is not None and event.related_entity_type == "social_morning_brief":
            return await self._brief_rights(session, delivery, event, user, tenant, member, role)
        reminder_id = await session.scalar(
            select(PlannerReminder.id).where(
                PlannerReminder.tenant_id == _scope(),
                PlannerReminder.notification_id == delivery.notification_id,
                PlannerReminder.recipient_user_id == delivery.user_id,
            )
        )
        if user is None or tenant is None or ("plannertask", ActionType.VIEW) not in workspace_grants(role):
            return None
        reminder = await session.get(PlannerReminder, reminder_id) if reminder_id is not None else None
        if reminder is None:
            return None
        task = await session.scalar(
            select(PlannerTask)
            .where(PlannerTask.tenant_id == _scope(), PlannerTask.id == reminder.task_id)
            .with_for_update(read=True)
        )
        reminder = await session.scalar(
            select(PlannerReminder)
            .where(PlannerReminder.id == reminder.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            task is None
            or task.status in {"done", "cancelled"}
            or reminder.state != "delivered"
            or reminder.notification_id != delivery.notification_id
            or (role not in MANAGERS and delivery.user_id not in {task.owner_user_id, task.assignee_id})
        ):
            return None
        return task

    async def _brief_rights(self, session, delivery, event, user, tenant, member, role):
        """Reauthorize signed brief task and live source access at D18 dispatch."""
        from types import SimpleNamespace

        from app.models import AgentTask
        from app.services.social.brief_schedule import report_window_from_message, schedule_for_task

        if (
            user is None
            or tenant is None
            or member is None
            or role is None
            or event.tenant_id != _scope()
            or delivery.tenant_id != _scope()
        ):
            return None
        now = datetime.now(UTC)
        if event.expires_at is not None and event.expires_at <= now:
            return None
        task = await session.scalar(
            select(AgentTask)
            .where(AgentTask.tenant_id == _scope(), AgentTask.id == event.related_entity_id)
            .with_for_update(read=True)
        )
        if task is None or not task.is_active or task.job_type != "social.brief":
            return None
        try:
            schedule = schedule_for_task(task)
        except ValueError:
            return None
        if schedule.user_id != delivery.user_id or schedule.channel != delivery.channel:
            return None
        window = report_window_from_message(event, task=task, user_id=delivery.user_id)
        if window is None:
            return None
        from app.core.permissions import permission_scope
        from app.services.social.morning_brief import preview

        with permission_scope(user, member_grants=workspace_grants(role)):
            try:
                await preview(
                    _scope(),
                    delivery.user_id,
                    days=schedule.days,
                    now=datetime.now(UTC),
                    timezone_name=schedule.timezone,
                    report_start=window[0],
                    report_end=window[1],
                )
            except Exception:
                return None
        return SimpleNamespace(timezone=schedule.timezone)

    async def _telegram_recipient(self, session, user_id):
        # A workspace chat is not a person. Join a provider-verified personal
        # identity to persisted bot membership and a private bound chat. Never
        # accept recipient IDs, tokens, or authorization flags from the caller.
        rows = list(
            (
                await session.scalars(
                    select(PersonalConnectorAuthorization)
                    .where(
                        PersonalConnectorAuthorization.owner_user_id == user_id,
                        PersonalConnectorAuthorization.provider_id == "telegram",
                        PersonalConnectorAuthorization.auth_method == "mtproto",
                        PersonalConnectorAuthorization.state == "active",
                    )
                    .order_by(PersonalConnectorAuthorization.id)
                    .limit(2)
                    .with_for_update(read=True)
                )
            ).all()
        )
        if len(rows) != 1:
            return None
        subject = rows[0].subject_key
        if not subject.isascii() or not subject.isdecimal() or subject.startswith("0"):
            return None
        member = await session.scalar(
            select(TenantUser.id)
            .where(
                TenantUser.tenant_id == _scope(),
                TenantUser.channel == "telegram",
                TenantUser.external_user_id == subject,
                TenantUser.is_active.is_(True),
                TenantUser.role_id.is_not(None),
            )
            .with_for_update(read=True)
        )
        binding = await session.scalar(
            select(TenantChannel)
            .where(
                TenantChannel.tenant_id == _scope(),
                TenantChannel.channel == "telegram",
                TenantChannel.chat_id == subject,
                TenantChannel.kind == "private",
                TenantChannel.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
        return subject if member is not None and binding is not None else None

    async def dispatch_pending(self, *, limit=20, now=None):
        supplied_now = now
        tenant_id, now = _scope(), _now(now)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_page")
        counts = {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 0}
        async with async_session_maker() as session:
            ids = list(
                (
                    await session.scalars(
                        select(NotificationDelivery.id)
                        .where(
                            NotificationDelivery.tenant_id == tenant_id,
                            NotificationDelivery.state.in_(["pending", "sending"]),
                            NotificationDelivery.next_attempt_at <= now,
                        )
                        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
                        .limit(limit)
                    )
                ).all()
            )
        for delivery_id in ids:
            # Lock authority before the delivery row, then commit a durable
            # sending marker before any network operation. Concurrent dispatch
            # sees sending, so it cannot launch another attempt.
            async with async_session_maker() as session:
                delivery = await session.get(NotificationDelivery, delivery_id)
                if delivery is None:
                    continue
                task = await self._rights(session, delivery)
                row = await session.scalar(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.id == delivery_id, NotificationDelivery.tenant_id == tenant_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if row is None or row.state not in {"pending", "sending"} or row.next_attempt_at > now:
                    continue
                if row.state == "sending":
                    if row.started_at is not None and row.started_at <= now - timedelta(minutes=2):
                        row.state, row.error_code = "unknown", "outcome_unconfirmed"
                        counts["unknown"] += 1
                    await session.commit()
                    continue
                if task is None:
                    row.state, row.error_code = "blocked", "recipient_or_task_unavailable"
                    counts["blocked"] += 1
                else:
                    next_at, reason = await self.allowed_at(session, row.user_id, task.timezone, now)
                    if next_at is not None:
                        row.next_attempt_at, row.error_code = next_at, reason
                        counts["deferred"] += 1
                    elif row.channel != "telegram":
                        row.state, row.error_code = "blocked", "channel_not_implemented"
                        counts["blocked"] += 1
                    else:
                        row.state, row.started_at, row.error_code = "sending", now, None
                await session.commit()
                attempt = row.state == "sending"
            if not attempt:
                continue
            async with async_session_maker() as session:
                delivery = await session.get(NotificationDelivery, delivery_id)
                if delivery is None:
                    continue
                task = await self._rights(session, delivery)
                row = await session.scalar(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.id == delivery_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if row is None or row.state != "sending":
                    continue
                attempt_now = _now(supplied_now)
                if task is not None:
                    next_at, reason = await self.allowed_at(session, row.user_id, task.timezone, attempt_now)
                    if next_at is not None:
                        # No HTTP has occurred yet. A preference change between
                        # claim and send can safely put this attempt back in the
                        # queue; a crash-left sending marker is never reset here.
                        row.state, row.started_at = "pending", None
                        row.next_attempt_at, row.error_code = next_at, reason
                        counts["deferred"] += 1
                        await session.commit()
                        continue
                recipient = None if task is None else await self._telegram_recipient(session, row.user_id)
                if recipient is None:
                    row.state, row.error_code = "blocked", "personal_telegram_unavailable"
                    counts["blocked"] += 1
                else:
                    event = await session.get(Notification, row.notification_id)
                    # messenger already performs workspace/binding revalidation;
                    # hold user/task/identity/binding locks until HTTP returns.
                    try:
                        result = await messenger_service.send_notification(
                            title=event.title,
                            message=event.message,
                            notification_type=event.notification_type,
                            messenger="telegram",
                            recipient_id=recipient,
                            tenant_id=tenant_id,
                        )
                        success = (result.get("telegram") or {}).get("success") is True
                    except Exception:
                        success = False
                    row.state, row.error_code = ("sent", None) if success else ("unknown", "outcome_unconfirmed")
                    row.sent_at = now if success else None
                    counts[row.state] += 1
                await session.commit()
        return counts


reminder_notifications = ReminderNotifications()
