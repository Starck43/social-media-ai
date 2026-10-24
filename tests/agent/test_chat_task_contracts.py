"""Chat task tool contracts, running as a DB-backed bootstrap suite.

The tools own what reaches a task row and its source links: omitted fields keep
their stored value, explicit clearing is honoured, a cron change writes its
planned occurrence, and source links are replaced as one exact set inside the
caller's transaction.

The tests exercise the real registered handlers, but every mutation lands in
compact manager/model/session doubles. The module still imports the real
``app.core.database``, ORM models and manager, and the root ``tests/conftest.py``
redirects the engine to ``DB_TEST_SCHEMA`` and validates that schema at session
start. This is therefore a DB-backed bootstrap suite: run it strictly
sequentially through the root conftest, never with ``--noconftest`` and never in
parallel with another test process, even though the cases themselves perform no
database reads or writes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import models as model_package
from app.agent.tools import get_tool, tool_specs
from app.agent.toolset import tasks
from app.core import database as database_module
from app.core.config import settings
from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks import cron as cron_module

TASK_NAME = "chat-task"
TENANT_ID = 42
TASK_ID = 501
_MAX_PG_INTEGER = 2147483647


class _Transaction:
    def __init__(self, session):
        self.session = session
        self.snapshots = []

    async def __aenter__(self):
        self.snapshots = [(participant, participant.snapshot()) for participant in self.session.participants]
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            for participant, snapshot in self.snapshots:
                participant.restore(snapshot)
        return False


class _SessionDouble:
    """Minimal async session: only the begin/close surface the tools use."""

    def __init__(self):
        self.begins = 0
        self.closed = False
        self.participants = []

    def begin(self):
        self.begins += 1
        return _Transaction(self)

    async def close(self):
        self.closed = True


class _TaskStore:
    def __init__(self, *, task=None, rows=()):
        self.task = task
        self.rows = list(rows)
        self.reads = []
        self.created = []
        self.updates = []

    async def get(self, **filters):
        self.reads.append(filters)
        if "name" in filters:
            return deepcopy(self.task) if self.task is not None and self.task.name == filters["name"] else None
        return self.task

    async def order_by(self, *_):
        return list(self.rows)

    async def create(self, **values):
        self.created.append(values)
        self.task = SimpleNamespace(
            id=TASK_ID,
            name=values["name"],
            next_run_at=values["next_run_at"],
            tenant_id=TENANT_ID,
            agent_scenario_id=values["agent_scenario_id"],
            job_type=values["job_type"],
            cron_expr=values["cron_expr"],
            payload=deepcopy(values["payload"]),
        )
        return self.task

    async def update_by_id(self, task_id, session=None, **values):
        self.updates.append({"task_id": task_id, "session": session, **values})
        for key, value in values.items():
            setattr(self.task, key, value)
        return self.task

    def snapshot(self):
        return deepcopy((self.task, self.created, self.updates))

    def restore(self, snapshot):
        self.task, self.created, self.updates = snapshot


class _TenantStore:
    def __init__(self, tenant):
        self.tenant = tenant
        self.reads = []

    async def get(self, **filters):
        self.reads.append(filters)
        return self.tenant


class _SourceStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    async def filter(self, **filters):
        self.queries.append(filters)
        wanted = set(filters.get("id__in") or ())
        return [row for row in self.rows if row.id in wanted]


class _ScenarioStore:
    def __init__(self, scenario=None):
        self.scenario = scenario
        self.reads = []

    async def get(self, **filters):
        self.reads.append(filters)
        return self.scenario


class _SourceWriteTrace:
    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    def patch_target(self):
        """Build a free async function for the class attribute.

        Patching a bound method would keep its own receiver, so the handler's
        call arguments would shift by one position. As a plain function it binds
        the manager instance the handler constructs into ``manager_self``.
        """

        async def set_sources(manager_self, task_id, source_ids, session=None):
            assert isinstance(manager_self, AgentTaskManager)
            available = {row.id: row for row in self.sources.rows}
            if any(
                source_id not in available
                or available[source_id].tenant_id != TENANT_ID
                or (available[source_id].owner_task_id is not None and available[source_id].owner_task_id != task_id)
                for source_id in source_ids
            ):
                raise ValueError("Task sources must belong to the same workspace")
            self.calls.append({"task_id": task_id, "source_ids": list(source_ids), "session": session})
            return len(source_ids)

        return set_sources

    def snapshot(self):
        return deepcopy(self.calls)

    def restore(self, snapshot):
        self.calls = snapshot


def _model(store):
    class _Model:
        id = object()
        objects = store

    return _Model


def _source(source_id: int, tenant_id: int = TENANT_ID, *, owner_task_id: int | None = None):
    return SimpleNamespace(id=source_id, tenant_id=tenant_id, owner_task_id=owner_task_id)


def _task(**overrides):
    values = {
        "id": TASK_ID,
        "name": TASK_NAME,
        "job_type": "collect",
        "cron_expr": "0 9 * * *",
        "payload": {
            "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            "force_refresh": True,
        },
        "tenant_id": TENANT_ID,
        "agent_scenario_id": 77,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def install(monkeypatch):
    """Install explicit stores for the models the handlers import lazily."""

    def _install(*, task=None, rows=(), sources=(), scenario=None):
        session = _SessionDouble()
        task_store = _TaskStore(task=task, rows=rows)
        source_store = _SourceStore(sources)
        tenant_store = _TenantStore(SimpleNamespace(id=TENANT_ID, timezone=None))
        scenario_store = _ScenarioStore(scenario)
        trace = _SourceWriteTrace(source_store)
        session.participants = [task_store, trace]

        monkeypatch.setattr(model_package, "AgentTask", _model(task_store))
        monkeypatch.setattr(model_package, "Source", _model(source_store))
        monkeypatch.setattr(model_package, "Tenant", _model(tenant_store))
        monkeypatch.setattr(model_package, "AgentScenario", _model(scenario_store))
        monkeypatch.setattr(database_module, "new_session", lambda: session)
        monkeypatch.setattr("app.core.tenant_context.current_tenant_id", lambda: TENANT_ID)

        async def lock_task(manager_self, db_session, task_id):
            assert db_session is session
            assert task_store.task is not None and task_store.task.id == task_id
            return task_store.task

        async def lock_admission(db_session, tenant_id):
            assert db_session is session
            assert tenant_id == TENANT_ID

        monkeypatch.setattr(AgentTaskManager, "_lock_task_for_mutation", lock_task)
        monkeypatch.setattr("app.models.managers.job_manager.JobManager.lock_source_admission", lock_admission)
        monkeypatch.setattr(AgentTaskManager, "set_sources", trace.patch_target())
        return SimpleNamespace(
            session=session,
            tasks=task_store,
            sources=source_store,
            tenants=tenant_store,
            scenarios=scenario_store,
            trace=trace,
        )

    return _install


async def test_task_list_reads_the_model_and_formats_rows(install) -> None:
    next_run = datetime(2026, 10, 22, 6, 30, tzinfo=timezone.utc)
    row = SimpleNamespace(
        name="digest",
        cron_expr="0 8 * * *",
        job_type="digest",
        is_active=True,
        next_run_at=next_run,
        last_status="ok",
    )
    install(rows=[row])

    assert await tasks.task_list() == [
        {
            "name": "digest",
            "cron": "0 8 * * *",
            "job_type": "digest",
            "active": True,
            "next_run_at": next_run.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE)).strftime("%d.%m.%Y %H:%M"),
            "last_status": "ok",
        }
    ]


def test_task_tools_keep_registration_and_confirmation_metadata() -> None:
    add = get_tool("task_add")
    update = get_tool("task_update")
    assert add is not None and update is not None
    assert add.handler is tasks.task_add
    assert update.handler is tasks.task_update
    assert add.confirm and add.required_permission == "scheduling.task.create"
    assert update.confirm and update.required_permission == "scheduling.task.update"

    specs = {spec["function"]["name"]: spec["function"] for spec in tool_specs()}
    update_properties = specs["task_update"]["parameters"]["properties"]
    assert {"scenario_id", "clear_scenario", "source_ids", "start_date", "end_date"} <= set(update_properties)


async def test_task_add_omits_unset_fields_and_forwards_one_session(install) -> None:
    env = install(sources=[_source(11), _source(12)])

    result = await tasks.task_add(
        name="collector",
        cron="0 9 * * *",
        job_type="collect",
        source_ids=[11, 12],
        start_date="2026-02-01",
    )

    assert result["status"] == "created"
    created = env.tasks.created[0]
    assert created["session"] is env.session
    assert created["agent_scenario_id"] is None
    assert created["payload"] == {"force_refresh": False, "cli_dates": {"start_date": "2026-02-01"}}
    assert env.trace.calls == [{"task_id": TASK_ID, "source_ids": [11, 12], "session": env.session}]
    assert env.session.closed


async def test_task_add_empty_source_list_is_an_explicit_replacement(install) -> None:
    env = install()

    await tasks.task_add(name="collector", cron="0 9 * * *", job_type="collect", source_ids=[], start_date="2026-02-01")

    assert env.trace.calls == [{"task_id": TASK_ID, "source_ids": [], "session": env.session}]
    assert env.tasks.created[0]["session"] is env.session


async def test_task_update_omitted_fields_keep_existing_values(install) -> None:
    task = _task()
    env = install(task=task)

    result = await tasks.task_update(name=TASK_NAME)

    assert result["status"] == "updated"
    update = env.tasks.updates[0]
    assert update["session"] is env.session
    assert update["job_type"] == "collect"
    assert update["payload"] == task.payload
    assert "agent_scenario_id" not in update
    assert "cron_expr" not in update
    assert "next_run_at" not in update
    assert env.trace.calls == []


async def test_task_update_clear_scenario_and_conflicting_id(install) -> None:
    task = _task(job_type="learn", payload={})
    env = install(task=task)

    cleared = await tasks.task_update(name=TASK_NAME, clear_scenario=True)
    assert cleared["status"] == "updated"
    assert env.tasks.updates[0]["agent_scenario_id"] is None

    env.tasks.updates.clear()
    refused = await tasks.task_update(name=TASK_NAME, clear_scenario=True, scenario_id=5)
    assert refused.get("error")
    assert env.tasks.updates == []
    assert env.trace.calls == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("start_date", "2026-01-15", {"start_date": "2026-01-15", "end_date": "2026-01-31"}),
        ("end_date", "2026-02-28", {"start_date": "2026-01-01", "end_date": "2026-02-28"}),
    ],
)
async def test_task_update_one_sided_bound_preserves_the_other(install, field, value, expected) -> None:
    env = install(task=_task())

    await tasks.task_update(name=TASK_NAME, **{field: value})

    payload = env.tasks.updates[0]["payload"]
    assert payload["cli_dates"] == expected
    assert payload["force_refresh"] is True


async def test_task_update_saves_cron_and_next_run_together(install, monkeypatch) -> None:
    planned = datetime(2031, 1, 1, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(cron_module, "next_run_at", lambda expression, timezone_name: planned)
    env = install(task=_task(job_type="learn", payload={}))

    await tasks.task_update(name=TASK_NAME, cron="0 10 * * *")

    update = env.tasks.updates[0]
    assert update["cron_expr"] == "0 10 * * *"
    assert update["next_run_at"] == planned


async def test_task_update_replaces_sources_exactly_including_empty(install) -> None:
    env = install(task=_task(job_type="learn", payload={}), sources=[_source(11), _source(13)])

    await tasks.task_update(name=TASK_NAME, source_ids=[11, 13])
    await tasks.task_update(name=TASK_NAME, source_ids=[])

    assert [call["source_ids"] for call in env.trace.calls] == [[11, 13], []]
    assert all(call["session"] is env.session for call in env.trace.calls)
    assert all(update["session"] is env.session for update in env.tasks.updates)


async def test_task_update_merges_after_the_task_lock(install, monkeypatch) -> None:
    task = _task(payload={"brands": ["before"], "collection": {"limit": 10}})
    env = install(task=task)

    # The fixture lock is the boundary under test; return the current persisted
    # object after simulating a change that committed after the initial lookup.
    async def locked_current(manager_self, session, task_id):
        assert session is env.session and task_id == TASK_ID
        task.payload = {"brands": ["concurrent"], "collection": {"limit": 20}}
        return task

    monkeypatch.setattr(AgentTaskManager, "_lock_task_for_mutation", locked_current)
    await tasks.task_update(name=TASK_NAME, period="week")

    payload = env.tasks.updates[0]["payload"]
    assert payload["brands"] == ["concurrent"]
    assert payload["collection"] == {"limit": 20}
    assert payload["period"] == "week"


@pytest.mark.parametrize(
    ("source_ids", "sources", "fragment"),
    [
        ([7], [], "same workspace"),
        ([7], [_source(7, tenant_id=99)], "same workspace"),
        ([7], [_source(7, owner_task_id=88)], "same workspace"),
        ([0], [], "positive PostgreSQL INTEGER"),
        ([-1], [], "positive PostgreSQL INTEGER"),
        ([True], [], "positive PostgreSQL INTEGER"),
        (["7"], [], "positive PostgreSQL INTEGER"),
        ([_MAX_PG_INTEGER + 1], [], "positive PostgreSQL INTEGER"),
    ],
)
async def test_task_update_refuses_bad_source_ids_before_writes(install, source_ids, sources, fragment) -> None:
    env = install(task=_task(job_type="learn", payload={}), sources=sources)

    result = await tasks.task_update(name=TASK_NAME, source_ids=source_ids)

    assert fragment in result["error"]
    assert env.tasks.updates == []
    assert env.trace.calls == []


@pytest.mark.parametrize(
    ("source_ids", "sources", "fragment"),
    [
        ([7], [], "same workspace"),
        ([7], [_source(7, tenant_id=99)], "same workspace"),
        ([7], [_source(7, owner_task_id=88)], "same workspace"),
        ([_MAX_PG_INTEGER + 1], [], "positive PostgreSQL INTEGER"),
    ],
)
async def test_task_add_refuses_bad_source_ids_before_task_write(install, source_ids, sources, fragment) -> None:
    env = install(sources=sources)

    result = await tasks.task_add(
        name="collector",
        cron="0 9 * * *",
        job_type="collect",
        source_ids=source_ids,
        start_date="2026-02-01",
    )

    assert fragment in result["error"]
    assert env.tasks.created == []
    assert env.trace.calls == []


async def test_task_add_targets_do_not_touch_the_scenario_payload(install) -> None:
    scenario = SimpleNamespace(
        id=77,
        name="Scenario",
        analysis_types=["brand_mentions"],
        scope={"brands": ["scenario-owned"]},
    )
    env = install(scenario=scenario)

    result = await tasks.task_add(
        name="analyzer",
        cron="0 9 * * *",
        job_type="analyze",
        scenario_id=77,
        start_date="2026-02-01",
        brands=["task-owned"],
    )

    assert result["status"] == "created"
    assert scenario.scope == {"brands": ["scenario-owned"]}
    assert env.tasks.created[0]["payload"]["brands"] == ["task-owned"]
    assert env.scenarios.reads == [{"id": 77}]
