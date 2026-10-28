"""Data-retention setting, calendar cutoff and prune wiring; no DB/bootstrap.

Run directly: python tests/test_data_retention.py
PostgreSQL deletion semantics live in tests/test_collected_items.py.
"""

from __future__ import annotations

import ast
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils.retention import months_before  # noqa: E402

UTC = timezone.utc
SECRET = "synthetic-private-sql-parameter"


class MonthsBeforeTests(unittest.TestCase):
    def test_one_calendar_month(self):
        self.assertEqual(months_before(datetime(2026, 10, 12, 4, tzinfo=UTC), 1), datetime(2026, 9, 12, 4, tzinfo=UTC))

    def test_year_boundary_and_twelve_months(self):
        self.assertEqual(months_before(datetime(2026, 1, 15, tzinfo=UTC), 1), datetime(2025, 12, 15, tzinfo=UTC))
        self.assertEqual(months_before(datetime(2026, 3, 1, tzinfo=UTC), 12), datetime(2025, 3, 1, tzinfo=UTC))
        self.assertEqual(months_before(datetime(2026, 3, 1, tzinfo=UTC), 27), datetime(2023, 12, 1, tzinfo=UTC))

    def test_day_is_clamped_never_shortening_the_window(self):
        self.assertEqual(months_before(datetime(2026, 3, 31, tzinfo=UTC), 1), datetime(2026, 2, 28, tzinfo=UTC))
        self.assertEqual(months_before(datetime(2028, 3, 31, tzinfo=UTC), 1), datetime(2028, 2, 29, tzinfo=UTC))
        self.assertEqual(months_before(datetime(2026, 10, 31, tzinfo=UTC), 1), datetime(2026, 9, 30, tzinfo=UTC))
        for day in range(1, 32):
            moment = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=day - 1)
            self.assertGreaterEqual((moment - months_before(moment, 1)).days, 28)

    def test_invalid_input_fails_closed(self):
        aware = datetime(2026, 10, 12, tzinfo=UTC)
        with self.assertRaises(ValueError):
            months_before(datetime(2026, 10, 12), 1)
        for months in (0, -1, True, 1.0, "1", None):
            with self.subTest(months=months), self.assertRaises(ValueError):
                months_before(aware, months)


