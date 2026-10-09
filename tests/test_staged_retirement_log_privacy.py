"""Actual-source retirement helper tests with mocked storage; no DB acceptance.

Only the selected function is compiled, avoiding application startup. Run with
pytest in the owner's isolated test environment, or directly with Python.
"""

import ast
import asyncio
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

SECRET = "FAKE-TOKEN-PRIVATE-SQL-AND-CONTENT"


def module_with(**values):
    module = ModuleType("retirement_test_stub")
    module.__dict__.update(values)
    return module


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class StagedRetirementLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "app/jobs/handlers.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_retire_staged")
        namespace = {"Any": Any, "logger": logging.getLogger("retirement_privacy_test")}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        self.retire = namespace["_retire_staged"]
        self.logger = namespace["logger"]
        self.session = SimpleNamespace(begin=Mock(return_value=Transaction()), close=AsyncMock())
        self.new_session = Mock(return_value=self.session)
        self.delete_hashes = AsyncMock(return_value=2)
        self.hashes = [SECRET]
        self.analysed_hashes = Mock(return_value=self.hashes)
        modules = {
            "app.core.database": module_with(new_session=self.new_session),
            "app.models": module_with(CollectedItem=SimpleNamespace(objects=SimpleNamespace(delete_hashes=self.delete_hashes))),
            "app.services.ai.dedup": module_with(analysed_hashes=self.analysed_hashes),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_safe_warning(self, logs, source_id):
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.msg, "staged_retirement_failed source_id=%s error_code=storage_operation_failed")
        self.assertEqual(record.args, (source_id,))
        self.assertNotIn(SECRET, record.getMessage())
        self.assertNotIn(SECRET, repr(record.args))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)

    async def test_failure_is_private_and_preserves_zero_fallback_and_cleanup(self):
        self.delete_hashes.side_effect = RuntimeError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            self.assertEqual(await self.retire([SECRET], 23), 0)
        self.assert_safe_warning(logs, 23)
        self.delete_hashes.assert_awaited_once_with(self.session, 23, self.hashes)
        self.session.close.assert_awaited_once_with()

    async def test_exception_message_and_custom_class_name_are_never_formatted(self):
        class DangerousError(Exception):
            def __str__(self):
                raise AssertionError("Logging must not stringify storage exceptions")

        DangerousError.__name__ = SECRET
        self.delete_hashes.side_effect = DangerousError()
        with self.assertLogs(self.logger, level="WARNING") as logs:
            self.assertEqual(await self.retire([SECRET], 23), 0)
        self.assert_safe_warning(logs, 23)
        self.session.close.assert_awaited_once_with()

    async def test_identifiers_are_bounded_without_stringifying_unexpected_values(self):
        class DangerousId:
            def __str__(self):
                raise AssertionError("Logging must not stringify unexpected IDs")

            def __repr__(self):
                raise AssertionError("Logging must not repr unexpected IDs")

        self.delete_hashes.side_effect = RuntimeError(SECRET)
        for source_id in (SECRET, True, None, 0, -1, 2**63, 2**100, DangerousId()):
            with self.assertLogs(self.logger, level="WARNING") as logs:
                self.assertEqual(await self.retire([SECRET], source_id), 0)
            self.assert_safe_warning(logs, None)
        self.assertEqual(self.session.close.await_count, 8)

    async def test_valid_identifier_boundaries_remain_correlatable(self):
        self.delete_hashes.side_effect = RuntimeError(SECRET)
        for source_id in (1, 2**63 - 1):
            with self.assertLogs(self.logger, level="WARNING") as logs:
                self.assertEqual(await self.retire([SECRET], source_id), 0)
            self.assert_safe_warning(logs, source_id)

    async def test_success_passes_hashes_and_returns_storage_count_unchanged(self):
        analytics = [SECRET]
        self.assertEqual(await self.retire(analytics, 23), 2)
        self.analysed_hashes.assert_called_once_with(analytics)
        self.delete_hashes.assert_awaited_once_with(self.session, 23, self.hashes)
        self.session.begin.assert_called_once_with()
        self.session.close.assert_awaited_once_with()

    async def test_no_covered_hashes_never_opens_storage(self):
        self.analysed_hashes.return_value = []
        self.assertEqual(await self.retire([], 23), 0)
        self.new_session.assert_not_called()
        self.delete_hashes.assert_not_awaited()
        self.session.close.assert_not_awaited()

    async def test_cancellation_propagates_and_closes_session(self):
        self.delete_hashes.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.retire([SECRET], 23)
        self.session.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
