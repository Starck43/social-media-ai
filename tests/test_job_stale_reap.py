"""Actual-manager source regressions; owner execution only, not author-run.

SQLAlchemy expressions are constructed without an app/DB import or engine.
These tests strengthen the previous requeue checks with a fail-closed budget
stop before requeue. PostgreSQL behavior is covered in the separate DB file.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
MODEL = Table(
    "jobs",
    MetaData(),
    Column("status", String),
    Column("locked_at", DateTime(timezone=True)),
    Column("run_at", DateTime(timezone=True)),
    Column("attempts", Integer),
    Column("max_attempts", Integer),
    Column("error", Text),
).c


def sql(expression):
    return str(expression.compile(compile_kwargs={"literal_binds": True}))


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class DummyBase:
    @classmethod
    def __class_getitem__(cls, item):
        return cls


class ReapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.budget = load_isolated_source("app.jobs._budget_fixture", ROOT / "app/jobs/attempt_budget.py", {})
        module = load_isolated_source(
            "app.models.managers._stale_reap_source",
            ROOT / "app/models/managers/job_manager.py",
            {
                "app.models.managers.base_manager": SimpleNamespace(BaseManager=DummyBase),
                "app.models.job": SimpleNamespace(Job=MODEL),
                "app.jobs.attempt_budget": self.budget,
            },
        )
        module.datetime = FixedDatetime
        self.manager = object.__new__(module.JobManager)
        self.query = SimpleNamespace(update=AsyncMock(side_effect=[0, 2]))
        self.manager.filter = Mock(return_value=self.query)
        self.manager.update_by_id = AsyncMock()

    async def test_default_cutoff_and_conditional_updates_keep_budget_in_predicate(self):
        self.assertEqual(await self.manager.reap_stale(), 2)
        self.assertEqual(self.manager.filter.call_count, 2)
        stop, requeue = self.manager.filter.call_args_list
        self.assertEqual(stop.kwargs, {})
        self.assertIn("jobs.status = 'running'", sql(stop.args[0]))
        self.assertIn("jobs.status = 'pending'", sql(stop.args[0]))
        self.assertIn("jobs.attempts >= jobs.max_attempts", sql(stop.args[1]))
        self.assertEqual(requeue.kwargs, {"status": "running", "locked_at__lt": NOW - timedelta(minutes=30)})
        self.assertIn("jobs.attempts < jobs.max_attempts", sql(requeue.args[0]))
        stopped, requeued = self.query.update.await_args_list
        self.assertEqual((stopped.kwargs["status"], stopped.kwargs["finished_at"]), ("failed", NOW))
        self.assertIn(self.budget.ATTEMPT_BUDGET_STOP_PREFIX, sql(stopped.kwargs["error"]))
        self.assertIn("jobs.error", sql(stopped.kwargs["error"]))
        self.assertNotIn("result", stopped.kwargs)
        self.assertNotIn("llm_cost", stopped.kwargs)
        self.assertNotIn("attempts", stopped.kwargs)
        self.assertNotIn("locked_at", stopped.kwargs)
        self.assertEqual(requeued.kwargs, {"status": "pending", "locked_at": None})
        self.manager.update_by_id.assert_not_awaited()

    async def test_custom_timeout_is_preserved_for_both_writes(self):
        await self.manager.reap_stale(timeout_minutes=5)
        stop, requeue = self.manager.filter.call_args_list
        self.assertIn("2026-10-09 23:55:00", sql(stop.args[0]))
        self.assertEqual(requeue.kwargs["locked_at__lt"], NOW - timedelta(minutes=5))

    async def test_return_actual_requeued_count_not_stopped_or_candidate_count(self):
        self.query.update.side_effect = [7, 0]
        self.assertEqual(await self.manager.reap_stale(), 0)
        self.assertEqual(self.query.update.await_count, 2)

    async def test_storage_failure_propagates_without_requeue_or_per_row_fallback(self):
        self.query.update.side_effect = RuntimeError("storage_write_failed")
        with self.assertRaisesRegex(RuntimeError, "storage_write_failed"):
            await self.manager.reap_stale()
        self.assertEqual(self.manager.filter.call_count, 1)
        self.query.update.assert_awaited_once()
        self.manager.update_by_id.assert_not_awaited()

    async def test_missing_tenant_scope_propagates_before_any_write(self):
        self.manager.filter.side_effect = RuntimeError("tenant_context_required")
        with self.assertRaisesRegex(RuntimeError, "tenant_context_required"):
            await self.manager.reap_stale()
        self.query.update.assert_not_awaited()
        self.manager.update_by_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
