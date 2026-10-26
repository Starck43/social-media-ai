"""User-owned inbox across own/invited workspaces, never an ambient-tenant feed."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.models import AgentTask, Notification, NotificationReceipt, TenantUser
from app.services.notifications.inbox import PersonalInbox
from app.services.notifications.service import notify
from app.types import NotificationType
from tests.shared.fixtures.personal_workspace import personal_world

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def publish(world, workspace=0, recipients=(0,), **kwargs):
    with tenant_scope(world.tenants[workspace].id):
        return await NotificationReceipt.objects.publish(
            recipient_user_ids=[world.users[i].id for i in recipients],
            title="personal",
            message="synthetic",
            notification_type=NotificationType.REPORT_READY,
            **kwargs,
        )


async def test_inbox_is_global_user_owned_not_current_workspace(personal_world):
    w = personal_world
    first = await publish(w)
    invited = await publish(w, 1)
    other = await publish(w, recipients=(1,))
    with tenant_scope(w.tenants[0].id):
        archive = await Notification.objects.create_notification(
            title="legacy", message="not addressed", notification_type=NotificationType.REPORT_READY
        )
        await Notification.objects.update_by_id(first.id, is_read=True)
        with permission_scope(w.users[0]):
            entries = await PersonalInbox(w.users[0]).entries()
            assert {e.event.id for e in entries} == {first.id, invited.id}
            assert not any(e.receipt.read_at for e in entries)
            assert current_tenant_id() == w.tenants[0].id
        with permission_scope(w.users[1]):
            assert {e.event.id for e in await PersonalInbox(w.users[1]).entries()} == {other.id}


async def test_read_is_personal_even_in_same_workspace(personal_world):
    w = personal_world
    event = await publish(w, recipients=(0, 1))
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        assert await PersonalInbox(w.users[0]).read(event.id)
        assert await PersonalInbox(w.users[0]).read(event.id)
        assert not await PersonalInbox(w.users[0]).entries(is_read=False)
        assert not (await Notification.objects.get(id=event.id)).is_read
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[1]):
        assert len(await PersonalInbox(w.users[1]).entries(is_read=False)) == 1
        with pytest.raises(PermissionError):
            await NotificationReceipt.objects.list_for_user(w.users[0].id)


async def test_no_implicit_broadcast_or_foreign_recipient(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id):
        await notify.create(
            title="background", message="synthetic", ntype=NotificationType.API_ERROR, recipient_user_ids=[]
        )
        with pytest.raises(PermissionError):
            await publish(w, recipients=(2,))
        with permission_scope(w.users[0]):
            assert not await PersonalInbox(w.users[0]).entries()


async def test_receipt_publish_is_atomic_idempotent_and_content_bound(personal_world):
    w = personal_world
    first, second = await asyncio.gather(publish(w, dedup_key="same"), publish(w, dedup_key="same"))
    assert first.id == second.id
    with pytest.raises(ValueError):
        await publish(w, dedup_key="same", recipients=(1,))
    with tenant_scope(w.tenants[1].id), permission_scope(w.users[0]):
        assert len(await PersonalInbox(w.users[0]).entries()) == 1
        assert (await NotificationReceipt.objects.stats_for_user(w.users[0].id))["total"] == 1


async def test_revocation_expiry_and_personal_dismissal(personal_world):
    w = personal_world
    event = await publish(w, recipients=(0, 1))
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await NotificationReceipt.objects.dismiss(w.users[0].id, [event.id])
        assert not await PersonalInbox(w.users[0]).entries()
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[1]):
        assert len(await PersonalInbox(w.users[1]).entries()) == 1
        await TenantUser.objects.update_by_id(w.memberships[2].id, is_active=False)
        assert not await PersonalInbox(w.users[1]).entries()
        await TenantUser.objects.update_by_id(w.memberships[2].id, is_active=True)
        await Notification.objects.update_by_id(event.id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert not await PersonalInbox(w.users[1]).entries()


async def test_linked_entity_acl_and_origin_are_rechecked(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id), operator_permission_scope():
        task = await AgentTask.objects.create(name=w.tag, cron_expr="0 0 * * *", job_type="collect")
    event = await publish(w, workspace=1, related_entity_type="task", related_entity_id=task.id)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        assert not await PersonalInbox(w.users[0]).entries()
        assert not await PersonalInbox(w.users[0]).read(event.id)


async def test_default_recipient_requires_active_origin_membership(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        own = await notify.create("mine", "safe", NotificationType.REPORT_READY)
        assert {e.event.id for e in await PersonalInbox(w.users[0]).entries()} == {own.id}
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[3]):
        archive = await notify.create("unscoped account", "safe", NotificationType.REPORT_READY)
        assert not await PersonalInbox(w.users[3]).entries()
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        assert not await PersonalInbox(w.users[0]).entries(notification_id=archive.id)


async def test_job_claim_preserves_trusted_global_recipient_and_attempt_dedup(personal_world):
    from app.jobs.claim_outcomes import JobClaim
    from app.jobs.dispatcher import _notify_job_result
    from app.models import Job

    w = personal_world
    with tenant_scope(w.tenants[1].id):
        job = await Job.objects.create(
            job_type="collect",
            payload={"initiated_by_user_id": w.users[2].id},
            initiated_by_user_id=w.users[0].id,
            run_at=datetime.now(timezone.utc),
            max_attempts=2,
        )
        acquired = await Job.objects.claim_job(job.id)
        claim = JobClaim.capture(acquired)
        assert claim.initiated_by_user_id == w.users[0].id
        await _notify_job_result(claim, success=False)
        await _notify_job_result(claim, success=False)
        await _notify_job_result(claim, success=True, result={"collected": 1})
        with permission_scope(w.users[0]):
            entries = await PersonalInbox(w.users[0]).entries()
            assert len(entries) == 2
            assert {e.event.severity for e in entries} == {"action_required", "info"}
        with permission_scope(w.users[2]):
            assert not await PersonalInbox(w.users[2]).entries()


async def test_cleanup_removes_only_own_old_receipts(personal_world):
    w = personal_world
    event = await publish(w, recipients=(0, 1))
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await NotificationReceipt.objects.mark_read(w.users[0].id, [event.id])
        # Owned synthetic fixture: emulate an old acknowledged receipt.
        await NotificationReceipt.objects.filter(user_id=w.users[0].id, notification_id=event.id).update(
            read_at=datetime.now(timezone.utc) - timedelta(days=40)
        )
        assert await NotificationReceipt.objects.cleanup_for_user(w.users[0].id, 30) == 1
        assert not await PersonalInbox(w.users[0]).entries()
        assert await Notification.objects.get(id=event.id) is not None
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[1]):
        assert len(await PersonalInbox(w.users[1]).entries(is_read=False)) == 1


async def test_personal_stats_are_sql_totals_not_the_bounded_read_window(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        async with NotificationReceipt.objects._get_owned_session() as session:
            events = [
                Notification(
                    tenant_id=w.tenants[0].id,
                    title="bounded",
                    message="safe",
                    notification_type=NotificationType.REPORT_READY,
                )
                for _ in range(105)
            ]
            session.add_all(events)
            await session.flush()
            session.add_all([NotificationReceipt(notification_id=e.id, user_id=w.users[0].id) for e in events])
        assert (await NotificationReceipt.objects.stats_for_user(w.users[0].id))["unread"] == 105
        assert await PersonalInbox(w.users[0]).read_window() == 100
        stats = await NotificationReceipt.objects.stats_for_user(w.users[0].id)
        assert stats["total"] == 105 and stats["unread"] == 5
