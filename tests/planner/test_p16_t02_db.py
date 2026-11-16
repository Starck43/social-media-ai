"""PostgreSQL-only reminder ownership, persistence and exactly-once inbox checks."""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    PersonalConnectorAuthorization,
    Tenant,
    TenantChannel,
    TenantUser,
)
from app.models.planner import PlannerReminder, PlannerTask
from app.services.notifications.messenger import messenger_service
from app.services.notifications.reminders import reminder_notifications
from app.services.planner.contracts import TaskCreate
from app.services.planner.reminder_contracts import ReminderCreate, ReminderReschedule, ReminderSnooze
from app.services.planner.reminders import reminder_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.types import ActionType
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
UTC = timezone.utc


@pytest.fixture(autouse=True)
def reminder_database_gate(monkeypatch):
    assert require_isolated_schema().startswith("test_"), "planner reminders require an isolated test schema"
    assert settings.DB_SCHEMA == settings.DB_TEST_SCHEMA, "planner reminder schema must match explicit test target"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def scope(world, *, tenant=0, user=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[tenant].id))
    role_name = "owner" if user in {0, 1, 2} else "viewer"
    stack.enter_context(
        permission_scope(
            world.users[user],
            member_grants=__import__("app.core.access_catalog", fromlist=["workspace_grants"]).workspace_grants(
                role_name
            ),
        )
    )
    return stack


async def test_personal_reminder_is_unique_snoozable_and_only_owner_can_mutate(personal_world):
    world = personal_world
    tenant_id = world.tenants[0].id
    with scope(world):
        task = await planner_service.create(
            TaskCreate(title="Synthetic reminder task", due_date=datetime(2099, 1, 1).date())
        )
        request = ReminderCreate(preset="day")
        reminder = await reminder_service.create(task.id, request)
        assert reminder.recipient_user_id == world.users[0].id
        with pytest.raises(PlannerConflict, match="exists"):
            await reminder_service.create(task.id, request)
        assert len(await reminder_service.list(task.id)) == 1
        with pytest.raises(PlannerConflict, match="stale"):
            await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=2, preset="hour"))

    # The other workspace member cannot discover or change this user's reminder.
    with scope(world, user=1):
        with pytest.raises(PlannerUnavailable):
            await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=1, preset="day"))

    with scope(world):
        snoozed = await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=1, preset="hour"))
        assert snoozed.generation == 2 and snoozed.version == 2
        assert snoozed.state == "pending" and snoozed.notification_id is None
        with pytest.raises(PlannerConflict, match="stale"):
            await reminder_service.cancel(reminder.id, expected_version=1)
        rescheduled_at = datetime.now(UTC) + timedelta(days=2)
        rescheduled = await reminder_service.reschedule(
            reminder.id,
            ReminderReschedule(expected_version=2, remind_at=rescheduled_at),
        )
        assert rescheduled.version == 3 and rescheduled.generation == 3
        assert rescheduled.preset == "custom" and rescheduled.snoozed_until is None
        cancelled = await reminder_service.cancel(reminder.id, expected_version=3)
        assert cancelled.state == "cancelled" and cancelled.version == 4
    with tenant_scope(tenant_id):
        outcome = await reminder_service.dispatch_due(now=rescheduled_at + timedelta(days=1))
    assert outcome["sent"] == 0
    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.tenant_id == tenant_id, Notification.related_entity_type == "planner_reminder")
            )
            == 0
        )


async def test_closed_task_rejects_reminder_snooze_and_reschedule(personal_world):
    world = personal_world
    with scope(world):
        task = await planner_service.create(
            TaskCreate(title="Synthetic closed reminder task", due_date=datetime(2099, 1, 1).date())
        )
        reminder = await reminder_service.create(task.id, ReminderCreate(preset="day"))
        await planner_service.complete(task.id, expected_version=1)
        with pytest.raises(PlannerConflict, match="task_not_remindable"):
            await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=1, preset="hour"))
        with pytest.raises(PlannerConflict, match="task_not_remindable"):
            await reminder_service.reschedule(
                reminder.id,
                ReminderReschedule(expected_version=1, remind_at=datetime.now(UTC) + timedelta(days=1)),
            )


