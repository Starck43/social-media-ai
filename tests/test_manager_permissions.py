"""Manager write protection: Phase 5 pins enforcement on three managers.

`AgentTaskManager`, `AgentScenarioManager` and `SourceManager` gate their
user-facing writes with `@require_permission` from `app/core/permissions.py`
(the same predicate the API/console/web use: `User.has_perm_for()`).
Bookkeeping writes (`mark_triggered`, `record_result`, `update_last_checked`)
stay unchecked — the scheduler/collector/worker run without a user context.

Reads stay open (tenant scoping already isolates data); bypass and
legacy-None pass-through are verified per manager.
"""

import secrets

import pytest

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Role, User
from app.models.managers.agent_scenario_manager import AgentScenarioManager
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.source_manager import SourceManager
from app.types import UserRoleType

TASK_WRITE_METHODS = ("create", "update_by_id", "delete_by_id", "set_sources", "add_sources")
SCENARIO_WRITE_METHODS = ("create", "create_scenario", "update_by_id", "update_scenario_activity", "delete_by_id")
SOURCE_WRITE_METHODS = ("create", "create_source", "update_by_id", "delete_by_id")


async def _role(codename: str) -> Role:
    return await Role.objects.get(codename=UserRoleType[codename].name)


async def _user(role: Role) -> User:
    username = f"mgr{secrets.token_hex(4)}"
    return await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="secret-password-1",
        role_id=role.id,
    )


async def _loaded(user: User) -> User:
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


async def _viewer() -> User:
    """VIEWER has source.view only — any create/update/delete must be denied."""
    return await _loaded(await _user(await _role("VIEWER")))


async def _workspace() -> int:
    from app.core.config import settings
    from app.models import Tenant

    tenant = await Tenant.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
    assert tenant is not None
    return tenant.id


# --- AgentTaskManager --------------------------------------------------


async def test_task_writes_deny_viewer_allow_admin_and_pass_bypass():
    from app.models import AgentTask

    viewer = await _viewer()
    admin = await _loaded(await _user(await _role("ADMIN")))

    with tenant_scope(await _workspace()), permission_scope(viewer):
        with pytest.raises(PermissionDeniedError):
            await AgentTask.objects.create(name="nope", cron_expr="@once", job_type="digest")
        with pytest.raises(PermissionDeniedError):
            await AgentTaskManager().set_sources(1, [])
        with pytest.raises(PermissionDeniedError):
            await AgentTaskManager().add_sources(1, [])

    with tenant_scope(await _workspace()), permission_scope(admin):
        task = await AgentTask.objects.create(name=f"ok-{secrets.token_hex(3)}", cron_expr="@once", job_type="digest")
        assert task.id
        assert await AgentTask.objects.update_by_id(task.id, is_active=False) is not None
        assert await AgentTaskManager().set_sources(task.id, []) == 0
        assert await AgentTaskManager().add_sources(task.id, []) == 0
        assert await AgentTask.objects.delete_by_id(task.id) is True

    # Bypass (CLI/scheduler) and legacy None-user (jobs) never deny.
    with tenant_scope(bypass=True):
        task = await AgentTask.objects.create(name=f"sys-{secrets.token_hex(3)}", cron_expr="@once", job_type="digest")
        assert await AgentTask.objects.delete_by_id(task.id) is True
    with tenant_scope(await _workspace()), permission_scope(None):
        task = await AgentTask.objects.create(name=f"job-{secrets.token_hex(3)}", cron_expr="@once", job_type="digest")
        assert await AgentTask.objects.delete_by_id(task.id) is True


async def test_task_mark_triggered_and_record_result_skip_checks():
    from datetime import datetime, timezone

    from app.models import AgentTask

    viewer = await _viewer()
    tid = await _workspace()
    with tenant_scope(bypass=True):
        task = await AgentTask.objects.create(name=f"rec-{secrets.token_hex(3)}", cron_expr="@once", job_type="digest")
    with tenant_scope(tid), permission_scope(viewer):
        # No PermissionDeniedError even though viewer lacks agenttask.update.
        await AgentTaskManager().mark_triggered(task.id, datetime.now(timezone.utc))
        await AgentTaskManager().record_result(task.id, "ok")
    with tenant_scope(bypass=True):
        assert await AgentTask.objects.delete_by_id(task.id) is True


async def test_task_reads_stay_open():
    from app.models import AgentTask

    viewer = await _viewer()
    with tenant_scope(await _workspace()), permission_scope(viewer):
        assert isinstance(await AgentTaskManager().get_active(), list)
        assert isinstance(await AgentTaskManager().get_due(), list)


# --- AgentScenarioManager ----------------------------------------------


