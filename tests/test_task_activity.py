"""Task effective-activity: a task whose scenario is deactivated or that has no
active source to operate on is not "active" — it is hidden from the queue
(`get_due`) and shown/activated in the web UI only when the conditions hold.

Covers:
- `AgentTaskManager.get_due` skips tasks bound to an inactive scenario;
- `get_due` skips tasks whose linked sources are all inactive;
- `get_due` keeps a task that has at least one active source;
- the web create flow starts a task inactive when the conditions are unmet;
- the web toggle refuses to activate a task that violates the conditions;
- the `/app/tasks` list reflects effective activity.

Real PostgreSQL (same policy as the rest of the suite).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentScenario, AgentTask, Platform, Source, User
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import SourceType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _make_source(tenant_id: int, name: str, external_id: str, is_active: bool = True) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(bypass=True):
        return await Source.objects.create(
            name=name,
            platform_id=platform.id,
            external_id=external_id,
            source_type=SourceType.USER,
            is_active=is_active,
            params={},
            tenant_id=tenant_id,
        )


async def _make_scenario(tenant_id: int, is_active: bool = True) -> AgentScenario:
    with tenant_scope(bypass=True):
        return await AgentScenario.objects.create(name=_name("scenario"), is_active=is_active, tenant_id=tenant_id)


async def _make_task(tenant_id: int, is_active: bool = True) -> AgentTask:
    with tenant_scope(bypass=True):
        return await AgentTask.objects.create(
            name=_name("task"),
            job_type="collect",
            cron_expr="0 * * * *",
            timezone="Europe/Moscow",
            payload={},
            is_active=is_active,
            tenant_id=tenant_id,
        )


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=1)


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


def _due_ids(tasks) -> set[int]:
    return {t.id for t in tasks}


# ── queue (get_due) ────────────────────────────────────────────────────────


async def test_get_due_skips_task_with_inactive_scenario(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "ScenInactive")
    task = scenario = None
    try:
        scenario = await _make_scenario(tenant_id, is_active=False)
        source = await _make_source(tenant_id, _name("src"), _name("ext"), is_active=True)
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr="@once",
                timezone="Europe/Moscow",
                payload={},
                is_active=True,
                agent_scenario_id=scenario.id,
                tenant_id=tenant_id,
                next_run_at=_past(),
            )
            await AgentTaskManager().set_sources(task.id, [source.id])
            due = await AgentTaskManager().get_due()
        assert task.id not in _due_ids(due)
    finally:
        if task is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=task.id)
        if scenario is not None:
            with tenant_scope(bypass=True):
                await AgentScenario.objects.delete(id=scenario.id)
        await _drop(user, tenant_id)


async def test_get_due_skips_task_with_no_active_linked_sources(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "SrcInactive")
    task = None
    try:
        source = await _make_source(tenant_id, _name("src"), _name("ext"), is_active=False)
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr="@once",
                timezone="Europe/Moscow",
                payload={},
                is_active=True,
                tenant_id=tenant_id,
                next_run_at=_past(),
            )
            await AgentTaskManager().set_sources(task.id, [source.id])
            due = await AgentTaskManager().get_due()
        assert task.id not in _due_ids(due)
    finally:
        if task is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=task.id)
        await _drop(user, tenant_id)


async def test_get_due_keeps_task_with_active_source(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "SrcActive")
    task = None
    try:
        source = await _make_source(tenant_id, _name("src"), _name("ext"), is_active=True)
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr="@once",
                timezone="Europe/Moscow",
                payload={},
                is_active=True,
                tenant_id=tenant_id,
                next_run_at=_past(),
            )
            await AgentTaskManager().set_sources(task.id, [source.id])
            due = await AgentTaskManager().get_due()
        assert task.id in _due_ids(due)
    finally:
        if task is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=task.id)
        await _drop(user, tenant_id)


# ── workspace/memory-level types need no source ────────────────────────────


async def test_get_due_keeps_reflect_task_without_any_source(client: AsyncClient) -> None:
    """A reflect task needs no source and stays active even with no active source."""
    user, tenant_id = await _register(client, "ReflectNoSrc")
    task = None
    try:
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="reflect",
                cron_expr="@once",
                timezone="Europe/Moscow",
                payload={},
                is_active=True,
                tenant_id=tenant_id,
                next_run_at=_past(),
            )
            due = await AgentTaskManager().get_due()
        assert task.id in _due_ids(due)
    finally:
        if task is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=task.id)
        await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_get_due_skips_collect_task_with_no_active_source_in_workspace(client: AsyncClient) -> None:
    """A source-based collect task with no linked sources still needs an active source."""
    user, tenant_id = await _register(client, "CollectNoSrc")
    task = None
    try:
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("task"),
                job_type="collect",
                cron_expr="@once",
                timezone="Europe/Moscow",
                payload={},
                is_active=True,
                tenant_id=tenant_id,
                next_run_at=_past(),
            )
        with tenant_scope(tenant_id):
            due = await AgentTaskManager().get_due()
        assert task.id not in _due_ids(due)
    finally:
        if task is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=task.id)
        await _drop(user, tenant_id)


# ── web create: activation requires conditions ─────────────────────────────


async def test_create_reflect_task_starts_active_without_sources(client: AsyncClient) -> None:
    """A reflect task needs no source, so it can start active with none selected."""
    async with await _client() as c:
        user, tenant_id = await _register(c, "CreateReflect")
        name = _name("task")
        task_id = None
        try:
            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(
                "/app/tasks",
                data={
                    "name": name,
                    "job_type": "reflect",
                    "cron_custom": "@once",
                    "_csrf": csrf,
                },
            )
            assert resp.status_code == 200
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.get(name=name)
                task_id = task.id
                assert task.is_active is True
        finally:
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_create_collect_task_without_sources_starts_inactive(client: AsyncClient) -> None:
    """A source-based collect task with no active source starts inactive with a warning."""
    async with await _client() as c:
        user, tenant_id = await _register(c, "CreateCollectNoSrc")
        name = _name("task")
        task_id = None
        try:
            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(
                "/app/tasks",
                data={
                    "name": name,
                    "job_type": "collect",
                    "cron_custom": "@once",
                    "_csrf": csrf,
                },
            )
            assert resp.status_code == 200
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.get(name=name)
                task_id = task.id
                assert task.is_active is False
            assert "создана неактивной" in resp.text
        finally:
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await _drop(user, tenant_id)


async def test_create_with_inactive_scenario_starts_inactive(client: AsyncClient) -> None:
    async with await _client() as c:
        user, tenant_id = await _register(c, "CreateInactive")
        scenario = await _make_scenario(tenant_id, is_active=False)
        name = _name("task")
        task_id = None
        try:
            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(
                "/app/tasks",
                data={
                    "name": name,
                    "job_type": "collect",
                    "cron_custom": "@once",
                    "scenario_id": str(scenario.id),
                    "_csrf": csrf,
                },
            )
            assert resp.status_code == 200
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.get(name=name)
                task_id = task.id
                assert task.is_active is False
            assert "создана неактивной" in resp.text
        finally:
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            if scenario is not None:
                with tenant_scope(bypass=True):
                    await AgentScenario.objects.delete(id=scenario.id)
            await _drop(user, tenant_id)


async def test_toggle_refuses_activation_with_inactive_scenario(client: AsyncClient) -> None:
    async with await _client() as c:
        user, tenant_id = await _register(c, "ToggleBlocked")
        scenario = await _make_scenario(tenant_id, is_active=False)
        source = await _make_source(tenant_id, _name("src"), _name("ext"), is_active=True)
        task = None
        try:
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.create(
                    name=_name("task"),
                    job_type="collect",
                    cron_expr="@once",
                    timezone="Europe/Moscow",
                    payload={},
                    is_active=False,
                    agent_scenario_id=scenario.id,
                    tenant_id=tenant_id,
                )
                await AgentTaskManager().set_sources(task.id, [source.id])

            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(f"/app/tasks/{task.id}/toggle", data={"_csrf": csrf})
            assert resp.status_code == 200
            with tenant_scope(bypass=True):
                refreshed = await AgentTask.objects.get(id=task.id)
                assert refreshed.is_active is False
            assert "не активирована" in resp.text
        finally:
            if task is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task.id)
            if scenario is not None:
                with tenant_scope(bypass=True):
                    await AgentScenario.objects.delete(id=scenario.id)
            await _drop(user, tenant_id)


async def test_list_marks_task_with_inactive_scenario_as_inactive(client: AsyncClient) -> None:
    async with await _client() as c:
        user, tenant_id = await _register(c, "ListInactive")
        scenario = await _make_scenario(tenant_id, is_active=False)
        source = await _make_source(tenant_id, _name("src"), _name("ext"), is_active=True)
        task = None
        try:
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.create(
                    name=_name("task"),
                    job_type="collect",
                    cron_expr="@once",
                    timezone="Europe/Moscow",
                    payload={},
                    is_active=True,
                    agent_scenario_id=scenario.id,
                    tenant_id=tenant_id,
                )
                await AgentTaskManager().set_sources(task.id, [source.id])
            resp = await c.get("/app/tasks")
            assert resp.status_code == 200
            # the row is rendered as inactive even though is_active is True
            assert "неактивна" in resp.text
        finally:
            if task is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task.id)
            if scenario is not None:
                with tenant_scope(bypass=True):
                    await AgentScenario.objects.delete(id=scenario.id)
            await _drop(user, tenant_id)
