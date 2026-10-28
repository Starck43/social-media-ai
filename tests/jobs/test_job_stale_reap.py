"""Actual-manager source regressions for stale recovery; no app/DB import or engine.

Statements are compiled from real SQLAlchemy tables against a recording session
double. PostgreSQL behavior, locking and atomicity are covered in the DB file.
"""

import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
STARTED = NOW - timedelta(minutes=40)


class Model(DeclarativeBase):
    pass


class JobRow(Model):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    agent_task_id = Column(Integer)
    status = Column(String)
    locked_at = Column(DateTime(timezone=True))
    run_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    attempts = Column(Integer)
    max_attempts = Column(Integer)
    result = Column(Text)
    llm_cost = Column(Integer)
    error = Column(Text)


class TaskRow(Model):
    __tablename__ = "agent_tasks"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    last_status = Column(String)
    last_error = Column(Text)
    last_run_at = Column(DateTime(timezone=True))


# Shared with sibling source tests (attempt budget, cleanup) as a column collection.
MODEL = JobRow.__table__.c
UNCONFIRMED = "Queue outcome unconfirmed; inspect before retry. "


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class DummyBase:
    @classmethod
    def __class_getitem__(cls, item):
        return cls


class TenantError(RuntimeError):
    pass


class ReapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.budget = load_isolated_source("app.jobs._budget_fixture", ROOT / "app/jobs/attempt_budget.py", {})
        self.statements = []
        self.results = [[], []]
        self.sessions = 0
        self.committed = False
        self.fail_on = None
        self.bypass = False
        self.tenant_id = 31

        test = self

        class Result:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return list(self.rows)

        class Session:
            async def execute(self, statement):
                test.statements.append(statement)
                if test.fail_on is not None and len(test.statements) == test.fail_on:
                    raise RuntimeError("storage_write_failed")
                return Result(test.results.pop(0) if test.results else [])

            @asynccontextmanager
            async def begin(self):
                yield
                test.committed = True

        @asynccontextmanager
        async def session_maker():
            test.sessions += 1
            yield Session()

        tenancy = SimpleNamespace(
            TenantContextError=TenantError,
            current_tenant_id=lambda: self.tenant_id,
            is_bypass=lambda: self.bypass,
        )
        module = load_isolated_source(
            "app.models.managers._stale_reap_source",
            ROOT / "app/models/managers/job_manager.py",
            {
                "app.models.managers.base_manager": SimpleNamespace(BaseManager=DummyBase),
                **dict.fromkeys(('app.models.job', 'app.models.scheduling.job'), SimpleNamespace(Job=JobRow)),
                **dict.fromkeys(('app.models.agent_task', 'app.models.scheduling.agent_task'), SimpleNamespace(AgentTask=TaskRow)),
                "app.core.database": SimpleNamespace(async_session_maker=session_maker),
                "app.core.tenant_context": tenancy,
                "app.jobs.attempt_budget": self.budget,
                "app.jobs.recovery_policy": SimpleNamespace(OUTCOME_UNCONFIRMED_PREFIX=UNCONFIRMED),
            },
        )
        module.__isolated_imports__["app.models.managers.job_lifecycle"].datetime = FixedDatetime
        self.module = module
        self.manager = object.__new__(module.JobManager)

    async def test_budget_stop_then_quarantine_keep_predicates_and_evidence(self):
        self.assertEqual(await self.manager.reap_stale(), 0)
        self.assertEqual(len(self.statements), 2)
        stop, quarantine = (sql(statement) for statement in self.statements)
        self.assertIn("jobs.status = 'running'", stop)
        self.assertIn("jobs.status = 'pending'", stop)
        self.assertIn("jobs.attempts >= jobs.max_attempts", stop)
        self.assertIn("jobs.locked_at < '2026-10-09 23:30:00+00:00'", stop)
        self.assertIn("jobs.tenant_id = 31", stop)
        self.assertIn(self.budget.ATTEMPT_BUDGET_STOP_PREFIX, stop)
        self.assertIn("jobs.attempts < jobs.max_attempts", quarantine)
        self.assertIn("jobs.locked_at < '2026-10-09 23:30:00+00:00'", quarantine)
        self.assertIn("jobs.tenant_id = 31", quarantine)
        self.assertIn(UNCONFIRMED, quarantine)
        for text in (stop, quarantine):
            set_clause = text.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
            for field in ("locked_at", "started_at", "attempts", "run_at", "result", "llm_cost"):
                self.assertNotIn(f"{field}=", set_clause.replace(" ", ""))
            self.assertIn(
                "RETURNING jobs.tenant_id, jobs.agent_task_id, coalesce(jobs.started_at, jobs.created_at)", text
            )
        self.assertTrue(self.committed)

    async def test_custom_timeout_is_preserved_for_both_writes(self):
        await self.manager.reap_stale(timeout_minutes=5)
        for statement in self.statements:
            self.assertIn("2026-10-09 23:55:00", sql(statement))

    async def test_stopped_rows_project_task_with_newer_evidence_guard(self):
        stopped = (31, 9, STARTED, self.budget.ATTEMPT_BUDGET_STOP_PREFIX + "old")
        reaped = [(31, 10, STARTED, UNCONFIRMED), (31, None, STARTED, UNCONFIRMED)]
        self.results = [[stopped], reaped]
        self.assertEqual(await self.manager.reap_stale(), 2)
        # Two job writes, then exactly one Task write per stopped row that has a Task.
        self.assertEqual(len(self.statements), 4)
        for statement, task_id, error in zip(self.statements[2:], (9, 10), (stopped[3], UNCONFIRMED)):
            text = sql(statement)
            self.assertIn("UPDATE agent_tasks SET", text)
            self.assertIn(f"agent_tasks.id = {task_id}", text)
            self.assertIn("agent_tasks.tenant_id = 31", text)
            self.assertIn(
                "agent_tasks.last_run_at IS NULL OR agent_tasks.last_run_at <= '2026-10-09 23:20:00+00:00'", text
            )
            self.assertIn("last_status='failed'", text)
            self.assertIn(f"last_error='{error}'", text)
            self.assertIn("last_run_at='2026-10-10 00:00:00+00:00'", text)
        self.assertEqual(self.sessions, 1)
        self.assertTrue(self.committed)

    async def test_bypass_is_cross_workspace_and_projects_each_owner(self):
        self.bypass = True
        self.tenant_id = None
        self.results = [[], [(31, 9, STARTED, UNCONFIRMED), (32, 9, STARTED, UNCONFIRMED)]]
        self.assertEqual(await self.manager.reap_stale(), 2)
        self.assertNotIn("jobs.tenant_id =", sql(self.statements[0]))
        self.assertIn("agent_tasks.tenant_id = 31", sql(self.statements[2]))
        self.assertIn("agent_tasks.tenant_id = 32", sql(self.statements[3]))

    async def test_task_write_failure_propagates_inside_the_same_transaction(self):
        self.results = [[], [(31, 9, STARTED, UNCONFIRMED)]]
        self.fail_on = 3
        with self.assertRaisesRegex(RuntimeError, "storage_write_failed"):
            await self.manager.reap_stale()
        self.assertFalse(self.committed)
        self.assertEqual(self.sessions, 1)

    async def test_storage_failure_propagates_without_requeue_or_fallback(self):
        self.fail_on = 1
        with self.assertRaisesRegex(RuntimeError, "storage_write_failed"):
            await self.manager.reap_stale()
        self.assertEqual(len(self.statements), 1)
        self.assertFalse(self.committed)

    async def test_missing_or_malformed_tenant_scope_fails_before_any_session(self):
        for tenant in (None, 0, "31", True):
            with self.subTest(tenant=tenant):
                self.tenant_id = tenant
                with self.assertRaises(TenantError):
                    await self.manager.reap_stale()
        self.assertEqual(self.sessions, 0)
        self.assertEqual(self.statements, [])


if __name__ == "__main__":
    unittest.main()
