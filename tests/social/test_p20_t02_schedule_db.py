"""PostgreSQL acceptance for signed social brief schedules and D18 dedup."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import String, cast, delete, select, text

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    ActionAuditEvent,
    AgentTask,
    AIAnalytics,
    Job,
    Notification,
    NotificationDelivery,
    NotificationReceipt,
    Platform,
    Source,
    TenantUser,
)
from app.models.managers.agent_task_manager import AgentTaskManager
from app.services.notifications.messenger import messenger_service
from app.services.notifications.reminders import reminder_notifications
from app.services.social.brief_schedule import (
    configure_schedule,
    publish_dispatch,
    verify_occurrence_token,
)
from app.services.social.morning_brief import SocialBriefRepository, preview
from app.types import PeriodType, PlatformType, SourceType
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
UTC = timezone.utc


@pytest.fixture(autouse=True)
def isolated_schema():
    require_isolated_schema()


def owner_scope(world, *, user_index=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[0].id))
    stack.enter_context(permission_scope(world.users[user_index], member_grants=workspace_grants("owner")))
    return stack


@pytest.fixture
async def brief_world(personal_world):
    world = personal_world
    tenant_id = world.tenants[0].id
    try:
        yield world
    finally:
        with tenant_scope(bypass=True):
            async with async_session_maker() as session, session.begin():
                event_ids = select(Notification.id).where(
                    Notification.tenant_id == tenant_id,
                    Notification.related_entity_type == "social_morning_brief",
                )
                await session.execute(
                    delete(NotificationDelivery).where(NotificationDelivery.notification_id.in_(event_ids))
                )
                await session.execute(
                    delete(NotificationReceipt).where(NotificationReceipt.notification_id.in_(event_ids))
                )
                await session.execute(delete(Notification).where(Notification.id.in_(event_ids)))
                task_ids = select(AgentTask.id).where(
                    AgentTask.tenant_id == tenant_id, AgentTask.job_type == "social.brief"
                )
                await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
                await session.execute(
                    delete(ActionAuditEvent).where(
                        ActionAuditEvent.tenant_id == tenant_id,
                        ActionAuditEvent.action == "scheduling.task_admit",
                        ActionAuditEvent.resource_id.in_(
                            select(cast(AgentTask.id, String)).where(
                                AgentTask.tenant_id == tenant_id, AgentTask.job_type == "social.brief"
                            )
                        ),
                    )
                )
                await session.execute(delete(Job).where(Job.tenant_id == tenant_id, Job.agent_task_id.in_(task_ids)))
                await session.execute(delete(AgentTask).where(AgentTask.id.in_(task_ids)))
                source_ids = select(Source.id).where(
                    Source.tenant_id.in_([tenant.id for tenant in world.tenants]),
                    Source.external_id.like(f"{world.tag}-brief-%"),
                )
                await session.execute(delete(AIAnalytics).where(AIAnalytics.source_id.in_(source_ids)))
                await session.execute(delete(Source).where(Source.id.in_(source_ids)))
                remaining_notifications = await session.scalar(
                    select(Notification.id)
                    .where(
                        Notification.tenant_id == tenant_id,
                        Notification.related_entity_type == "social_morning_brief",
                    )
                    .limit(1)
                )
                remaining_tasks = await session.scalar(
                    select(AgentTask.id)
                    .where(AgentTask.tenant_id == tenant_id, AgentTask.job_type == "social.brief")
                    .limit(1)
                )
                assert remaining_notifications is None
                assert remaining_tasks is None


async def test_preview_reads_only_authorized_current_tenant_sources(brief_world, monkeypatch):
    world = brief_world
    tenant_a, tenant_b = (tenant.id for tenant in world.tenants)
    platform = await Platform.objects.get(platform_type=PlatformType.VK)
    end_date = datetime.now(UTC).date() - timedelta(days=2)
    observed_now = datetime.combine(end_date, datetime.min.time(), UTC) + timedelta(hours=12)
    with tenant_scope(bypass=True):
        async with async_session_maker() as session, session.begin():
            source_a = Source(
                tenant_id=tenant_a,
                platform_id=platform.id,
                name="Synthetic authorized brief source A",
                source_type=SourceType.PUBLIC,
                external_id=f"{world.tag}-brief-a",
                params={},
                is_active=True,
            )
            source_b = Source(
                tenant_id=tenant_b,
                platform_id=platform.id,
                name="Synthetic foreign brief source B",
                source_type=SourceType.PUBLIC,
                external_id=f"{world.tag}-brief-b",
                params={},
                is_active=True,
            )
            session.add_all([source_a, source_b])
            await session.flush()
            row_a = AIAnalytics(
                tenant_id=tenant_a,
                source_id=source_a.id,
                analysis_date=end_date,
                period_type=PeriodType.DAY,
                analysis_fingerprint=f"{world.tag}-brief-a",
                summary_data={"analysis_title": "URGENT authorized evidence"},
            )
            row_b = AIAnalytics(
                tenant_id=tenant_b,
                source_id=source_b.id,
                analysis_date=end_date,
                period_type=PeriodType.DAY,
                analysis_fingerprint=f"{world.tag}-brief-b",
                summary_data={"analysis_title": "URGENT foreign evidence"},
            )
            session.add_all([row_a, row_b])
            await session.flush()
            own_analysis_id = row_a.id
            foreign_analysis_id = row_b.id

    async def authorized_sources(self, sources):
        return {source.id: SimpleNamespace(blocked=False, service_only=True) for source in sources}

    monkeypatch.setattr(SocialBriefRepository, "access", authorized_sources)
    with owner_scope(world):
        brief = await preview(
            tenant_a,
            world.users[0].id,
            days=1,
            now=observed_now,
            timezone_name="Europe/Kirov",
            report_start=end_date,
            report_end=end_date,
        )
        assert [item.authoritative_ref for item in brief.items] == [f"analysis:{own_analysis_id}"]
        assert f"analysis:{foreign_analysis_id}" not in {item.authoritative_ref for item in brief.items}

        async with async_session_maker() as session, session.begin():
            current = await session.get(Source, source_a.id)
            current.is_active = False
        after_revoke = await preview(
            tenant_a,
            world.users[0].id,
            days=1,
            now=observed_now,
            timezone_name="Europe/Kirov",
            report_start=end_date,
            report_end=end_date,
        )
        assert after_revoke.items == ()
        assert any("недоступен" in notice for notice in after_revoke.notices)


async def test_schedule_admission_notification_dedup_and_revoked_dispatch(brief_world, monkeypatch):
    world = brief_world
    tenant_id = world.tenants[0].id
    user_id = world.users[0].id
    monkeypatch.setattr(settings, "SECRET_KEY", "p20-t02-test-secret")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://brief.example.test")
    transport = AsyncMock(return_value={"telegram": {"success": True}})
    monkeypatch.setattr(messenger_service, "send_notification", transport)

    async def authorized_sources(self, sources):
        return {source.id: SimpleNamespace(blocked=False, service_only=True) for source in sources}

    monkeypatch.setattr(SocialBriefRepository, "access", authorized_sources)
    before = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(days=5)

    with owner_scope(world):
        task = await configure_schedule(
            tenant_id,
            user_id,
            local_time="07:05",
            timezone_name="Europe/Kirov",
            days=2,
            channel="telegram",
            notify_confirmed=True,
            now=before,
        )
        assert task.job_type == "social.brief" and task.is_active
        assert task.next_run_at is not None

        with pytest.raises(ValueError, match="managed by their schedule form"):
            await AgentTaskManager().update_by_id(task.id, name="edited outside schedule form")
        with pytest.raises(ValueError, match="signed morning brief schedule service"):
            await AgentTaskManager().create(
                name="Unsigned brief",
                cron_expr="5 7 * * *",
                job_type="social.brief",
                payload={},
            )

    # A different owner in the same workspace cannot configure somebody else's schedule.
    with owner_scope(world, user_index=1), pytest.raises(PermissionError, match="schedule_denied"):
        await configure_schedule(
            tenant_id,
            user_id,
            local_time="07:05",
            timezone_name="Europe/Kirov",
            days=2,
            channel="inbox",
            now=before,
        )

    occurrence = task.next_run_at
    with tenant_scope(tenant_id):
        admission = await AgentTaskManager()._admit_task_run(
            task.id,
            now=occurrence + timedelta(seconds=1),
            expected_next_run_at=occurrence,
            scheduled=True,
            timezone_name="UTC",
        )
        assert admission is not None and admission["status"] == "enqueued"
        job = await Job.objects.get(id=admission["job"].id)
        assert job.payload["scheduled_for"] == occurrence.astimezone(UTC).isoformat()
        assert job.payload["signed_config"] == task.payload["signed_config"]
        assert verify_occurrence_token(
            job.payload["occurrence_token"],
            tenant_id=tenant_id,
            task_id=task.id,
            config_token=task.payload["signed_config"],
            scheduled_for=occurrence,
        )
        assert not verify_occurrence_token(
            job.payload["occurrence_token"],
            tenant_id=tenant_id,
            task_id=task.id,
            config_token=task.payload["signed_config"],
            scheduled_for=occurrence + timedelta(days=1),
        )

    with owner_scope(world):
        schedule = __import__("app.services.social.brief_schedule", fromlist=["schedule_for_task"]).schedule_for_task(
            task
        )
        token = task.payload["signed_config"]
        first_id = await publish_dispatch(
            schedule,
            task_id=task.id,
            task_config_token=token,
            scheduled_for=occurrence,
        )
        repeated_id = await publish_dispatch(
            schedule,
            task_id=task.id,
            task_config_token=token,
            scheduled_for=occurrence,
        )
    assert first_id == repeated_id

    async with async_session_maker() as session:
        event_count = await session.scalar(select(Notification.id).where(Notification.id == first_id).limit(1))
        receipt_count = await session.scalar(
            select(NotificationReceipt.id).where(NotificationReceipt.notification_id == first_id).limit(1)
        )
        delivery_rows = list(
            (
                await session.scalars(
                    select(NotificationDelivery).where(NotificationDelivery.notification_id == first_id)
                )
            ).all()
        )
    assert event_count == first_id and receipt_count is not None
    assert len(delivery_rows) == 1 and delivery_rows[0].state == "pending"

    # Revoking the recipient membership blocks before any external transport.
    async with async_session_maker() as session, session.begin():
        membership = await session.scalar(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.channel == "web",
            )
        )
        membership.is_active = False
    with tenant_scope(tenant_id):
        result = await reminder_notifications.dispatch_pending(now=datetime.now(UTC) + timedelta(minutes=1))
    assert result["blocked"] == 1
    transport.assert_not_awaited()
    async with async_session_maker() as session:
        delivery = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == first_id)
        )
        assert delivery.state == "blocked"
        assert delivery.error_code == "recipient_or_task_unavailable"

    async with async_session_maker() as session, session.begin():
        membership = await session.scalar(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.channel == "web",
            )
        )
        membership.is_active = True

    # A fresh occurrence reauthorizes its signed frozen window, sends once via
    # the existing D18 transport, and becomes terminal after acknowledgment.
    next_occurrence = occurrence + timedelta(days=1)
    with owner_scope(world):
        second_id = await publish_dispatch(
            schedule,
            task_id=task.id,
            task_config_token=task.payload["signed_config"],
            scheduled_for=next_occurrence,
        )
    assert second_id != first_id
    monkeypatch.setattr(reminder_notifications, "_telegram_recipient", AsyncMock(return_value="verified-chat"))
    dispatch_now = datetime.now(UTC) + timedelta(minutes=1)
    with tenant_scope(tenant_id):
        sent = await reminder_notifications.dispatch_pending(now=dispatch_now)
        repeated = await reminder_notifications.dispatch_pending(now=dispatch_now + timedelta(minutes=1))
    assert sent["sent"] == 1
    assert repeated["sent"] == 0
    transport.assert_awaited_once()
    async with async_session_maker() as session:
        delivered = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == second_id)
        )
        assert delivered.state == "sent" and delivered.sent_at is not None