async def test_due_reminder_publishes_once_and_snooze_creates_new_inbox_generation(personal_world):
    world = personal_world
    tenant_id, user_id = world.tenants[0].id, world.users[0].id
    with scope(world):
        task = await planner_service.create(
            TaskCreate(title="Synthetic due reminder", due_date=datetime.now(UTC).date())
        )
        now = datetime.now(UTC)
        reminder = await reminder_service.create(
            task.id,
            ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1), channel="inbox"),
        )

    with tenant_scope(tenant_id):
        first, second = await asyncio.gather(
            reminder_service.dispatch_due(now=now), reminder_service.dispatch_due(now=now)
        )
    assert first["sent"] + second["sent"] == 1
    async with async_session_maker() as session:
        refreshed = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        assert refreshed.state == "delivered" and refreshed.notification_id is not None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.tenant_id == tenant_id, Notification.related_entity_type == "planner_reminder")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(NotificationDelivery.tenant_id == tenant_id)
            )
            == 0
        )

    with scope(world):
        snoozed = await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=2, preset="hour"))
    assert snoozed.generation == 2
    with tenant_scope(tenant_id):
        second_generation = await reminder_service.dispatch_due(now=snoozed.next_attempt_at)
    assert second_generation["sent"] == 1
    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.tenant_id == tenant_id, Notification.related_entity_type == "planner_reminder")
            )
            == 2
        )


async def test_quiet_hours_defer_without_publishing_and_retry_late(personal_world):
    world = personal_world
    now = datetime(2026, 11, 8, 23, 0, tzinfo=UTC)
    with scope(world):
        await NotificationPreference.objects.save_for_user(
            world.users[0].id,
            {
                "version": 1,
                "connections": True,
                "tasks": True,
                "popup": False,
                "quiet": True,
                "start": "22:00",
                "end": "08:00",
            },
        )
        task = await planner_service.create(TaskCreate(title="Synthetic quiet reminder", due_date=now.date()))
        reminder = await reminder_service.create(
            task.id,
            ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1)),
        )
    with tenant_scope(world.tenants[0].id):
        first = await reminder_service.dispatch_due(now=now)
    assert first["deferred"] == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        assert row.state == "deferred" and row.last_error == "quiet_hours"
        assert row.next_attempt_at == datetime(2026, 11, 9, 8, tzinfo=UTC)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.related_entity_type == "planner_reminder")
            )
            == 0
        )
    with tenant_scope(world.tenants[0].id):
        second = await reminder_service.dispatch_due(now=row.next_attempt_at)
    assert second["sent"] == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        assert row.state == "delivered" and row.late is True


async def test_revoked_membership_defers_and_unsupported_channel_is_reported(personal_world):
    world = personal_world
    now = datetime.now(UTC)
    with scope(world):
        task = await planner_service.create(TaskCreate(title="Synthetic unsupported reminder", due_date=now.date()))
        reminder = await reminder_service.create(
            task.id,
            ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1), channel="email"),
        )
        membership = world.memberships[0]
        membership.is_active = False
        async with async_session_maker() as session:
            session.add(membership)
            await session.commit()
    with tenant_scope(world.tenants[0].id):
        blocked = await reminder_service.dispatch_due(now=now)
    assert blocked["deferred"] == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        assert row.state == "deferred" and row.notification_id is None

    # Restore membership and let the durable reminder publish. Email is explicit
    # in the inbox with a terminal unsupported-channel delivery result.
    membership.is_active = True
    async with async_session_maker() as session:
        session.add(membership)
        await session.commit()
    with tenant_scope(world.tenants[0].id):
        delivered = await reminder_service.dispatch_due(now=now + timedelta(hours=1))
    assert delivered["sent"] == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        delivery = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == row.notification_id)
        )
        assert delivery.state == "blocked" and delivery.error_code == "channel_not_implemented"