async def test_scenario_writes_deny_viewer_allow_admin_and_pass_bypass():
    from app.models import AgentScenario

    viewer = await _viewer()
    admin = await _loaded(await _user(await _role("ADMIN")))

    with tenant_scope(await _workspace()), permission_scope(viewer):
        with pytest.raises(PermissionDeniedError):
            await AgentScenario.objects.create(name="nope")
        with pytest.raises(PermissionDeniedError):
            await AgentScenarioManager().create_scenario(name="nope2")
        with pytest.raises(PermissionDeniedError):
            await AgentScenario.objects.update_by_id(1, is_active=False)
        with pytest.raises(PermissionDeniedError):
            await AgentScenarioManager().update_scenario_activity(1, False)
        with pytest.raises(PermissionDeniedError):
            await AgentScenario.objects.delete_by_id(1)

    with tenant_scope(await _workspace()), permission_scope(admin):
        row = await AgentScenario.objects.create(name=f"ok-{secrets.token_hex(3)}")
        assert row.id
        assert await AgentScenarioManager().update_scenario_activity(row.id, False) is not None
        assert await AgentScenario.objects.delete_by_id(row.id) is True

    with tenant_scope(bypass=True):
        row = await AgentScenario.objects.create(name=f"sys-{secrets.token_hex(3)}")
        assert await AgentScenario.objects.delete_by_id(row.id) is True
    with tenant_scope(await _workspace()), permission_scope(None):
        row = await AgentScenario.objects.create(name=f"job-{secrets.token_hex(3)}")
        assert await AgentScenario.objects.delete_by_id(row.id) is True


async def test_scenario_reads_stay_open():
    from app.models import AgentScenario

    viewer = await _viewer()
    with tenant_scope(await _workspace()), permission_scope(viewer):
        assert isinstance(await AgentScenario.objects.get_active_scenarios(), list)


# --- SourceManager -----------------------------------------------------


async def _platform_id() -> int:
    from app.models import Platform

    platform = await Platform.objects.get(platform_type="telegram")
    assert platform is not None
    return platform.id


async def test_source_writes_deny_viewer_allow_manager_and_pass_bypass():
    from app.models import Source
    from app.types import SourceType

    viewer = await _viewer()
    manager = await _loaded(await _user(await _role("MANAGER")))
    platform_id = await _platform_id()

    with tenant_scope(await _workspace()), permission_scope(viewer):
        with pytest.raises(PermissionDeniedError):
            await Source.objects.create(
                platform_id=platform_id,
                source_type=SourceType.CHANNEL,
                external_id=f"nope-{secrets.token_hex(3)}",
                name="nope",
            )
        with pytest.raises(PermissionDeniedError):
            await SourceManager().create_source(
                platform_id=platform_id, source_type=SourceType.CHANNEL, external_id=f"nope2-{secrets.token_hex(3)}"
            )
        with pytest.raises(PermissionDeniedError):
            await Source.objects.update_by_id(1, is_active=False)
        with pytest.raises(PermissionDeniedError):
            await Source.objects.delete_by_id(1)

    with tenant_scope(await _workspace()), permission_scope(manager):
        row = await SourceManager().create_source(
            platform_id=platform_id,
            source_type=SourceType.CHANNEL,
            external_id=f"ext-{secrets.token_hex(4)}",
        )
        assert row.id
        assert await Source.objects.update_by_id(row.id, is_active=False) is not None
        assert await Source.objects.delete_by_id(row.id) is True

    with tenant_scope(bypass=True):
        row = await Source.objects.create(
            platform_id=platform_id,
            source_type=SourceType.CHANNEL,
            external_id=f"sys-{secrets.token_hex(4)}",
            name="sys",
        )
        assert await Source.objects.delete_by_id(row.id) is True


async def test_source_bookkeeping_stays_unchecked():
    """Collector/ingest watermark writes run without a user context."""
    from app.models import Source
    from app.types import SourceType

    viewer = await _viewer()
    tid = await _workspace()
    platform_id = await _platform_id()
    with tenant_scope(bypass=True):
        row = await Source.objects.create(
            platform_id=platform_id,
            source_type=SourceType.CHANNEL,
            external_id=f"wm-{secrets.token_hex(4)}",
            name="wm",
        )
    with tenant_scope(tid), permission_scope(viewer):
        updated = await Source.objects.update_last_checked(row.id)
        assert updated is not None and updated.last_checked is not None
    with tenant_scope(bypass=True):
        assert await Source.objects.delete_by_id(row.id) is True


async def test_source_reads_stay_open():
    from app.models import Source

    viewer = await _viewer()
    with tenant_scope(await _workspace()), permission_scope(viewer):
        assert isinstance(await Source.objects.get_active_sources(), list)
        assert isinstance(await Source.objects.get_stats(), dict)


# --- method inventory --------------------------------------------------


def _is_gated(fn) -> bool:
    """True when `fn` is wrapped by `@require_permission` (unwraps functools chain)."""
    seen = fn
    while seen is not None:
        if getattr(seen, "__permission_gate__", False):
            return True
        seen = getattr(seen, "__wrapped__", None)
    return False


def test_gated_method_inventory():
    """Every method in the inventory exists and carries the decorator."""
    for manager_cls, methods in (
        (AgentTaskManager, TASK_WRITE_METHODS),
        (AgentScenarioManager, SCENARIO_WRITE_METHODS),
        (SourceManager, SOURCE_WRITE_METHODS),
    ):
        for name in methods:
            fn = getattr(manager_cls, name)
            assert callable(fn), f"{manager_cls.__name__}.{name} is missing"
            assert _is_gated(fn), f"{manager_cls.__name__}.{name} is not gated"
