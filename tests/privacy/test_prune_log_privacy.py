"""Source-isolated prune warning checks; SQL/session doubles, no DB/bootstrap.

Run directly: python tests/test_prune_log_privacy.py
Not a retention, PostgreSQL or fleet-wide logging acceptance test.
"""

from __future__ import annotations

import ast
from functools import partial
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
SECRET = "synthetic-token-private-sql-parameter"
EVENT = "staged_retention_failed job_id=%s error_code=%s"


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
    nodes = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    if {node.name for node in nodes} != names:
        raise RuntimeError("Required prune/log definitions missing")
    classifier_path = ROOT / "app/utils/error_kinds.py"
    classifier_tree = ast.parse(classifier_path.read_text())
    classifier_node = next(
        node for node in classifier_tree.body if isinstance(node, ast.FunctionDef) and node.name == "classify_error"
    )
    classifier_namespace = {}
    exec(
        compile(ast.Module(body=[classifier_node], type_ignores=[]), str(classifier_path), "exec"),
        classifier_namespace,
    )
    logger = logging.getLogger("isolated_prune_log_privacy")
    namespace = {"Any": Any, "logger": logger, "_error_kind": classifier_namespace["classify_error"]}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)
    return partial(
        namespace["handle_prune"], logger=logger, log_id=namespace["_log_id"],
        error_kind=classifier_namespace["classify_error"],
    ), logger


class FieldDouble:
    def __lt__(self, value):
        return ("older_than", value)

    def is_(self, value):
        return ("is", value)

    def startswith(self, value):
        return ("prefix", value)


class QueryDouble:
    def __init__(self):
        self.criteria = []
        self.delete = AsyncMock(return_value=5)

    def filter(self, *args, **kwargs):
        self.criteria.append((args, kwargs))
        return self


class SessionDouble:
    def __init__(self):
        self.close = AsyncMock()
        self.enter_error = None
        self.exits = []

    def begin(self):
        return self

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, kind, error, traceback):
        self.exits.append(kind)
        return False


class PruneLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.prune, self.logger = load_prune()
        self.query = QueryDouble()
        self.session = SessionDouble()
        self.sweep = AsyncMock(return_value=3)
        self.expire = AsyncMock(return_value=4)
        job = SimpleNamespace(objects=self.query, created_at=FieldDouble(), error=FieldDouble())
        modules = {
            "sqlalchemy": module_with(not_=lambda expr: ("not", expr), or_=lambda *exprs: ("or", exprs)),
            "app.core.database": module_with(new_session=lambda: self.session),
            "app.jobs.attempt_budget": module_with(ATTEMPT_BUDGET_STOP_PREFIX="bounded_stop"),
            "app.models": module_with(
                Job=job,
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(delete_older_than=self.sweep)),
                AIAnalytics=SimpleNamespace(objects=SimpleNamespace(delete_expired=self.expire)),
            ),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_private(self, logs, job_id, code):
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual((record.msg, record.args), (EVENT, (job_id, code)))
        self.assertNotIn(SECRET, record.getMessage())
        self.assertNotIn(SECRET, repr(record.args))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)

    async def test_sweep_error_is_private_and_result_shape_is_preserved(self):
        self.sweep.side_effect = RuntimeError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            result = await self.prune({"job_id": 31, "staged_days": 9})
        self.assert_private(logs, 31, "runtime_error")
        self.assertEqual(
            result,
            {"deleted": 0, "staged_deleted": 0, "staged_days": 9, "analytics_deleted": 4, "retention_months": 1},
        )
        self.sweep.assert_awaited_once_with(self.session, 9)
        self.session.close.assert_awaited_once_with()
        self.assertEqual(self.session.exits, [RuntimeError])
        self.query.delete.assert_not_awaited()
        self.assertEqual(self.query.criteria, [])

    async def test_hostile_exception_is_not_formatted(self):
        class HostileError(Exception):
            def __str__(self):
                raise AssertionError("Raw errors must not be formatted")

        HostileError.__name__ = SECRET
        self.sweep.side_effect = HostileError()
        with self.assertLogs(self.logger, level="WARNING") as logs:
            await self.prune({})
        self.assert_private(logs, None, "unexpected_error")
        self.session.close.assert_awaited_once_with()

    async def test_sensitive_or_malformed_payload_id_is_not_logged(self):
        class HostileId:
            def __str__(self):
                raise AssertionError("Unexpected IDs must not be formatted")

            def __repr__(self):
                raise AssertionError("Unexpected IDs must not be represented")

        for value in (SECRET, True, None, 0, -1, 2**63, HostileId()):
            with self.subTest(kind=type(value).__name__):
                self.sweep.side_effect = RuntimeError(SECRET)
                with self.assertLogs(self.logger, level="WARNING") as logs:
                    await self.prune({"job_id": value, "private": SECRET})
                self.assert_private(logs, None, "runtime_error")

    async def test_success_keeps_default_days_counts_and_closes_session(self):
        with self.assertNoLogs(self.logger, level="WARNING"):
            result = await self.prune({"job_id": 31})
        self.assertEqual(
            result,
            {"deleted": 0, "staged_deleted": 3, "staged_days": 7, "analytics_deleted": 4, "retention_months": 1},
        )
        self.sweep.assert_awaited_once_with(self.session, 7)
        self.session.close.assert_awaited_once_with()
        self.query.delete.assert_not_awaited()
        self.assertEqual(self.query.criteria, [])

    async def test_transaction_entry_error_is_private_and_session_closes(self):
        self.session.enter_error = TimeoutError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            result = await self.prune({"job_id": 31})
        self.assert_private(logs, 31, "timeout")
        self.assertEqual(result["staged_deleted"], 0)
        self.sweep.assert_not_awaited()
        self.session.close.assert_awaited_once_with()

    async def test_close_failure_still_propagates(self):
        error = RuntimeError("synthetic close failure")
        self.session.close.side_effect = error
        with self.assertNoLogs(self.logger, level="WARNING"):
            with self.assertRaises(RuntimeError) as caught:
                await self.prune({})
        self.assertIs(caught.exception, error)

    async def test_base_exception_is_not_swallowed_or_logged(self):
        self.sweep.side_effect = KeyboardInterrupt()
        with self.assertNoLogs(self.logger, level="WARNING"):
            with self.assertRaises(KeyboardInterrupt):
                await self.prune({})
        self.session.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
