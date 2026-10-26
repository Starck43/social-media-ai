"""Remote no-DB contracts: actual manager source, SQL expressions, in-memory locks.

No engine, application import, DDL, provider, credentials or root conftest.
Simulated concurrency is NOT proof of PostgreSQL transaction/locking behavior.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from source_import_isolation import load_isolated_source
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import operators

ROOT = Path(__file__).resolve().parents[2]
activity = load_isolated_source("isolated_source_activity", ROOT / "app/jobs/source_activity.py")
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
TENANT = ContextVar("test_admission_tenant", default=7)
BYPASS = ContextVar("test_admission_bypass", default=False)
Base = declarative_base()


class Tenant(Base):
    __tablename__ = "fixture_tenants"
    id = Column(Integer, primary_key=True)


class Platform(Base):
    __tablename__ = "fixture_platforms"
    id = Column(Integer, primary_key=True)
    platform_type = Column(String)


class Source(Base):
    __tablename__ = "fixture_sources"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    is_active = Column(Boolean, default=True)
    owner_task_id = Column(Integer)
    platform_id = Column(ForeignKey("fixture_platforms.id"))
    platform = relationship(Platform)


links = Table(
    "fixture_links",
    Base.metadata,
    Column("task_id", ForeignKey("fixture_tasks.id")),
    Column("source_id", ForeignKey("fixture_sources.id")),
)


class Scenario(Base):
    __tablename__ = "fixture_scenarios"
    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean)


class Task(Base):
    __tablename__ = "fixture_tasks"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    job_type = Column(String)
    payload = Column(JSON)
    is_active = Column(Boolean)
    cron_expr = Column(String)
    next_run_at = Column(DateTime(timezone=True))
    last_run_at = Column(DateTime(timezone=True))
    last_status = Column(String)
    last_error = Column(String)
    agent_scenario_id = Column(ForeignKey("fixture_scenarios.id"))
    agent_scenario = relationship(Scenario)
    sources = relationship(Source, secondary=links)
    # Read by the admission-time effective snapshot; None/False defaults match
    # a task row that never configured them.
    trigger_type = Column(String)
    trigger_config = Column(JSON)
    action_type = Column(String)
    rate_limit_per_hour = Column(Integer)
    cooldown_seconds = Column(Integer)
    dry_run = Column(Boolean)


class Job(Base):
    __tablename__ = "fixture_jobs"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    agent_task_id = Column(Integer)
    job_type = Column(String)
    status = Column(String)
    payload = Column(JSON)


class ContextError(RuntimeError):
    pass


@contextmanager
def tenant_scope(tenant_id):
    token = TENANT.set(tenant_id)
    try:
        yield
    finally:
        TENANT.reset(token)


def matches(row, query):
    for clause in query._where_criteria:
        left = getattr(row, clause.left.key)
        right = getattr(clause.right, "value", None)
        if clause.operator is operators.eq and left != right:
            return False
        if clause.operator is operators.in_op and left not in right:
            return False
        if clause.operator is operators.is_ and left is not True:
            return False
        if clause.operator is operators.le and not (left is not None and left <= right):
            return False
    return True


class Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        assert len(self.values) <= 1
        return self.values[0] if self.values else None


class Session:
    def __init__(self, fixture):
        self.f = fixture
        self.locked = None
        self.saved_jobs = None
        self.saved_tasks = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self.Transaction(self)

    class Transaction:
        def __init__(self, session):
            self.s = session

        async def __aenter__(self):
            return self.s

        async def __aexit__(self, kind, error, tb):
            s = self.s
            try:
                if s.locked is not None and (kind or s.f.commit_error):
                    s.f.jobs[:] = [job for job in s.f.jobs if job.tenant_id != s.locked] + s.saved_jobs
                    for task_id, attrs in s.saved_tasks.items():
                        task = s.f.tasks[task_id]
                        for key, value in attrs.items():
                            setattr(task, key, value)
                    if not kind:
                        raise s.f.commit_error
                elif s.locked is not None:
                    s.f.events.append("commit")
            finally:
                if s.locked is not None:
                    s.f.locks[s.locked].release()
                    s.locked = None

    async def execute(self, query):
        self.f.queries.append(query)
        description = query.column_descriptions[0]
        model = description["entity"]
        if model is Tenant:
            tid = query._where_criteria[0].right.value
            if tid not in self.f.tenants:
                return Result([])
            if self.locked is None:
                await self.f.locks.setdefault(tid, asyncio.Lock()).acquire()
                self.locked = tid
                self.saved_jobs = [job for job in self.f.jobs if job.tenant_id == tid]
                self.saved_tasks = {
                    key: {
                        name: getattr(task, name)
                        for name in ("next_run_at", "last_run_at", "last_status", "last_error", "is_active")
                    }
                    for key, task in self.f.tasks.items()
                    if task.tenant_id == tid
                }
                self.f.events.append("gate")
            assert query._for_update_arg.key_share and not query._for_update_arg.read
            return Result([tid])
        rows = {Job: self.f.jobs, Task: list(self.f.tasks.values()), Source: list(self.f.sources.values())}[model]
        rows = [row for row in rows if matches(row, query)]
        if description["name"] == "id":
            return Result([row.id for row in rows])
        return Result(rows)

    async def connection(self):
        return NS(get_isolation_level=AsyncMock(return_value=self.f.isolation))

    async def flush(self):
        pass

    # Inert endpoints for the with_db_session double below; transactional
    # commit/rollback semantics live in begin()/Transaction above.
    async def commit(self):
        pass

    async def rollback(self):
        pass


def with_db_session_double(session_factory):
    """No-DB double of app.core.database.with_db_session (never imported here).

    Mirrors the real decorator's contract: an explicit non-None ``session``
    kwarg passes straight through; otherwise the call owns a session from the
    fake factory, commits on success and rolls back + re-raises on failure.
    """

    def with_db_session(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            if kwargs.get("session") is not None:
                return await func(self, *args, **kwargs)
            async with session_factory() as session:
                kwargs["session"] = session
                try:
                    result = await func(self, *args, **kwargs)
                    await session.commit()
                    return result
                except Exception:
                    await session.rollback()
                    raise

        return wrapper

    return with_db_session


class Fixture:
    def __init__(self):
        self.jobs, self.tasks, self.sources, self.queries, self.events = [], {}, {}, [], []
        self.tenants, self.locks = {7, 8}, {}
        self.commit_error = self.create_error = None
        self.isolation = "READ COMMITTED"
        self.pause = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        fixture = self

        class ManagerBase:
            def __init__(self, model):
                self.model = model

            @classmethod
            def __class_getitem__(cls, name):
                return cls

            async def _apply_tenant(self, session, values):
                tid = TENANT.get()
                explicit = values.get("tenant_id")
                if BYPASS.get():
                    tid = explicit
                elif explicit is not None and explicit != tid:
                    raise ContextError("foreign")
                if type(tid) is not int or tid <= 0:
                    raise ContextError("missing")
                return values | {"tenant_id": tid}

            async def create(self, *, session, **values):
                assert session.locked == values["tenant_id"]
                job = Job(
                    id=100 + len(fixture.jobs),
                    status="pending",
                    **{
                        key: value
                        for key, value in values.items()
                        if key in {"tenant_id", "agent_task_id", "job_type", "payload"}
                    },
                )
                fixture.jobs.append(job)
                fixture.events.append("insert")
                if fixture.pause:
                    fixture.entered.set()
                    await fixture.release.wait()
                if fixture.create_error:
                    raise fixture.create_error
                return job

        config = NS(settings=NS(JOB_MAX_ATTEMPTS=3, SCHEDULER_TIMEZONE="UTC"))
        context = NS(
            current_tenant_id=TENANT.get,
            is_bypass=BYPASS.get,
            TenantContextError=ContextError,
            tenant_scope=tenant_scope,
        )
        self.imports = {
            "app.core.config": config,
            "app.core.tenant_context": context,
            "app.core.database": NS(
                async_session_maker=lambda: Session(self),
                with_db_session=with_db_session_double(lambda: Session(self)),
            ),
            "app.models.managers.base_manager": NS(BaseManager=ManagerBase),
            **dict.fromkeys(('app.models.job', 'app.models.scheduling.job'), NS(Job=Job)),
            **dict.fromkeys(('app.models.tenant', 'app.models.tenancy.tenant'), NS(Tenant=Tenant)),
            **dict.fromkeys(('app.models.agent_task', 'app.models.scheduling.agent_task'), NS(AgentTask=Task)),
            **dict.fromkeys(('app.models.source', 'app.models.collection.source'), NS(Source=Source)),
            "app.jobs.source_activity": activity,
        }
        self.module = load_isolated_source(
            "app.models.managers.isolated_source_jobs", ROOT / "app/models/managers/job_manager.py", self.imports
        )
        self.manager = self.module.JobManager()
        self.imports.update(
            {
                "app.models.managers.job_manager": self.module,
                "croniter": NS(croniter=lambda *a: None),
                "app.core.permissions": NS(require_permission=lambda *a: lambda f: f, get_current_user=lambda: None),
                "app.types": NS(ActionType=NS(CREATE="create", UPDATE="update", DELETE="delete")),
                "app.tasks.cron": NS(
                    next_run_at=lambda *a, after, **kw: after + timedelta(hours=1), resolve_tz=lambda *a: "UTC"
                ),
            }
        )
        self.task_module = load_isolated_source(
            "app.models.managers.isolated_source_tasks",
            ROOT / "app/models/managers/agent_task_manager.py",
            self.imports,
        )
        self.task_manager = self.task_module.AgentTaskManager()
        self.source(1)
        self.source(2)
        self.source(3, tenant=8)

    def source(self, id, tenant=7, active=True):
        source = Source(id=id, tenant_id=tenant, is_active=active)
        self.sources[id] = source
        return source

    def task(self, id=10, sources=(1,), tenant=7, kind="analyze", once=False):
        task = Task(
            id=id,
            tenant_id=tenant,
            sources=[self.sources[s] for s in sources],
            job_type=kind,
            payload={},
            is_active=True,
            cron_expr="@once" if once else "0 * * * *",
            next_run_at=NOW,
            last_run_at=None,
            last_status="old",
            last_error="old",
            agent_scenario=None,
        )
        self.tasks[id] = task
        return task

    def old_job(self, *, id=9, tenant=7, kind="analyze", status="running", task_id=None, payload=None):
        job = Job(id=id, tenant_id=tenant, job_type=kind, status=status, agent_task_id=task_id, payload=payload or {})
        self.jobs.append(job)
        return job

    async def admit_task(self, task, scheduled=False):
        with tenant_scope(task.tenant_id):
            return await self.task_manager._admit_task_run(
                task.id, now=NOW, scheduled=scheduled, expected_next_run_at=NOW if scheduled else None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["collect", "analyze"])
@pytest.mark.parametrize("status", ["pending", "running"])
@pytest.mark.parametrize("task_sources", [(1,), ()])
async def test_task_link_or_all_active_blocks_direct_source(kind, status, task_sources):
    f = Fixture()
    task = f.task(sources=task_sources, kind=kind)
    existing = f.old_job(kind=kind, status=status, task_id=task.id)
    with pytest.raises(activity.SourceJobBusy) as caught:
        await f.manager.admit_source_job("analyze", {"source_ids": [1]})
    assert caught.value.job is existing
    assert f.jobs == [existing] and "insert" not in f.events
    assert await f.manager.source_activity(1) == {"collect": kind == "collect", "analyze": kind == "analyze"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [True, False])
@pytest.mark.parametrize("once", [True, False])
async def test_busy_task_keeps_schedule_trigger_and_once_state(scheduled, once):
    f = Fixture()
    task = f.task(once=once)
    existing = f.old_job(kind="collect", payload={"source_ids": [1]})
    before = (task.next_run_at, task.last_run_at, task.last_status, task.last_error, task.is_active)
    receipt = await f.admit_task(task, scheduled)
    assert receipt["status"] == "busy" and receipt["job"] is existing
    assert before == (task.next_run_at, task.last_run_at, task.last_status, task.last_error, task.is_active)
    assert len(f.jobs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["source", "task"])
async def test_simultaneous_source_and_task_admit_only_one_job(first):
    f = Fixture()
    task = f.task()
    f.pause = True

    async def direct():
        return await f.manager.admit_source_job("analyze", {"source_ids": [1]})

    first_coro = direct() if first == "source" else f.admit_task(task)
    second_coro = f.admit_task(task) if first == "source" else direct()
    one = asyncio.create_task(first_coro)
    two = None
    try:
        await asyncio.wait_for(f.entered.wait(), 2)
        two = asyncio.create_task(second_coro)
        await asyncio.sleep(0.02)
        assert not two.done()  # contender waits at the common transaction gate
        f.release.set()
        results = await asyncio.gather(one, two, return_exceptions=True)
        assert len(f.jobs) == 1
        if first == "source":
            assert results[1]["status"] == "busy"
        else:
            assert isinstance(results[1], activity.SourceJobBusy)
    finally:
        f.release.set()
        await asyncio.gather(one, *([two] if two else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_two_simultaneous_manual_requests_do_not_duplicate_source_job():
    f = Fixture()
    results = await asyncio.gather(
        *[f.manager.admit_source_job("analyze", {"source_ids": [1]}) for _ in range(2)], return_exceptions=True
    )
    assert len(f.jobs) == 1
    assert sum(isinstance(value, activity.SourceJobBusy) for value in results) == 1


@pytest.mark.asyncio
async def test_overlapping_tasks_conflict_but_disjoint_sources_and_tenants_do_not():
    f = Fixture()
    one = f.task(10, (1, 2))
    await f.admit_task(one)
    other = f.task(11, (2,))
    assert (await f.admit_task(other))["status"] == "busy"
    f.jobs[0].status = "done"
    assert (await f.admit_task(other))["status"] == "enqueued"
    # Explicit disjoint source and different workspace are independently admitted.
    await f.manager.admit_source_job("collect", {"source_ids": [1]})
    with tenant_scope(8):
        await f.manager.admit_source_job("analyze", {"source_ids": [3]})
    assert len(f.jobs) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed"])
async def test_terminal_rows_do_not_occupy_source(status):
    f = Fixture()
    f.old_job(payload={"source_ids": [1]}, status=status)
    await f.manager.admit_source_job("analyze", {"source_ids": [1]})
    assert len(f.jobs) == 2


@pytest.mark.asyncio
async def test_handler_precedence_deleted_task_and_inactive_membership():
    f = Fixture()
    task = f.task(sources=(2,))
    f.old_job(task_id=task.id, payload={"source_ids": [1]})
    assert await f.manager.source_activity(1) == {"collect": False, "analyze": False}
    assert (await f.manager.source_activity(2))["analyze"]
    f.old_job(id=20, task_id=999, payload={"source_ids": [1]})
    assert (await f.manager.source_activity(1))["analyze"]
    f.sources[1].is_active = False
    f.tasks[task.id].sources = []
    f.jobs[:] = [f.jobs[0]]
    assert not (await f.manager.source_activity(1))["analyze"]
    assert (await f.manager.source_activity(2))["analyze"]


@pytest.mark.asyncio
async def test_new_task_admission_updates_only_after_unique_job_insert():
    f = Fixture()
    task = f.task(once=True)
    receipt = await f.admit_task(task, scheduled=True)
    assert receipt["status"] == "enqueued"
    assert task.last_status == "queued" and task.last_run_at == NOW
    assert not task.is_active and task.next_run_at is None
    assert len(f.jobs) == 1 and f.jobs[0].agent_task_id == task.id


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert", "commit"])
async def test_failed_admission_rolls_back_job_and_task_updates(failure):
    f = Fixture()
    task = f.task(once=True)
    if failure == "insert":
        f.create_error = RuntimeError("synthetic insert failure")
    else:
        f.commit_error = RuntimeError("synthetic commit failure")
    with pytest.raises(RuntimeError):
        await f.admit_task(task)
    assert not f.jobs
    assert task.is_active and task.next_run_at == NOW and task.last_status == "old"
    assert not f.locks[7].locked()


@pytest.mark.asyncio
async def test_workspace_predicates_and_non_key_admission_lock_compile_for_postgres():
    f = Fixture()
    f.task()
    f.old_job(task_id=10, payload={"source_ids": [1]})
    await f.manager.source_activity(1)
    async with Session(f) as session:
        async with session.begin():
            await f.manager.lock_source_admission(session, 7)
    sql = [
        str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})) for query in f.queries
    ]
    assert "FOR NO KEY UPDATE" in sql[-1]
    assert "fixture_jobs.tenant_id = 7" in sql[0]
    assert all("tenant_id = 7" in statement for statement in sql[:-1])
    assert len(sql) == 4  # jobs, memberships, active sources, gate; no per-job query loop
    assert all(query.get_execution_options().get("populate_existing") is True for query in f.queries[:2])


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant,bypass,explicit", [(None, False, None), (7, False, 8), (7, True, None)])
async def test_invalid_or_foreign_tenant_fails_before_writes(tenant, bypass, explicit):
    f = Fixture()
    token, flag = TENANT.set(tenant), BYPASS.set(bypass)
    try:
        with pytest.raises(ContextError):
            await f.manager.admit_source_job("analyze", {"source_ids": [1]}, tenant_id=explicit)
        assert not f.jobs
    finally:
        TENANT.reset(token)
        BYPASS.reset(flag)


@pytest.mark.asyncio
async def test_manual_wrapper_does_not_return_or_execute_conflicting_task_job():
    f = Fixture()
    task = f.task()
    f.old_job(payload={"source_ids": [1]})
    wrapper = load_isolated_source(
        "isolated_busy_wrapper",
        ROOT / "app/jobs/enqueue.py",
        f.imports | {"app.models.managers.agent_task_manager": f.task_module},
    )
    with pytest.raises(activity.SourceJobBusy):
        await wrapper.enqueue_task_run(task)
    assert len(f.jobs) == 1


@pytest.mark.asyncio
async def test_scheduler_does_not_count_busy_occurrence_as_enqueued():
    f = Fixture()
    task = f.task()
    f.old_job(payload={"source_ids": [1]})
    f.task_manager.get_due = AsyncMock(return_value=[task])
    runner = load_isolated_source(
        "app.tasks.isolated_busy_runner",
        ROOT / "app/tasks/runner.py",
        f.imports | {"app.models.managers.agent_task_manager": NS(AgentTaskManager=lambda: f.task_manager)},
    )
    assert await runner.tick_tenant(7, now=NOW) == {"due": 1, "enqueued": 0, "failed": 0}
    assert task.next_run_at == NOW


@pytest.mark.asyncio
async def test_web_queue_reports_existing_job_without_claiming_it():
    # Same AST route harness used by the preceding retry slice. Only imports
    # at application boundaries are replaced; execute/claim is not invoked.
    import ast

    tree = ast.parse((ROOT / "app/web/tasks.py").read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "queue_task_now"
    )
    function.decorator_list = []
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    scope = {
        "enqueue_task_run": AsyncMock(side_effect=activity.SourceJobBusy(NS(id=9, status="running"))),
        "SourceJobBusy": activity.SourceJobBusy,
        "launch_job_background": lambda job_id, **kw: None,  # busy path returns early; never called
        "AgentTaskManager": lambda: NS(get_sources=AsyncMock(return_value=[1])),
        "_can_activate": AsyncMock(return_value=None),
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "isolated_web_queue", "exec"),
        scope,
    )
    assert await scope["queue_task_now"](
        NS(id=10, tenant_id=7, job_type="collect", payload={}, agent_scenario_id=None)
    ) == {"job_id": 9, "status": "running", "already_running": True}


def test_active_membership_is_conservative_and_distinct_from_terminal_jobs():
    assert activity.job_source_ids({}, 10, {10: {1}}, {2}) == {1}
    assert activity.job_source_ids({"source_ids": [9]}, 10, {10: set()}, {2}) == {2}
    assert activity.job_source_ids({"source_ids": [1]}, 99, {}, {2}) == {1}
    assert activity.job_source_ids({"source_ids": ["malformed"]}, None, {}, {2}) == {2}
    ignored = [NS(job_type="digest", status="running"), NS(job_type="analyze", status="failed")]
    assert activity.source_conflict({"source_ids": [1]}, None, ignored, {}, {1}) is None


def test_task_plan_admission_uses_scoped_snapshot_and_discovery_reserves_unknown_sources():
    linked = {4, 5, 6}
    active = {1, 2, 3, 4, 5, 6}

    selected = {
        "task_plan": {"source_scope": "selected", "source_ids": [4]},
        "source_ids": [4, 5],
    }
    all_authorized = {"task_plan": {"source_scope": "all"}, "source_ids": [1, 2, 5]}
    discovery = {"task_plan": {"source_scope": "discovery"}, "source_ids": [5]}

    assert activity.job_source_ids(selected, 10, {10: linked}, active) == {4, 5}
    assert activity.job_source_ids(all_authorized, 10, {10: linked}, active) == {1, 2, 5}
    assert activity.job_source_ids(discovery, 10, {10: linked}, active) == active


@pytest.mark.asyncio
async def test_busy_api_run_returns_conflict_without_running_existing_job():
    import ast

    from fastapi import HTTPException

    tree = ast.parse((ROOT / "app/api/v1/endpoints/tasks.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_task")
    function.decorator_list = []
    function.args.defaults = [ast.Constant(None) for _ in function.args.defaults]
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    run = AsyncMock()
    scope = {
        "HTTPException": HTTPException,
        "SourceJobBusy": activity.SourceJobBusy,
        "AgentTask": NS(objects=NS(get=AsyncMock(return_value=NS(payload={})))),
        "enqueue_task_run": AsyncMock(side_effect=activity.SourceJobBusy(NS(id=9, status="running"))),
        "run_job_now": run,
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, function], type_ignores=[])), "isolated_api_run", "exec"
        ),
        scope,
    )
    with pytest.raises(HTTPException) as caught:
        await scope["run_task"](10, request=NS(payload=None))
    assert caught.value.status_code == 409
    assert caught.value.detail == {"code": "source_busy", "job_id": 9}
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_inline_source_run_uses_guard_before_claiming():
    import ast

    tree = ast.parse((ROOT / "app/jobs/dispatcher.py").read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_job_inline"
    )
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    manager = NS(admit_source_job=AsyncMock(side_effect=activity.SourceJobBusy(NS(id=9))), enqueue=AsyncMock())
    factory = lambda: manager
    factory.claim_job = AsyncMock()
    scope = {"JobManager": factory, "SOURCE_JOB_TYPES": activity.SOURCE_JOB_TYPES, "_run_claimed_inline": AsyncMock()}
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, function], type_ignores=[])),
            "isolated_inline_source",
            "exec",
        ),
        scope,
    )
    with pytest.raises(activity.SourceJobBusy):
        await scope["run_job_inline"]("collect", {"source_ids": [1]})
    manager.admit_source_job.assert_awaited_once()
    manager.enqueue.assert_not_awaited()
    factory.claim_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_wakeup_still_uses_low_level_enqueue_not_manual_admission():
    # Guarded starts must see these active rows, but must not coalesce a new raw
    # content wakeup into a batch which may already have taken its input snapshot.
    import ast

    tree = ast.parse((ROOT / "app/services/monitoring/staging.py").read_text())
    calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert "Job.objects.enqueue" in calls
    assert "Job.objects.admit_source_job" not in calls
    f = Fixture()
    background = f.old_job(payload={"source_ids": [1]})
    with pytest.raises(activity.SourceJobBusy) as caught:
        await f.manager.admit_source_job("analyze", {"source_ids": [1]})
    assert caught.value.job is background


@pytest.mark.asyncio
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
async def test_transaction_wide_snapshot_is_refused_instead_of_missing_winner(isolation):
    f = Fixture()
    f.isolation = isolation
    with pytest.raises(ContextError, match="READ COMMITTED"):
        await f.manager.admit_source_job("analyze", {"source_ids": [1]})
    assert not f.jobs and not f.queries
