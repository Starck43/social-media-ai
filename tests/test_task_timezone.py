"""The workspace timezone must decide when its tasks fire.

A task is scheduled twice: when it is created/edited, and again after every
fire by the runner. Both writers must resolve the same zone, otherwise the
schedule silently moves by the offset between the workspace zone and
`SCHEDULER_TIMEZONE` on the first run — hours off, with no error anywhere.

Real PostgreSQL (same policy as the rest of the suite).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.permissions import get_current_user, has_permission, permission_scope, service_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Job, Platform, Source, User
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.tasks import runner as runner_module
from app.tasks.cron import next_run_at, resolve_tz
from app.types import SourceType

WORKSPACE_TZ = "Asia/Yekaterinburg"  # UTC+5, deliberately not Europe/Moscow
CRON = "0 9 * * *"

# These tests exercise a workspace scheduler, not the legacy platform bypass.
# Without this marker conftest forces BaseManager.is_bypass even inside scope.
pytestmark = pytest.mark.tenancy


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _register_workspace(client: AsyncClient, prefix: str, tz: str) -> tuple[User, int]:
    """Register a user whose new workspace carries `tz`."""
    import re

    csrf = re.search(r'name="_csrf" value="([^"]+)"', (await client.get("/app/register")).text).group(1)
    username = _name(prefix)
    await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": csrf,
        },
    )
    user = await User.objects.get(username=username)
    tenant_id = (await TenantUserManager().web_memberships(user.id))[0].tenant_id
    with tenant_scope(tenant_id):
        await tenants.update_by_id(tenant_id, timezone=tz)
    return user, tenant_id


async def _make_source(tenant_id: int) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(tenant_id), service_permission_scope("source", "create"):
        return await Source.objects.create(
            name=_name("src"),
            platform_id=platform.id,
            external_id=_name("ext"),
            source_type=SourceType.USER,
            is_active=True,
            params={},
            tenant_id=tenant_id,
        )


async def _drop(user: User | None, tenant_id: int | None, *rows) -> None:
    for row in rows:
        if row is not None:
            assert isinstance(row, (AgentTask, Source)), "cleanup accepts only its own task/source rows"
            model_name = "agenttask" if isinstance(row, AgentTask) else "source"
            with tenant_scope(row.tenant_id), service_permission_scope(model_name, "delete"):
                await type(row).objects.delete_by_id(row.id)
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        with tenant_scope(tenant_id):
            await tenants.delete_by_id(tenant_id)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


async def test_tick_advances_a_task_in_the_workspace_zone(client: AsyncClient) -> None:
    """The runner must re-advance in the workspace zone, not the global one.

    Regression: it used to advance in `SCHEDULER_TIMEZONE`, so a task created
    at 09:00 Yekaterinburg jumped to 09:00 Moscow (2 h earlier) on its first run.
    """
    user, tenant_id = await _register_workspace(client, "TzTick", WORKSPACE_TZ)
    source = task = None
    try:
        source = await _make_source(tenant_id)
        # A fixed `now` keeps the expectation deterministic, and a start time in
        # the past makes the task genuinely due.
        now = datetime.now(timezone.utc)
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr=CRON,
                payload={},
                is_active=True,
                next_run_at=now - timedelta(minutes=1),
                tenant_id=tenant_id,
            )
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "update"):
            await AgentTaskManager().set_sources(task.id, [source.id])

        with tenant_scope(tenant_id), permission_scope(None):
            assert get_current_user() is None
            assert not has_permission(None, "agenttask", "create"), "arrange authority must not leak into tick"
            assert {row.id for row in await AgentTaskManager().get_due(now)} == {task.id}
            tenant = await tenants.get(id=tenant_id)
            stats = await runner_module.tick_tenant(tenant_id, now=now, tz=resolve_tz(tenant))
            assert not has_permission(None, "agenttask", "create")

        assert stats["enqueued"] == 1, "the due task must have been enqueued"
        with tenant_scope(tenant_id):
            refreshed = await AgentTask.objects.get(id=task.id)

        # 09:00 in the workspace zone, not 09:00 in the global default zone.
        expected = next_run_at(CRON, WORKSPACE_TZ, after=now)
        assert refreshed.next_run_at == expected
        # And explicitly not the value the old global-zone code produced.
        assert refreshed.next_run_at != next_run_at(CRON, "Europe/Moscow", after=now)
    finally:
        await _drop(user, tenant_id, task, source)


async def test_default_tasks_are_bootstrapped_in_the_workspace_zone(client: AsyncClient) -> None:
    """A default task must fire at 09:00 in the workspace's own zone."""
    user, tenant_id = await _register_workspace(client, "TzBoot", WORKSPACE_TZ)
    try:
        from app.tasks.bootstrap import ensure_default_tasks

        with tenant_scope(tenant_id):
            await ensure_default_tasks(tenant_id)
            rows = await AgentTask.objects.filter(name="daily-analyze")

        assert rows, "the default analyze task must exist"
        expected = next_run_at("0 9 * * *", WORKSPACE_TZ)
        assert rows[0].next_run_at.strftime("%H:%M") == expected.strftime("%H:%M")
        assert rows[0].next_run_at.strftime("%H:%M") != next_run_at("0 9 * * *", "Europe/Moscow").strftime("%H:%M")
    finally:
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "delete"):
            await AgentTask.objects.delete(tenant_id=tenant_id)
        await _drop(user, tenant_id)


