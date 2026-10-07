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

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Platform, Source, User
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.tasks import runner as runner_module
from app.tasks.cron import next_run_at, resolve_tz
from app.types import SourceType

WORKSPACE_TZ = "Asia/Yekaterinburg"  # UTC+5, deliberately not Europe/Moscow
CRON = "0 9 * * *"


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
    with tenant_scope(bypass=True):
        await tenants.update_by_id(tenant_id, timezone=tz)
    return user, tenant_id


async def _make_source(tenant_id: int) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(bypass=True):
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
            with tenant_scope(bypass=True):
                await type(row).objects.delete(id=row.id)
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        with tenant_scope(bypass=True):
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
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr=CRON,
                payload={},
                is_active=True,
                next_run_at=now - timedelta(minutes=1),
                tenant_id=tenant_id,
            )
            await AgentTaskManager().set_sources(task.id, [source.id])

        with tenant_scope(tenant_id):
            tenant = await tenants.get(id=tenant_id)
            stats = await runner_module.tick_tenant(tenant_id, now=now, tz=resolve_tz(tenant))

        assert stats["enqueued"] == 1, "the due task must have been enqueued"
        with tenant_scope(bypass=True):
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
        with tenant_scope(bypass=True):
            await AgentTask.objects.delete(tenant_id=tenant_id)
        await _drop(user, tenant_id)
