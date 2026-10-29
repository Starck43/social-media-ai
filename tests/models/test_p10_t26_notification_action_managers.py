"""P10-T26: notification/bot-action managers keep SQL-side, tenant-scoped behavior.

Real PostgreSQL test schema. Each test opts out of the conftest bypass
(`@pytest.mark.tenancy`) so the tenant guard is observable, creates its own
workspaces and deletes them afterwards (CASCADE removes their rows).
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentScenario, BotAction, Notification, Platform, Source, Tenant
from app.types import AgentActionType, BotActionStatus, NotificationType, PlatformType, SourceType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def workspaces():
    created = []
    with tenant_scope(bypass=True):
        for label in ("a", "b"):
            created.append(await Tenant.objects.create(slug=f"p10t26-{label}-{uuid.uuid4().hex[:8]}", name=label))
    try:
        yield created
    finally:
        with tenant_scope(bypass=True):
            for tenant in created:
                await Tenant.objects.delete_by_id(tenant.id)


async def _notify(ntype=NotificationType.REPORT_READY, **extra):
    return await Notification.objects.create_notification(
        title="P10-T26", message="synthetic", notification_type=ntype, **extra
    )


async def test_notification_bulk_operations_stay_inside_the_workspace(workspaces):
    first, second = workspaces
    with tenant_scope(first.id):
        await _notify()
        await _notify(NotificationType.API_ERROR)
        old = await _notify()
    with tenant_scope(second.id):
        await _notify()

    with tenant_scope(bypass=True):
        await Notification.objects.filter(id=old.id).update(created_at=datetime.now(timezone.utc) - timedelta(days=40))

    with tenant_scope(first.id):
        assert await Notification.objects.get_unread_count() == 3
        assert len(await Notification.objects.get_recent(days=7)) == 2
        stats = await Notification.objects.get_stats()
        assert stats["total"] == 3 and stats["unread"] == 3 and stats["read"] == 0
        assert stats["by_type"]["api_error"] == {"total": 1, "unread": 1}
        assert stats["by_type"]["report_ready"] == {"total": 2, "unread": 2}

        assert await Notification.objects.mark_all_as_read(NotificationType.API_ERROR) == 1
        assert await Notification.objects.get_unread_count() == 2
        assert await Notification.objects.mark_all_as_read() == 2
        assert (await Notification.objects.get_stats())["read"] == 3

        assert await Notification.objects.delete_old_notifications(days=30) == 1
        assert await Notification.objects.count() == 2

    with tenant_scope(second.id):
        # The other workspace's read flags and rows were not touched.
        assert await Notification.objects.get_unread_count() == 1
        assert await Notification.objects.count() == 1


@pytest.fixture
async def action_refs(workspaces):
    with tenant_scope(bypass=True):
        platform = await Platform.objects.create(
            name=f"p10t26_{uuid.uuid4().hex[:8]}", platform_type=PlatformType.VK.db_value, base_url="https://vk.com"
        )
    refs = {}
    try:
        for tenant in workspaces:
            with tenant_scope(tenant.id), operator_permission_scope():
                scenario = await AgentScenario.objects.create(name="P10-T26", is_active=True)
                source = await Source.objects.create(
                    platform_id=platform.id,
                    name="P10-T26",
                    source_type=SourceType.CHANNEL,
                    external_id=uuid.uuid4().hex[:10],
                    is_active=True,
                )
                refs[tenant.id] = (scenario.id, source.id)
        yield refs
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for tenant in workspaces:
                await BotAction.objects.filter(tenant_id=tenant.id).delete()
                await Source.objects.filter(tenant_id=tenant.id).delete()
                await AgentScenario.objects.filter(tenant_id=tenant.id).delete()
            await Platform.objects.delete_by_id(platform.id)


async def _action(scenario_id: int, source_id: int) -> BotAction:
    return await BotAction.objects.create(
        agent_scenario_id=scenario_id,
        source_id=source_id,
        action_type=AgentActionType.COMMENT,
        status=BotActionStatus.PENDING,
        payload={"text": "synthetic"},
        dry_run=True,
    )


async def test_bot_action_ledger_reads_are_workspace_scoped(workspaces, action_refs):
    first, second = workspaces
    with tenant_scope(first.id):
        own = await _action(*action_refs[first.id])
    with tenant_scope(second.id):
        await _action(*action_refs[second.id])

    with tenant_scope(first.id):
        assert [a.id for a in await BotAction.objects.get_pending_actions()] == [own.id]
        assert [a.id for a in await BotAction.objects.get_recent_actions(hours=1)] == [own.id]
        assert await BotAction.objects.count_actions_in_window(agent_scenario_id=action_refs[second.id][0]) == 0


async def test_bot_action_transitions_require_an_authenticated_actor(workspaces, action_refs):
    first, _second = workspaces
    with tenant_scope(first.id):
        action = await _action(*action_refs[first.id])
        # P9-T08 supersedes the old unauthenticated state-only API. Full
        # sequential/concurrent actor/token tests live in test_p9_t08_action_ledger.
        with pytest.raises(PermissionError):
            await BotAction.objects.approve_action(action_id=action.id)
        with pytest.raises(PermissionError):
            await BotAction.objects.mark_executed(action_id=action.id)
        assert (await BotAction.objects.get(id=action.id)).status == BotActionStatus.PENDING
