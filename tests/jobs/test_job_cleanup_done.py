"""Prepared actual-manager delegation checks; owner execution only.

No application/DB imports. These doubles verify a conditional DELETE delegation,
not PostgreSQL locking. No old tests removed or full-suite rerun requested.
"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import test_job_stale_reap as fixtures
from source_import_isolation import load_isolated_source


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        module = load_isolated_source(
            "app.models.managers._cleanup_source",
            fixtures.ROOT / "app/models/managers/job_manager.py",
            {"app.models.managers.base_manager": SimpleNamespace(BaseManager=fixtures.DummyBase)},
        )
        module.datetime = fixtures.FixedDatetime
        self.manager = object.__new__(module.JobManager)
        # Intentionally not awaitable/iterable: a read-before-delete must fail.
        self.query = SimpleNamespace(delete=AsyncMock(return_value=2))
        self.manager.filter = Mock(return_value=self.query)
        self.manager.delete = AsyncMock(side_effect=AssertionError("No per-row fallback"))

    async def test_default_cutoff_is_in_single_delete_and_returns_actual_count(self):
        self.assertEqual(await self.manager.cleanup_done(), 2)
        self.manager.filter.assert_called_once_with(status="done", finished_at__lt=fixtures.NOW - timedelta(hours=24))
        self.query.delete.assert_awaited_once_with()
        self.manager.delete.assert_not_awaited()

    async def test_custom_retention_is_unchanged(self):
        await self.manager.cleanup_done(older_than_hours=2)
        self.manager.filter.assert_called_once_with(status="done", finished_at__lt=fixtures.NOW - timedelta(hours=2))

    async def test_zero_retains_existing_default_semantics(self):
        await self.manager.cleanup_done(older_than_hours=0)
        self.manager.filter.assert_called_once_with(status="done", finished_at__lt=fixtures.NOW - timedelta(hours=24))

    async def test_no_actual_deletion_returns_zero_not_candidate_count(self):
        self.query.delete.return_value = 0
        self.assertEqual(await self.manager.cleanup_done(), 0)
        self.query.delete.assert_awaited_once_with()

    async def test_database_or_commit_error_is_not_count_or_per_row_retry(self):
        self.query.delete.side_effect = ConnectionError("cleanup outcome uncertain")
        with self.assertRaisesRegex(ConnectionError, "cleanup outcome uncertain"):
            await self.manager.cleanup_done()
        self.query.delete.assert_awaited_once_with()
        self.manager.delete.assert_not_awaited()

    async def test_missing_scope_fails_before_delete(self):
        self.manager.filter.side_effect = RuntimeError("tenant scope required")
        with self.assertRaisesRegex(RuntimeError, "tenant scope required"):
            await self.manager.cleanup_done()
        self.query.delete.assert_not_awaited()
        self.manager.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