async def test_foreign_workspace_cannot_create_or_list_reminder(personal_world):
    world = personal_world
    with scope(world):
        task = await planner_service.create(
            TaskCreate(title="Synthetic tenant reminder", due_date=datetime(2099, 1, 1).date())
        )
        reminder = await reminder_service.create(task.id, ReminderCreate(preset="day"))
    # User 0 is also a manager in workspace 1, but task lookup remains tenant-scoped.
    with scope(world, tenant=1):
        with pytest.raises(PlannerUnavailable):
            await reminder_service.create(task.id, ReminderCreate(preset="day"))
        with pytest.raises(PlannerUnavailable):
            await reminder_service.snooze(reminder.id, ReminderSnooze(expected_version=1, preset="hour"))


async def test_telegram_without_verified_personal_identity_is_blocked_before_transport(personal_world, monkeypatch):
    world = personal_world
    tenant_id = world.tenants[0].id
    now = datetime.now(UTC)
    with scope(world):
        task = await planner_service.create(TaskCreate(title="Synthetic Telegram reminder", due_date=now.date()))
        reminder = await reminder_service.create(
            task.id,
            ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1), channel="telegram"),
        )
    send = AsyncMock()
    monkeypatch.setattr(messenger_service, "send_notification", send)
    with tenant_scope(tenant_id):
        produced = await reminder_service.dispatch_due(now=now)
        assert produced["sent"] == 1
        result = await reminder_notifications.dispatch_pending(now=now)
    assert result["blocked"] == 1
    send.assert_not_awaited()
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        delivery = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == row.notification_id)
        )
        assert row.state == "delivered" and delivery.state == "blocked"
        assert delivery.error_code == "personal_telegram_unavailable"


async def test_verified_private_telegram_binding_sends_once_and_records_receipt(personal_world, monkeypatch):
    world = personal_world
    tenant_id, user = world.tenants[0].id, world.users[0]
    subject = f"987654321{user.id}"
    now = datetime.now(UTC)
    with scope(world):
        task = await planner_service.create(
            TaskCreate(title="Synthetic verified Telegram reminder", due_date=now.date())
        )
        reminder = await reminder_service.create(
            task.id,
            ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1), channel="telegram"),
        )
        role = world.roles["owner"]
    async with async_session_maker() as session:
        session.add(
            PersonalConnectorAuthorization(
                owner_user_id=user.id,
                provider_id="telegram",
                auth_method="mtproto",
                subject_key=subject,
                state="active",
                secret_encrypted="synthetic-ciphertext",
            )
        )
        session.add(
            TenantUser(
                tenant_id=tenant_id,
                channel="telegram",
                external_user_id=subject,
                user_id=None,
                role_id=role.id,
                is_active=True,
            )
        )
        session.add(
            TenantChannel(
                tenant_id=tenant_id,
                channel="telegram",
                chat_id=subject,
                kind="private",
                is_active=True,
            )
        )
        await session.commit()

    async def acknowledged(**kwargs):
        return {"telegram": {"success": True}}

    send = AsyncMock(side_effect=acknowledged)
    monkeypatch.setattr(messenger_service, "send_notification", send)
    with tenant_scope(tenant_id):
        produced = await reminder_service.dispatch_due(now=now)
        first = await reminder_notifications.dispatch_pending(now=now)
        second = await reminder_notifications.dispatch_pending(now=now)
    assert produced["sent"] == 1 and first["sent"] == 1 and second["sent"] == 0
    assert send.await_count == 1
    async with async_session_maker() as session:
        row = await session.scalar(select(PlannerReminder).where(PlannerReminder.id == reminder.id))
        delivery = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == row.notification_id)
        )
        assert delivery.state == "sent" and delivery.sent_at is not None
        assert delivery.error_code is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(
                    NotificationDelivery.notification_id == row.notification_id,
                    NotificationDelivery.user_id == user.id,
                    NotificationDelivery.channel == "telegram",
                )
            )
            == 1
        )
