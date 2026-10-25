"""Real home acceptance on the existing approved target, without DB bootstrap.

Run only through Kilo/pare-test with --noconftest. Resolve the test target before
application imports; create and remove only this module's own synthetic rows.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

# Make the project root importable when pytest runs as a script (pare-test).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from sqlalchemy import event, text

from scripts.setup_test_db import (
    check_existing_schema,
    resolve_and_redirect,
    resolve_test_schema,
    resolve_test_url,
)

_APPROVED_TARGET = ("localhost", 5432, "/social_manager", "test_schema")


def _resolve_approved_target() -> tuple[str, str]:
    """Return the approved (target_url, test_schema), redirecting only once.

    The standalone no-conftest run resolves and redirects here. Under pytest the
    conftest module already redirected the process to the same approved target,
    so a second resolve_and_redirect would reject it as the working schema.
    Detect that exact redirected state and reuse it instead.
    """

    def _identity(url: str) -> tuple:
        endpoint = urlsplit(url)
        return endpoint.hostname, endpoint.port or 5432, endpoint.path

    test_url = resolve_test_url()
    working_url = os.environ.get("POSTGRES_URL", "").strip()
    test_schema = resolve_test_schema()
    redirect_already_applied = (
        bool(test_url)
        and bool(working_url)
        and test_schema == _APPROVED_TARGET[3]
        and _identity(test_url) == _APPROVED_TARGET[:3]
        and _identity(working_url) == _APPROVED_TARGET[:3]
        and os.environ.get("DB_SCHEMA", "").strip() == _APPROVED_TARGET[3]
    )
    if redirect_already_applied:
        return test_url, test_schema
    _, redirected_test_url, redirected_schema = resolve_and_redirect()
    return redirected_test_url, redirected_schema


_target, _schema = _resolve_approved_target()
_endpoint = urlsplit(_target)
if (_endpoint.hostname, _endpoint.port or 5432, _endpoint.path, _schema) != _APPROVED_TARGET:
    raise RuntimeError("Home acceptance requires the owner-approved existing test target")

from app.core.config import settings  # noqa: E402
from app.core.database import async_engine  # noqa: E402
from app.core.permissions import operator_permission_scope  # noqa: E402
from app.core.tenant_context import tenant_scope  # noqa: E402
from app.models import (  # noqa: E402
    AgentScenario,
    AgentTask,
    AIAnalytics,
    DigestRun,
    ModelType,
    Permission,
    Platform,
    Role,
    Source,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.models.agent_task import agent_task_sources  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.managers.agent_task_manager import AgentTaskManager  # noqa: E402
from app.models.managers.tenant_manager import TenantUserManager, tenants  # noqa: E402
from app.models.role import role_permission  # noqa: E402
from app.types import ActionType, PlatformRoleType, SourceType, WorkspaceRoleType  # noqa: E402
from app.web import dashboard  # noqa: E402
from tests.web.test_web_settings import PASSWORD, _client, _login, _name, _register  # noqa: E402

pytestmark = pytest.mark.tenancy


@pytest.fixture(scope="module", autouse=True)
async def existing_target():
    assert settings.DB_SCHEMA == _schema
    try:
        async with async_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                # Unwired subsystems are not prerequisites of home acceptance.
                # Keep the shared guards on every existing table, and require
                # the actual auth/home dependencies before creating test data.
                await check_existing_schema(connection, Base.metadata, _schema)
                present = {
                    row[0]
                    for row in await connection.execute(
                        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
                        {"schema": _schema},
                    )
                }
                required = {
                    model.__table__.name
                    for model in (
                        User,
                        Role,
                        Permission,
                        ModelType,
                        Tenant,
                        TenantUser,
                        Platform,
                        Source,
                        AIAnalytics,
                        DigestRun,
                        AgentTask,
                    )
                } | {role_permission.name, agent_task_sources.name}
                assert not required - present, f"Missing home/auth tables: {sorted(required - present)}"
        yield
    finally:
        await async_engine.dispose()


@pytest.fixture
async def home():
    """Two private workspaces, an owner and owned cleanup even on assertions."""
    users, tenant_ids, sources, inactive_sources, tasks, digests, scenarios = [], [], [], [], [], [], []
    platform = None
    try:
        for prefix in ("HomeAccept", "HomeForeign"):
            async with await _client() as client:
                user, tenant_id = await _register(client, prefix)
                users.append(user)
                tenant_ids.append(tenant_id)
        platform = await Platform.objects.create(
            name=_name("HomePlatform"), platform_type="telegram", base_url="https://example.invalid", params={}
        )
        with tenant_scope(bypass=True), operator_permission_scope():
            for index, tenant_id in enumerate(tenant_ids):
                next_run = datetime.now(timezone.utc) + timedelta(hours=1)
                source = await Source.objects.create(
                    tenant_id=tenant_id,
                    platform_id=platform.id,
                    name=_name(f"HomeSource{index}"),
                    source_type=SourceType.CHANNEL.name,
                    external_id=_name("home-source"),
                    is_active=True,
                )
                sources.append(source)
                inactive_source = await Source.objects.create(
                    tenant_id=tenant_id,
                    platform_id=platform.id,
                    name=_name(f"InactiveHomeSource{index}"),
                    source_type=SourceType.CHANNEL.name,
                    external_id=_name("inactive-home-source"),
                    is_active=False,
                )
                inactive_sources.append(inactive_source)
                for offset in range(6):
                    await AIAnalytics.objects.create(
                        tenant_id=tenant_id,
                        source_id=source.id,
                        analysis_date=date.today() - timedelta(days=offset),
                        summary_data={
                            "analysis_title": f"{source.name}-result-{offset}",
                            "analysis_summary": "<script>homeStoredText()</script>",
                            "analysis_metadata": {"analysis_complete": False},
                        },
                    )
                digests.append(
                    await DigestRun.objects.create(
                        tenant_id=tenant_id,
                        period="week",
                        period_start=date.today() - timedelta(days=7),
                        period_end=date.today(),
                        channel="telegram",
                        status="failed",
                        error="partial_delivery_requires_review",
                        content=f"{source.name}-digest-content",
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
                    )
                )
                for offset in range(4):
                    task = await AgentTask.objects.create(
                        tenant_id=tenant_id,
                        name=f"{source.name}-task-{offset}",
                        job_type="collect",
                        cron_expr="0 9 * * *",
                        is_active=True,
                        next_run_at=next_run,
                    )
                    tasks.append(task)
                paused = await AgentTask.objects.create(
                    tenant_id=tenant_id,
                    name=f"{source.name}-paused",
                    job_type="collect",
                    cron_expr="0 9 * * *",
                    is_active=False,
                    next_run_at=next_run - timedelta(minutes=10),
                )
                tasks.append(paused)
                once = await AgentTask.objects.create(
                    tenant_id=tenant_id,
                    name=f"{source.name}-completed-once",
                    job_type="digest",
                    cron_expr="@once",
                    is_active=False,
                    next_run_at=None,
                )
                tasks.append(once)
                inactive_scenario = await AgentScenario.objects.create(
                    tenant_id=tenant_id,
                    name=_name("InactiveScenario"),
                    is_active=False,
                )
                scenarios.append(inactive_scenario)
                scenario_task = await AgentTask.objects.create(
                    tenant_id=tenant_id,
                    name=f"{source.name}-inactive-scenario",
                    job_type="digest",
                    cron_expr="0 9 * * *",
                    is_active=True,
                    next_run_at=next_run - timedelta(minutes=5),
                    agent_scenario_id=inactive_scenario.id,
                )
                tasks.append(scenario_task)
                linked_task = await AgentTask.objects.create(
                    tenant_id=tenant_id,
                    name=f"{source.name}-inactive-linked-source",
                    job_type="collect",
                    cron_expr="0 9 * * *",
                    is_active=True,
                    next_run_at=next_run - timedelta(minutes=2),
                )
                tasks.append(linked_task)
                await AgentTaskManager().set_sources(linked_task.id, [inactive_source.id])
        yield users, tenant_ids, sources
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for task in tasks:
                await AgentTask.objects.delete_by_id(task.id)
            for scenario in scenarios:
                await AgentScenario.objects.delete_by_id(scenario.id)
            for digest in digests:
                await DigestRun.objects.delete_by_id(digest.id)
            for source in sources:
                await AIAnalytics.objects.filter(source_id=source.id).delete()
                await Source.objects.delete_by_id(source.id)
            for source in inactive_sources:
                await Source.objects.delete_by_id(source.id)
            for user in users:
                await User.objects.delete_user(user.id)
            for tenant_id in tenant_ids:
                await tenants.delete_by_id(tenant_id)
            if platform is not None:
                await Platform.objects.delete_by_id(platform.id)


async def actor(home, level):
    users, tenant_ids, _ = home
    if level == "owner":
        return users[0]
    user_role = await Role.objects.get(codename=PlatformRoleType.USER.value)
    if level == "admin":
        user_role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
    viewer = user_role
    assert viewer is not None, "Existing Viewer reference role is required; never seed here"
    username = _name("HomeActor")
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role_id=viewer.id,
    )
    users.append(user)
    role_code = WorkspaceRoleType.VIEWER.value if level == "admin" else WorkspaceRoleType(level).value
    role = await WorkspaceRole.objects.get(codename=role_code)
    assert role is not None, "Existing membership role is required; never seed here"
    await TenantUserManager().add_web_member(tenant_id=tenant_ids[0], user_id=user.id, role_id=role.id)
    return user


@pytest.mark.parametrize("level", ["viewer", "manager", "owner", "admin"])
async def test_real_home_role_matrix_and_bounded_private_results(home, level):
    user = await actor(home, level)
    _, tenant_ids, sources = home
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get(f"/app/?tenant_id={tenant_ids[0]}")
        assert page.status_code == 200
        assert sources[0].name in page.text
        assert sources[1].name not in page.text
        assert page.text.count('class="ui-home-result ') == 5
        assert f"{sources[0].name}-digest-content" in page.text
        assert "Доставка: Доставлен частично" in page.text
        assert "&lt;script&gt;homeStoredText()&lt;/script&gt;" in page.text
        assert "<script>homeStoredText()</script>" not in page.text
        assert 'data-coverage="incomplete"' in page.text
        has_tasks = level in {"owner", "manager"}
        assert ("data-home-tasks" in page.text) == has_tasks
        if has_tasks:
            assert page.text.count('class="ui-home-task ') == 3
            assert "Ближайшие запуски" in page.text
            assert f"{sources[0].name}-task-0" in page.text
            assert f"{sources[0].name}-task-3" not in page.text
            assert f"{sources[0].name}-paused" not in page.text
            assert f"{sources[0].name}-inactive-scenario" not in page.text
            assert f"{sources[0].name}-inactive-linked-source" not in page.text
            assert f"{sources[0].name}-completed-once" not in page.text
        assert "run-now" not in page.text and "data-home-next-step" not in page.text


async def test_non_operator_cannot_widen_home_with_foreign_workspace_filter(home):
    user = await actor(home, "manager")
    _, tenant_ids, sources = home
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get(f"/app/?tenant_id={tenant_ids[1]}")
        assert page.status_code == 200
        assert sources[0].name in page.text and sources[1].name not in page.text


async def test_platform_admin_cannot_use_home_filter_to_cross_workspaces(home):
    user = await actor(home, "admin")
    _, tenant_ids, sources = home
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get(f"/app/?tenant_id={tenant_ids[1]}")
        assert page.status_code == 200
        assert sources[0].name in page.text and sources[1].name not in page.text


async def test_missing_membership_rights_hides_data_and_skips_home_queries(home, monkeypatch):
    user = await actor(home, "viewer")
    _, tenant_ids, sources = home
    membership = (await TenantUserManager().web_memberships(user.id))[0]
    await TenantUserManager().update_by_id(membership.id, role_id=None)
    results = AsyncMock(wraps=dashboard._recent_results)
    tasks = AsyncMock(wraps=dashboard._recent_tasks)
    monkeypatch.setattr(dashboard, "_recent_results", results)
    monkeypatch.setattr(dashboard, "_recent_tasks", tasks)
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get(f"/app/?tenant_id={tenant_ids[0]}")
        assert page.status_code == 200
        assert all(source.name not in page.text for source in sources)
        assert "data-home-tasks" not in page.text and "data-home-next-step" not in page.text
        assert 'href="/app/analytics' not in page.text and 'href="/app/sources' not in page.text
        results.assert_not_awaited()
        tasks.assert_not_awaited()
        assert (await client.get("/app/sources")).status_code == 403


@pytest.mark.parametrize(
    "role_code, expected_tasks",
    [(WorkspaceRoleType.VIEWER.value, 0), (WorkspaceRoleType.MANAGER.value, 3)],
)
async def test_fixed_workspace_role_presets_bound_home_queries(home, role_code, expected_tasks):
    user = await actor(home, "viewer")
    membership = (await TenantUserManager().web_memberships(user.id))[0]
    original_role_id = membership.role_id
    source_reads = []

    def observe_sources(connection, cursor, statement, parameters, context, executemany):
        sql = statement.replace('"', "").lower()
        if re.search(r"\b(?:from|join)\s+" + re.escape(settings.DB_SCHEMA.lower()) + r"\.sources\b", sql):
            source_reads.append(True)

    try:
        role = await WorkspaceRole.objects.get(codename=role_code)
        assert role is not None, "Fixed workspace role is seeded"
        await TenantUserManager().update_by_id(membership.id, role_id=role.id)
        async with await _client() as client:
            await _login(client, user.username)
            event.listen(async_engine.sync_engine, "before_cursor_execute", observe_sources)
            try:
                page = await client.get("/app/")
            finally:
                event.remove(async_engine.sync_engine, "before_cursor_execute", observe_sources)
            assert page.status_code == 200
            assert page.text.count('class="ui-home-task ') == expected_tasks
            assert source_reads
            assert "Статус расписаний с источниками неизвестен" not in page.text
            assert 'href="/app/sources' in page.text
            assert (await client.get("/app/sources")).status_code == 200
    finally:
        await TenantUserManager().update_by_id(membership.id, role_id=original_role_id)


@pytest.mark.parametrize(
    "role_id, role_name, owner",
    [(None, None, False), (None, "owner", False), (1, None, False), (1, "viewer", False), (1, "owner", True)],
)
def test_ownership_requires_an_explicit_resolved_membership_role(role_id, role_name, owner):
    membership = TenantUser(role_id=role_id)
    membership.role = WorkspaceRole(id=role_id or 1, codename=role_name) if role_name is not None else None
    assert membership.is_owner is owner


async def test_empty_owner_home_has_one_permitted_setup_step(home):
    users, _, sources = home
    with tenant_scope(bypass=True), operator_permission_scope():
        await AIAnalytics.objects.filter(source_id=sources[0].id).delete()
        await DigestRun.objects.filter(tenant_id=sources[0].tenant_id).delete()
        await AgentTask.objects.filter(tenant_id=sources[0].tenant_id).delete()
        await Source.objects.filter(tenant_id=sources[0].tenant_id).delete()
    async with await _client() as client:
        await _login(client, users[0].username)
        page = await client.get("/app/")
        assert page.status_code == 200
        assert "Результатов пока нет" in page.text
        assert page.text.count("data-home-next-step") == 1
        assert "Подключите первый источник" in page.text