async def test_workspace_tick_does_not_enqueue_or_advance_foreign_due_tasks(client: AsyncClient) -> None:
    """Deterministically reproduce foreign due rows without relying on leaked data."""
    user = other = own_task = foreign_task = None
    tenant_id = other_tenant_id = None
    now = datetime.now(timezone.utc)

    async def due_prune(target_id):
        with tenant_scope(target_id), service_permission_scope("agenttask", "create"):
            return await AgentTask.objects.create(
                name=_name("scope-task"),
                job_type="prune",
                cron_expr=CRON,
                payload={},
                is_active=True,
                next_run_at=now - timedelta(minutes=1),
                tenant_id=target_id,
            )

    try:
        user, tenant_id = await _register_workspace(client, "TzBound", WORKSPACE_TZ)
        async with await _client() as other_client:
            other, other_tenant_id = await _register_workspace(other_client, "TzForeign", "Europe/Moscow")
        assert tenant_id != other_tenant_id
        own_task = await due_prune(tenant_id)
        foreign_task = await due_prune(other_tenant_id)
        foreign_before = (foreign_task.next_run_at, foreign_task.last_run_at, foreign_task.last_status)

        with tenant_scope(tenant_id), permission_scope(None):
            assert get_current_user() is None
            assert not has_permission(None, "agenttask", "create")
            assert {row.id for row in await AgentTaskManager().get_due(now)} == {own_task.id}
            stats = await runner_module.tick_tenant(tenant_id, now=now, tz=WORKSPACE_TZ)
            assert stats == {"due": 1, "enqueued": 1, "failed": 0}
            refreshed = await AgentTask.objects.get(id=own_task.id)
            assert refreshed.next_run_at == next_run_at(CRON, WORKSPACE_TZ, after=now)
            jobs = await Job.objects.filter(agent_task_id=own_task.id)
            assert len(jobs) == 1 and jobs[0].tenant_id == tenant_id
            assert not has_permission(None, "agenttask", "create")
        with tenant_scope(other_tenant_id):
            untouched = await AgentTask.objects.get(id=foreign_task.id)
            assert (untouched.next_run_at, untouched.last_run_at, untouched.last_status) == foreign_before
            assert not await Job.objects.filter(agent_task_id=foreign_task.id)
    finally:
        await _drop(user, tenant_id, own_task)
        await _drop(other, other_tenant_id, foreign_task)
