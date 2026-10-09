"""Actual-source attempt-count helper tests with mocked storage; not DB acceptance.

Compile only the selected helper, without application startup. Owner/local agent
runs these cases; authoring this file does not constitute executed checks.
"""

import ast
import asyncio
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

SECRET = "FAKE-TOKEN-PRIVATE-SQL-AND-CONTENT"


def module_with(**values):
    module = ModuleType("attempt_count_test_stub")
    module.__dict__.update(values)
    return module


class Transaction:
    def __init__(self):
        self.exit = Mock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exit(*args)
        return False


class StagedAttemptLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "app/jobs/handlers.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        node = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_count_failed_staged"
        )
        namespace = {"logger": logging.getLogger("attempt_count_privacy_test")}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        self.count_failed = namespace["_count_failed_staged"]
        self.logger = namespace["logger"]
        self.transaction = Transaction()
        self.session = SimpleNamespace(begin=Mock(return_value=self.transaction), close=AsyncMock())
        self.new_session = Mock(return_value=self.session)
        self.record_attempts = AsyncMock(return_value=2)
        self.rows = [SimpleNamespace(content_hash=SECRET)]
        modules = {
            "app.core.database": module_with(new_session=self.new_session),
            "app.models": module_with(
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(record_attempts=self.record_attempts))
            ),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_safe_warning(self, logs, source_id):
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(
            record.msg,
            "staged_attempt_count_failed source_id=%s error_code=storage_operation_failed",
        )
        self.assertEqual(record.args, (source_id,))
        self.assertNotIn(SECRET, record.getMessage())
        self.assertNotIn(SECRET, repr(record.args))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)

    async def test_failure_warning_is_private_and_preserves_fallback_transaction_and_cleanup(self):
        error = RuntimeError(SECRET)
        self.record_attempts.side_effect = error
        with self.assertLogs(self.logger, level="WARNING") as logs:
            self.assertEqual(await self.count_failed(23, self.rows), 0)
        self.assert_safe_warning(logs, 23)
        self.record_attempts.assert_awaited_once_with(self.session, 23, [SECRET])
        self.transaction.exit.assert_called_once()
        self.assertIs(self.transaction.exit.call_args.args[1], error)
        self.session.close.assert_awaited_once_with()

    async def test_exception_message_and_custom_class_name_are_never_formatted(self):
        class DangerousError(Exception):
            def __str__(self):
                raise AssertionError("Logging must not stringify storage exceptions")

        DangerousError.__name__ = SECRET
        self.record_attempts.side_effect = DangerousError()
        with self.assertLogs(self.logger, level="WARNING") as logs:
            self.assertEqual(await self.count_failed(23, self.rows), 0)
        self.assert_safe_warning(logs, 23)
        self.session.close.assert_awaited_once_with()

    async def test_invalid_identifiers_are_bounded_without_stringification(self):
        class DangerousId:
            def __str__(self):
                raise AssertionError("Logging must not stringify unexpected IDs")

            def __repr__(self):
                raise AssertionError("Logging must not repr unexpected IDs")

        self.record_attempts.side_effect = RuntimeError(SECRET)
        for source_id in (SECRET, True, None, 0, -1, 2**63, 2**100, DangerousId()):
            with self.assertLogs(self.logger, level="WARNING") as logs:
                self.assertEqual(await self.count_failed(source_id, self.rows), 0)
            self.assert_safe_warning(logs, None)
        self.assertEqual(self.session.close.await_count, 8)

    async def test_valid_identifier_bounds_remain_correlatable(self):
        self.record_attempts.side_effect = RuntimeError(SECRET)
        for source_id in (1, 2**63 - 1):
            with self.assertLogs(self.logger, level="WARNING") as logs:
                self.assertEqual(await self.count_failed(source_id, self.rows), 0)
            self.assert_safe_warning(logs, source_id)

    async def test_success_preserves_hash_filtering_order_duplicates_and_storage_count(self):
        rows = [
            SimpleNamespace(content_hash=SECRET),
            SimpleNamespace(content_hash=None),
            SimpleNamespace(),
            SimpleNamespace(content_hash=""),
            SimpleNamespace(content_hash="SECOND-FAKE-HASH"),
            SimpleNamespace(content_hash=SECRET),
        ]
        with self.assertNoLogs(self.logger, level="WARNING"):
            self.assertEqual(await self.count_failed(23, rows), 2)
        self.record_attempts.assert_awaited_once_with(self.session, 23, [SECRET, "SECOND-FAKE-HASH", SECRET])
        self.session.begin.assert_called_once_with()
        self.transaction.exit.assert_called_once_with(None, None, None)
        self.session.close.assert_awaited_once_with()

    async def test_no_hashes_never_opens_storage(self):
        for rows in ([], [SimpleNamespace(), SimpleNamespace(content_hash=None), SimpleNamespace(content_hash="")]):
            self.assertEqual(await self.count_failed(23, rows), 0)
        self.new_session.assert_not_called()
        self.record_attempts.assert_not_awaited()
        self.session.close.assert_not_awaited()

    async def test_transaction_entry_failure_is_private_and_closes_session(self):
        self.session.begin.side_effect = TimeoutError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            self.assertEqual(await self.count_failed(23, self.rows), 0)
        self.assert_safe_warning(logs, 23)
        self.record_attempts.assert_not_awaited()
        self.session.close.assert_awaited_once_with()

    async def test_cancellation_propagates_and_closes_session(self):
        self.record_attempts.side_effect = asyncio.CancelledError()
        with self.assertNoLogs(self.logger, level="WARNING"):
            with self.assertRaises(asyncio.CancelledError):
                await self.count_failed(23, self.rows)
        self.session.close.assert_awaited_once_with()

    async def test_close_failure_keeps_existing_propagation_behavior(self):
        close_error = RuntimeError("FAKE-CLOSE-ERROR")
        self.session.close.side_effect = close_error
        with self.assertRaises(RuntimeError) as caught:
            await self.count_failed(23, self.rows)
        self.assertIs(caught.exception, close_error)
        self.record_attempts.assert_awaited_once_with(self.session, 23, [SECRET])
        self.session.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