class SettingTests(unittest.TestCase):
    def build(self, **values):
        from pydantic import ValidationError  # noqa: F401  (imported for the caller)

        from app.core.config import Settings

        return Settings(_env_file=None, SECRET_KEY="x", POSTGRES_URL="postgresql+asyncpg://x@127.0.0.1:1/x", **values)

    def test_default_is_one_month(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("DATA_RETENTION_MONTHS", None)
            self.assertEqual(self.build().DATA_RETENTION_MONTHS, 1)

    def test_env_override_and_minimum(self):
        from pydantic import ValidationError

        with patch.dict("os.environ", {"DATA_RETENTION_MONTHS": "6"}):
            self.assertEqual(self.build().DATA_RETENTION_MONTHS, 6)
        for value in ("0", "-3", "abc"):
            with self.subTest(value=value), patch.dict("os.environ", {"DATA_RETENTION_MONTHS": value}):
                with self.assertRaises(ValidationError):
                    self.build()

    def test_env_example_documents_the_default(self):
        lines = (ROOT / ".env.example").read_text().splitlines()
        self.assertIn("DATA_RETENTION_MONTHS=1", lines)


def module_with(**values):
    module = ModuleType("isolated_dependency")
    module.__dict__.update(values)
    return module


def load_prune():
    path = ROOT / "app/jobs/handlers.py"
    tree = ast.parse(path.read_text())
    implementation = ast.parse((ROOT / "app/jobs/job_handlers/prune.py").read_text())
    tree.body = [node for node in tree.body if getattr(node, "name", None) != "handle_prune"] + implementation.body
    names = {"handle_prune", "_log_id"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    classifier_path = ROOT / "app/utils/error_kinds.py"
    classifier_tree = ast.parse(classifier_path.read_text())
    classifier_node = next(
        n for n in classifier_tree.body if isinstance(n, ast.FunctionDef) and n.name == "classify_error"
    )
    classifier_namespace = {}
    exec(
        compile(ast.Module(body=[classifier_node], type_ignores=[]), str(classifier_path), "exec"), classifier_namespace
    )
    logger = logging.getLogger("isolated_data_retention")
    namespace = {"Any": Any, "logger": logger, "_error_kind": classifier_namespace["classify_error"]}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)
    return (
        partial(
            namespace["handle_prune"],
            logger=logger,
            log_id=namespace["_log_id"],
            error_kind=classifier_namespace["classify_error"],
        ),
        logger,
    )


class Field:
    def __lt__(self, value):
        return ("lt", value)

    def is_(self, value):
        return ("is", value)

    def startswith(self, value):
        return ("prefix", value)


class Query:
    def __init__(self):
        self.delete = AsyncMock(return_value=0)

    def filter(self, *args, **kwargs):
        return self


class Session:
    close = AsyncMock()

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class PruneWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.prune, self.logger = load_prune()
        self.sweep = AsyncMock(return_value=0)
        self.expire = AsyncMock(return_value=2)
        self.settings = SimpleNamespace(DATA_RETENTION_MONTHS=1)
        modules = {
            "sqlalchemy": module_with(not_=lambda e: ("not", e), or_=lambda *e: ("or", e)),
            "app.core.config": module_with(settings=self.settings),
            "app.core.database": module_with(new_session=Session),
            "app.jobs.attempt_budget": module_with(ATTEMPT_BUDGET_STOP_PREFIX="stop"),
            "app.jobs.recovery_policy": module_with(
                OPERATOR_CANCELLED_ERROR="Cancelled by operator", OUTCOME_UNCONFIRMED_PREFIX="unconfirmed"
            ),
            "app.models": module_with(
                Job=SimpleNamespace(objects=Query(), created_at=Field(), error=Field()),
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(delete_older_than=self.sweep)),
                AIAnalytics=SimpleNamespace(objects=SimpleNamespace(delete_expired=self.expire)),
            ),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_analytics_cutoff_follows_the_setting_in_calendar_months(self):
        for months in (1, 3):
            with self.subTest(months=months):
                self.expire.reset_mock()
                self.settings.DATA_RETENTION_MONTHS = months
                before = datetime.now(UTC)
                result = await self.prune({"days": 7})
                after = datetime.now(UTC)
                (cutoff,), _ = self.expire.await_args
                self.assertIsNotNone(cutoff.tzinfo)
                self.assertTrue(months_before(before, months) <= cutoff <= months_before(after, months))
                self.assertEqual(result["analytics_deleted"], 2)
                self.assertEqual(result["retention_months"], months)
                self.assertNotIn("analytics_retention_failed", result)

    async def test_raw_content_never_outlives_the_retention_cutoff(self):
        result = await self.prune({"staged_days": 400})
        self.assertLessEqual(result["staged_days"], 31)
        self.assertGreaterEqual(result["staged_days"], 28)
        self.sweep.assert_awaited_once()
        self.assertEqual(self.sweep.await_args.args[1], result["staged_days"])
        # The shorter job-history budget still governs by default.
        self.assertEqual((await self.prune({}))["staged_days"], 7)

    async def test_analytics_failure_is_reported_privately_and_not_as_zero(self):
        self.expire.side_effect = RuntimeError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            result = await self.prune({"job_id": 12})
        self.assertIs(result["analytics_retention_failed"], True)
        self.assertEqual(result["analytics_deleted"], 0)
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.msg, "analytics_retention_failed job_id=%s error_code=%s")
        self.assertEqual(record.args, (12, "runtime_error"))
        self.assertNotIn(SECRET, record.getMessage())
        self.assertIsNone(record.exc_info)

    async def test_cancellation_is_not_swallowed(self):
        import asyncio

        self.expire.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.prune({})


class ManagerPredicateTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_rows_exclude_bot_action_evidence(self):
        from sqlalchemy.dialects import postgresql

        from app.models import AIAnalytics

        captured = {}

        class QS:
            def __init__(self, criteria):
                captured["criteria"] = criteria

            async def delete(self):
                return 7

        cutoff = datetime(2026, 9, 12, tzinfo=UTC)
        with patch.object(type(AIAnalytics.objects), "filter", lambda self, *c, **k: QS(c)):
            self.assertEqual(await AIAnalytics.objects.delete_expired(cutoff), 7)
        from sqlalchemy import delete

        statement = delete(AIAnalytics).where(*captured["criteria"])
        sql = " ".join(
            str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).split()
        )
        self.assertIn("ai_analytics.updated_at <", sql)
        self.assertIn("NOT (EXISTS (SELECT", sql)
        from app.core.config import settings

        schema = settings.DB_SCHEMA
        self.assertIn(
            f"FROM {schema}.bot_actions WHERE {schema}.bot_actions.analytics_id = {schema}.ai_analytics.id", sql
        )
        self.assertNotIn(f"FROM {schema}.bot_actions, {schema}.ai_analytics", sql)
        self.assertNotIn("analysis_date", sql)

    async def test_naive_cutoff_is_rejected_before_sql(self):
        from app.models import AIAnalytics

        with patch.object(type(AIAnalytics.objects), "filter", side_effect=AssertionError("no SQL")):
            with self.assertRaises(ValueError):
                await AIAnalytics.objects.delete_expired(datetime(2026, 9, 12))


if __name__ == "__main__":
    unittest.main()
