"""Prepared SQL-predicate/source-wiring checks; owner execution only.

No application imports, SQL execution, providers or DB/bootstrap operations.
Actual manager acquisition/retention behavior is in the separate PostgreSQL file.
"""

import ast
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_job_stale_reap as fixtures
from source_import_isolation import load_isolated_source

ROOT = fixtures.ROOT
MODEL = fixtures.MODEL


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class BudgetPredicateTests(unittest.TestCase):
    def setUp(self):
        self.budget = load_isolated_source("app.jobs._budget_predicate_test", ROOT / "app/jobs/attempt_budget.py", {})

    def test_available_requires_nonnegative_counter_positive_limit_and_room(self):
        value = fixtures.sql(self.budget.available_attempt_budget(MODEL))
        for expected in ("jobs.attempts >= 0", "jobs.max_attempts > 0", "jobs.attempts < jobs.max_attempts"):
            self.assertIn(expected, value)
        self.assertIn(" AND ", value)

    def test_unavailable_includes_null_negative_zero_and_exhausted_values(self):
        value = fixtures.sql(self.budget.unavailable_attempt_budget(MODEL))
        for expected in (
            "jobs.attempts IS NULL",
            "jobs.max_attempts IS NULL",
            "jobs.attempts < 0",
            "jobs.max_attempts <= 0",
            "jobs.attempts >= jobs.max_attempts",
        ):
            self.assertIn(expected, value)
        self.assertIn(" OR ", value)

    def test_marker_is_static_and_has_no_like_wildcards(self):
        prefix = self.budget.ATTEMPT_BUDGET_STOP_PREFIX
        self.assertIn("outcome unconfirmed", prefix)
        self.assertNotIn("%", prefix)
        self.assertNotIn("_", prefix)

    def test_both_acquisition_paths_apply_same_helper_inside_where(self):
        tree = ast.parse((ROOT / "app/models/managers/job_manager.py").read_text())
        for name in ("_claim", "start_running"):
            method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            wheres = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "where"
            ]
            uses = [
                n
                for where in wheres
                for n in ast.walk(where)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "available_attempt_budget"
            ]
            self.assertEqual(len(uses), 1, name)


class PrunePredicateTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_prune_keeps_null_error_retention_and_excludes_budget_stop(self):
        from sqlalchemy import Column, DateTime, MetaData, Table, Text

        budget = load_isolated_source("app.jobs._budget_prune_fixture", ROOT / "app/jobs/attempt_budget.py", {})
        columns = Table("jobs", MetaData(), Column("created_at", DateTime(timezone=True)), Column("error", Text)).c
        filters = []

        class Query:
            def __init__(self):
                self.delete = AsyncMock(return_value=2)

            def filter(self, *criteria, **kwargs):
                filters.append((criteria, kwargs))
                return self

        query = Query()
        session = SimpleNamespace(begin=lambda: Transaction(), close=AsyncMock())
        collected = SimpleNamespace(objects=SimpleNamespace(delete_older_than=AsyncMock(return_value=0)))
        module = load_isolated_source(
            "app.jobs._prune_budget_test",
            ROOT / "app/jobs/handlers.py",
            {
                "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=RuntimeError),
                "app.core.database": SimpleNamespace(new_session=lambda: session),
                "app.jobs.attempt_budget": budget,
                "app.models": SimpleNamespace(
                    Job=SimpleNamespace(objects=query, created_at=columns.created_at, error=columns.error),
                    CollectedItem=collected,
                ),
            },
        )
        result = await module.handle_prune({"days": 7})
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(filters[0][1], {"status__in": ["done", "failed"]})
        value = fixtures.sql(filters[-1][0][0])
        self.assertIn("jobs.error IS NULL OR", value)
        self.assertIn("NOT LIKE", value)
        self.assertIn(budget.ATTEMPT_BUDGET_STOP_PREFIX, value)
        query.delete.assert_awaited_once()
        session.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
