"""Real PostgreSQL acceptance for the chat task tools and their source links.

The suite calls the registered task handlers against the real models, managers,
tenant guard and permission scopes. Nothing about persistence is doubled: the
created task, its payload, its schedule and its m2m source links are read back
from the database after every operation.

A foreign source is deliberately created in a second workspace. Under the real
tenant guard it is invisible to the owning workspace, so the write is refused
as "not found in this workspace" rather than leaking that the row exists.

The one injected fault is bounded to ``AgentTaskManager.set_sources`` after the
caller's row update has already been flushed. It proves the link write and the
task update share one transaction and roll back together.

Run strictly sequentially through the root conftest and the primary runner;
never with ``--noconftest`` or against a freshly prepared schema.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

# STOP before importing application models/engines if the normal redirect is absent.
_expected = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only pre-approved local social_manager/test_schema is allowed")

# isort: split
from sqlalchemy import select

from app.agent.toolset import tasks
from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models import AgentScenario, AgentTask, Platform, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenants
from app.tasks import cron as cron_module
from app.tasks.cron import resolve_tz
from app.types import SourceType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

_MISSING_SOURCE_ID = 2_147_483_000


def _load_dep(name: str, path: str):
    """Load a cross-directory helper module by path (repo idiom)."""
    import importlib.util

    filepath = Path(__file__).resolve().parents[2] / path
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if str(filepath.parent) not in sys.path:
        sys.path.insert(0, str(filepath.parent))
    spec.loader.exec_module(module)
    return module


fixtures = _load_dep("_dep_chat_task_access_scope", "tests/security/test_access_postgres_acceptance.py")


@dataclass(frozen=True)
class StoredTask:
    id: int
    cron_expr: str
    job_type: str
    payload: dict
    scenario_id: int | None
    is_active: bool
    next_run_at: datetime | None
    last_status: str | None
    source_ids: list[int]


@pytest.fixture
async def workspace():
    async with fixtures._workspace() as created:
        yield created


@pytest.fixture
async def two_workspaces():
    async with fixtures._workspace() as own, fixtures._workspace() as foreign:
        yield own, foreign


async def _owner(workspace) -> object:
    owner = await workspace.new_user()
    await workspace.add_web_member(owner, workspace.owner_role_id)
    return owner


async def _platform_id() -> int:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "STOP: missing seeded active platform; do not seed"
    return platform.id


def _name(label: str) -> str:
    return f"persist-{label}-{uuid4().hex[:10]}"


async def _source(workspace, owner, label: str) -> Source:
    platform_id = await _platform_id()
    with workspace.as_owner(owner):
        return await Source.objects.create(
            name=f"synthetic-source-{label}-{uuid4().hex[:8]}",
            platform_id=platform_id,
            external_id=f"synthetic-{uuid4().hex}",
            source_type=SourceType.GROUP,
            is_active=True,
        )


async def _scenario(workspace, owner, label: str) -> AgentScenario:
    with workspace.as_owner(owner):
        return await AgentScenario.objects.create(
            name=f"synthetic-scenario-{label}-{uuid4().hex[:8]}",
            analysis_types=[],
            content_types=[],
            scope={},
            is_active=True,
        )


async def _create_task(
    workspace,
    owner,
    *,
    name: str,
    job_type: str = "collect",
    source_ids: list[int] | None = None,
    scenario_id: int | None = None,
    start_date: str | None = "2026-01-01",
    end_date: str | None = "2026-01-31",
    force_refresh: bool = True,
    brands: list[str] | None = None,
    collection: dict | None = None,
) -> dict:
    with workspace.as_owner(owner):
        result = await tasks.task_add(
            name=name,
            cron="0 9 * * *",
            job_type=job_type,
            source_ids=source_ids,
            scenario_id=scenario_id,
            start_date=start_date,
            end_date=end_date,
            force_refresh=force_refresh,
            brands=brands,
            collection=collection,
        )
    assert result["status"] == "created", result
    return result


async def _snapshot(workspace, name: str) -> StoredTask:
    with tenant_scope(workspace.tenant_id):
        task = await AgentTask.objects.get(name=name)
        assert task is not None, f"task {name!r} is absent"
        source_ids = await AgentTaskManager().get_sources(task.id)
        return StoredTask(
            id=task.id,
            cron_expr=task.cron_expr,
            job_type=task.job_type,
            payload=deepcopy(task.payload),
            scenario_id=task.agent_scenario_id,
            is_active=task.is_active,
            next_run_at=task.next_run_at,
            last_status=task.last_status,
            source_ids=source_ids,
        )


def _display(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE)).strftime("%d.%m.%Y %H:%M")


async def _tz_name(workspace) -> str:
    tenant = await tenants.get(id=workspace.tenant_id)
    assert tenant is not None, "STOP: generated workspace is absent; do not seed"
    return resolve_tz(tenant)


async def test_task_list_reports_persisted_state_and_links(workspace) -> None:
    owner = await _owner(workspace)
    first = await _source(workspace, owner, "first")
    second = await _source(workspace, owner, "second")
    name = _name("list")
    requested = [first.id, second.id]

    with workspace.as_owner(owner):
        created = await tasks.task_add(
            name=name,
            cron="0 9 * * *",
            job_type="collect",
            source_ids=requested,
            start_date="2026-01-01",
        )
        rows = await tasks.task_list()

    assert created["status"] == "created"
    assert requested == [first.id, second.id]

    stored = await _snapshot(workspace, name)
    assert [row["name"] for row in rows] == [name]
    row = rows[0]
    assert row["cron"] == stored.cron_expr == "0 9 * * *"
    assert row["job_type"] == stored.job_type == "collect"
    assert row["active"] is True
    assert stored.is_active is True
    assert row["next_run_at"] == _display(stored.next_run_at)
    assert stored.next_run_at is not None
    assert row["last_status"] is None
    assert stored.source_ids == sorted([first.id, second.id])


async def test_omitted_update_settings_keep_stored_values(workspace) -> None:
    owner = await _owner(workspace)
    scenario = await _scenario(workspace, owner, "kept")
    source = await _source(workspace, owner, "kept")
    name = _name("omitted")
    await _create_task(
        workspace,
        owner,
        name=name,
        source_ids=[source.id],
        scenario_id=scenario.id,
        brands=["stored-brand"],
        collection={"limit": 12},
    )

    before = await _snapshot(workspace, name)
    with workspace.as_owner(owner):
        result = await tasks.task_update(name=name)
    after = await _snapshot(workspace, name)

    assert result["status"] == "updated"
    assert after == before
    assert after.payload["cli_dates"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    assert after.payload["force_refresh"] is True
    assert after.scenario_id == scenario.id
    assert after.source_ids == [source.id]
    assert after.payload["brands"] == ["stored-brand"]
    assert before.payload["collection"]["limit"] == 12
    assert after.payload["collection"] == before.payload["collection"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("start_date", "2026-01-10", {"start_date": "2026-01-10", "end_date": "2026-01-31"}),
        ("end_date", "2026-02-28", {"start_date": "2026-01-01", "end_date": "2026-02-28"}),
    ],
)
async def test_one_sided_date_bound_keeps_the_other(workspace, field, value, expected) -> None:
    owner = await _owner(workspace)
    name = _name("bound")
    await _create_task(workspace, owner, name=name)

    with workspace.as_owner(owner):
        result = await tasks.task_update(name=name, **{field: value})
    after = await _snapshot(workspace, name)

    assert result["status"] == "updated"
    assert after.payload["cli_dates"] == expected
    assert after.payload["force_refresh"] is True


async def test_start_date_after_stored_end_date_is_refused_without_a_write(workspace) -> None:
    owner = await _owner(workspace)
    source = await _source(workspace, owner, "invalid-window")
    name = _name("invalid-window")
    await _create_task(workspace, owner, name=name, source_ids=[source.id])
    before = await _snapshot(workspace, name)

    # Other mutable fields ride along, so a refusal that still wrote anything
    # would be visible in the reloaded row.
    with workspace.as_owner(owner):
        result = await tasks.task_update(
            name=name,
            start_date="2026-02-01",
            cron="0 10 * * *",
            brands=["must-not-persist"],
        )
    after = await _snapshot(workspace, name)

    assert "start_date не может быть позже end_date" in result["error"]
    assert after == before
    assert after.payload["cli_dates"] == {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    assert after.cron_expr == "0 9 * * *"
    assert after.source_ids == [source.id]


async def test_cron_expr_and_next_run_at_persist_together(workspace) -> None:
    owner = await _owner(workspace)
    name = _name("cron")
    await _create_task(workspace, owner, name=name, job_type="learn", start_date=None, end_date=None)

    before = await _snapshot(workspace, name)
    tz_name = await _tz_name(workspace)
    new_cron = "0 10 * * *"
    lower_bound = cron_module.next_run_at(new_cron, tz_name)

    with workspace.as_owner(owner):
        result = await tasks.task_update(name=name, cron=new_cron)
    upper_bound = cron_module.next_run_at(new_cron, tz_name)
    after = await _snapshot(workspace, name)

    assert result["status"] == "updated"
    assert after.cron_expr == new_cron
    assert after.next_run_at is not None
    assert after.next_run_at != before.next_run_at
    # The handler computed its occurrence between the two boundaries; a minute
    # rollover may pick the later one, never an unrelated value.
    assert after.next_run_at in {lower_bound, upper_bound}


async def test_clear_scenario_differs_from_omitted_field(workspace) -> None:
    owner = await _owner(workspace)
    first = await _scenario(workspace, owner, "first")
    second = await _scenario(workspace, owner, "second")
    name = _name("scenario")
    await _create_task(
        workspace,
        owner,
        name=name,
        job_type="analyze",
        scenario_id=first.id,
        start_date="2026-01-01",
    )

    with workspace.as_owner(owner):
        omitted = await tasks.task_update(name=name)
    assert omitted["status"] == "updated"
    assert (await _snapshot(workspace, name)).scenario_id == first.id

    with workspace.as_owner(owner):
        rebound = await tasks.task_update(name=name, scenario_id=second.id)
    assert rebound["status"] == "updated"
    assert (await _snapshot(workspace, name)).scenario_id == second.id

    with workspace.as_owner(owner):
        cleared = await tasks.task_update(name=name, clear_scenario=True)
    assert cleared["status"] == "updated"
    assert (await _snapshot(workspace, name)).scenario_id is None

    with workspace.as_owner(owner):
        refused = await tasks.task_update(name=name, clear_scenario=True, scenario_id=second.id)
    assert refused.get("error")
    assert (await _snapshot(workspace, name)).scenario_id is None


async def test_source_replacement_is_exact_including_empty(workspace) -> None:
    owner = await _owner(workspace)
    first = await _source(workspace, owner, "first")
    second = await _source(workspace, owner, "second")
    third = await _source(workspace, owner, "third")
    name = _name("sources")
    await _create_task(workspace, owner, name=name, source_ids=[first.id, second.id])

    exact = [second.id, third.id]
    brands = ["synthetic-brand"]
    collection = {"limit": 25}
    with workspace.as_owner(owner):
        result = await tasks.task_update(
            name=name,
            source_ids=exact,
            brands=brands,
            collection=collection,
        )
    after = await _snapshot(workspace, name)

    assert result["status"] == "updated"
    assert after.source_ids == sorted([second.id, third.id])
    assert after.payload["brands"] == ["synthetic-brand"]
    assert after.payload["collection"]["limit"] == 25
    # The caller's input containers are not mutated by the handler.
    assert exact == [second.id, third.id]
    assert brands == ["synthetic-brand"]
    assert collection == {"limit": 25}

    with workspace.as_owner(owner):
        await tasks.task_update(name=name, source_ids=[])
    assert (await _snapshot(workspace, name)).source_ids == []


async def test_unavailable_sources_leave_task_and_links_unchanged(two_workspaces) -> None:
    own, foreign = two_workspaces
    owner = await _owner(own)
    foreign_owner = await _owner(foreign)
    source = await _source(own, owner, "own")
    foreign_source = await _source(foreign, foreign_owner, "foreign")
    name = _name("bad-source")
    await _create_task(own, owner, name=name, source_ids=[source.id])
    before = await _snapshot(own, name)

    for source_ids in ([_MISSING_SOURCE_ID], [foreign_source.id]):
        with own.as_owner(owner):
            result = await tasks.task_update(
                name=name,
                source_ids=source_ids,
                cron="0 10 * * *",
                brands=["must-not-persist"],
            )
        # Both cases fail closed without revealing whether a foreign row exists.
        assert "Task sources must belong to the same workspace" in result["error"]
        assert await _snapshot(own, name) == before


async def test_task_add_refuses_unavailable_sources_without_creating_a_task(two_workspaces) -> None:
    own, foreign = two_workspaces
    owner = await _owner(own)
    foreign_owner = await _owner(foreign)
    foreign_source = await _source(foreign, foreign_owner, "foreign")

    for source_ids in ([_MISSING_SOURCE_ID], [foreign_source.id]):
        name = _name("bad-add")
        with own.as_owner(owner):
            result = await tasks.task_add(
                name=name,
                cron="0 9 * * *",
                job_type="collect",
                source_ids=source_ids,
                start_date="2026-01-01",
            )
        assert "Task sources must belong to the same workspace" in result["error"]
        with tenant_scope(own.tenant_id):
            assert await AgentTask.objects.get(name=name) is None


async def test_task_add_refuses_foreign_scenario_without_creating_a_task(two_workspaces) -> None:
    own, foreign = two_workspaces
    owner = await _owner(own)
    foreign_owner = await _owner(foreign)
    foreign_scenario = await _scenario(foreign, foreign_owner, "foreign")
    name = _name("foreign-scenario-add")

    with own.as_owner(owner):
        result = await tasks.task_add(
            name=name,
            cron="0 9 * * *",
            job_type="analyze",
            scenario_id=foreign_scenario.id,
            start_date="2026-01-01",
        )

    assert "Scenario is unavailable in this workspace" in result["error"]
    with tenant_scope(own.tenant_id):
        assert await AgentTask.objects.get(name=name) is None


async def test_foreign_scenario_update_rolls_back_other_task_changes(two_workspaces) -> None:
    own, foreign = two_workspaces
    owner = await _owner(own)
    foreign_owner = await _owner(foreign)
    source = await _source(own, owner, "scenario-rollback")
    foreign_scenario = await _scenario(foreign, foreign_owner, "foreign-update")
    name = _name("foreign-scenario-update")
    await _create_task(own, owner, name=name, source_ids=[source.id], job_type="learn", start_date=None)
    before = await _snapshot(own, name)

    with own.as_owner(owner):
        result = await tasks.task_update(
            name=name,
            scenario_id=foreign_scenario.id,
            cron="0 10 * * *",
            brands=["must-not-persist"],
        )

    assert "Scenario is unavailable in this workspace" in result["error"]
    assert await _snapshot(own, name) == before


@pytest.mark.parametrize("scenario_id", [0, -1, True, 2**31])
async def test_invalid_scenario_ids_are_refused_before_task_writes(workspace, scenario_id) -> None:
    owner = await _owner(workspace)
    source = await _source(workspace, owner, "invalid-scenario-id")
    name = _name("invalid-scenario-update")
    await _create_task(workspace, owner, name=name, source_ids=[source.id], job_type="learn", start_date=None)
    before = await _snapshot(workspace, name)
    create_name = _name("invalid-scenario-create")

    with workspace.as_owner(owner):
        create_result = await tasks.task_add(
            name=create_name,
            cron="0 9 * * *",
            job_type="analyze",
            scenario_id=scenario_id,
            start_date="2026-01-01",
        )
    assert create_result.get("error"), create_result
    with tenant_scope(workspace.tenant_id):
        assert await AgentTask.objects.get(name=create_name) is None

    with workspace.as_owner(owner):
        update_result = await tasks.task_update(
            name=name,
            scenario_id=scenario_id,
            cron="0 10 * * *",
            brands=["must-not-persist"],
        )
    assert update_result.get("error"), update_result
    assert await _snapshot(workspace, name) == before


async def test_link_write_failure_rolls_back_task_and_links(workspace, monkeypatch) -> None:
    owner = await _owner(workspace)
    first = await _source(workspace, owner, "first")
    second = await _source(workspace, owner, "second")
    third = await _source(workspace, owner, "third")
    name = _name("rollback")
    await _create_task(workspace, owner, name=name, source_ids=[first.id, second.id])
    before = await _snapshot(workspace, name)

    new_cron = "0 10 * * *"
    observed: dict = {}
    original = AgentTaskManager.set_sources

    async def fail_after_link_write(manager_self, task_id, source_ids, session=None):
        await original(manager_self, task_id, source_ids, session=session)
        row = (
            await session.execute(select(AgentTask.cron_expr, AgentTask.payload).where(AgentTask.id == task_id))
        ).one()
        observed["cron_expr"] = row.cron_expr
        observed["payload"] = deepcopy(row.payload)
        raise RuntimeError("injected link-write failure")

    monkeypatch.setattr(AgentTaskManager, "set_sources", fail_after_link_write)

    with workspace.as_owner(owner), pytest.raises(RuntimeError, match="injected link-write failure"):
        await tasks.task_update(
            name=name,
            cron=new_cron,
            source_ids=[third.id],
            brands=["rolled-back"],
        )

    # The row update and the link write reached PostgreSQL before the fault.
    assert observed["cron_expr"] == new_cron
    assert observed["payload"]["brands"] == ["rolled-back"]

    after = await _snapshot(workspace, name)
    assert after == before
    assert after.source_ids == sorted([first.id, second.id])
