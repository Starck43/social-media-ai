"""Actual-manager source regressions with local doubles; prepared, NOT run.

Owner command: python tests/test_job_stale_reap.py (no app/DB imports).
Pytest also loads the shared DB conftest; do not run it concurrently.
These tests check delegation, not PostgreSQL locking or queue-wide safety.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


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
        module = load_isolated_source(
            "app.models.managers._stale_reap_source",
            ROOT / "app/models/managers/job_manager.py",
            {"app.models.managers.base_manager": SimpleNamespace(BaseManager=DummyBase)},
        )
        module.datetime = FixedDatetime
        # __init__ would import the real model; this test only exercises reap.
        self.manager = object.__new__(module.JobManager)
        self.query = SimpleNamespace(update=AsyncMock(return_value=2))
        self.manager.filter = Mock(return_value=self.query)
        self.manager.update_by_id = AsyncMock()

    async def test_default_cutoff_and_single_conditional_update(self):
        self.assertEqual(await self.manager.reap_stale(), 2)
        self.manager.filter.assert_called_once_with(
            status="running", locked_at__lt=NOW - timedelta(minutes=30)
        )
        self.query.update.assert_awaited_once_with(status="pending", locked_at=None)
        self.manager.update_by_id.assert_not_awaited()

    async def test_custom_timeout_is_preserved(self):
        await self.manager.reap_stale(timeout_minutes=5)
        self.manager.filter.assert_called_once_with(
            status="running", locked_at__lt=NOW - timedelta(minutes=5)
        )

    async def test_return_actual_changed_count_not_candidate_count(self):
        self.query.update.return_value = 0
        self.assertEqual(await self.manager.reap_stale(), 0)
        self.query.update.assert_awaited_once()

    async def test_storage_failure_propagates_without_per_row_fallback(self):
        self.query.update.side_effect = RuntimeError("storage_write_failed")
        with self.assertRaisesRegex(RuntimeError, "storage_write_failed"):
            await self.manager.reap_stale()
        self.manager.update_by_id.assert_not_awaited()

    async def test_missing_tenant_scope_propagates_before_any_write(self):
        self.manager.filter.side_effect = RuntimeError("tenant_context_required")
        with self.assertRaisesRegex(RuntimeError, "tenant_context_required"):
            await self.manager.reap_stale()
        self.query.update.assert_not_awaited()
        self.manager.update_by_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()