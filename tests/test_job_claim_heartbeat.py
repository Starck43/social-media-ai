"""Actual manager/claim source, stdlib module-local SQL/session/context doubles.

Run: python tests/test_job_claim_heartbeat.py
No app bootstrap/DB/providers; this is not PostgreSQL atomicity acceptance.
"""

import asyncio
import copy
import unittest
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = load_isolated_source("_heartbeat_claims", ROOT / "app/jobs/claim_outcomes.py")
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class TenantContextError(RuntimeError):
    pass


class BaseManager:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, model):
        self.model = model


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value

    def is_not_distinct_from(self, value):
        return self.name, value


class Update:
    def __init__(self, model):
        self.model = model
        self.criteria = []
        self.changes = {}
        self.returned = None

    def where(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def values(self, **changes):
        self.changes = changes
        return self

    def returning(self, column):
        self.returned = column
        return self


class Transaction:
    def __init__(self, fixture):
        self.fixture = fixture

    async def __aenter__(self):
        self.before = copy.deepcopy(self.fixture.row)
        return self

    async def __aexit__(self, kind, error, traceback):
        f = self.fixture
        if f.release is not None:
            f.ready.set()
            await f.release.wait()
        if kind is not None or f.commit_error is not None:
            f.row = self.before
            if f.commit_error is not None:
                raise f.commit_error
        else:
            f.committed = True
        return False


class Session:
    def __init__(self, fixture):
        self.fixture = fixture
        self.execute = AsyncMock(side_effect=self.executed)

    def begin(self):
        return Transaction(self.fixture)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def executed(self, query):
        f = self.fixture
        f.query = query
        matched = all(f.row.get(name) == value for name, value in query.criteria)
        if matched:
            expression = query.changes["locked_at"]
            assert expression[0] == "greatest"
            f.row["locked_at"] = max(value for value in (f.row["locked_at"], expression[2]) if value is not None)
        return SimpleNamespace(scalar_one_or_none=lambda: f.row["id"] if matched else None)


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.claim = CLAIMS.JobClaim(7, 31, "learn", None, 2, NOW)
        self.row = dict(id=7, tenant_id=31, job_type="learn", agent_task_id=None, status="running",
                        attempts=2, started_at=NOW, locked_at=NOW, updated_at=NOW, max_attempts=3,
                        result={"retained": True}, error="retained", llm_cost=1.25, run_at=NOW,
                        finished_at=None, payload={"opaque": True})
        self.tenant = ContextVar("test_tenant", default=31)
        self.bypass = ContextVar("test_bypass", default=False)
        self.committed = False
        self.commit_error = None
        self.release = None
        self.ready = asyncio.Event()
        self.query = None
        self.session = Session(self)
        self.factory = Mock(return_value=self.session)
        self.model = type("JobModel", (), {key: Column(key) for key in [
            "id", "tenant_id", "job_type", "agent_task_id", "status", "attempts", "started_at", "updated_at", "locked_at"
        ]})
        imports = {
            "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManager),
            "app.models.job": SimpleNamespace(Job=self.model),
            "sqlalchemy": SimpleNamespace(update=Update, func=SimpleNamespace(greatest=lambda column, stamp: ("greatest", column, stamp))),
            "app.core.database": SimpleNamespace(async_session_maker=self.factory),
            "app.core.tenant_context": SimpleNamespace(TenantContextError=TenantContextError,
                current_tenant_id=self.tenant.get, is_bypass=self.bypass.get),
            "app.jobs.claim_outcomes": SimpleNamespace(JobClaim=CLAIMS.JobClaim),
        }
        self.module = load_isolated_source("app.models.managers._heartbeat_check", ROOT / "app/models/managers/job_manager.py", imports)
        self.manager = self.module.JobManager()

    async def test_matching_claim_renews_only_lease_after_commit(self):
        before = copy.deepcopy(self.row)
        newer = NOW + timedelta(minutes=5)
        self.assertIs(await self.manager.renew_claim(self.claim, now=newer), True)
        self.assertTrue(self.committed)
        before["locked_at"] = newer
        self.assertEqual(self.row, before)
        self.assertEqual(set(self.query.changes), {"locked_at", "updated_at"})
        self.assertIs(self.query.changes["updated_at"], self.model.updated_at)

    async def test_late_or_null_lease_is_monotonic_without_false_claim_loss(self):
        self.row["locked_at"] = NOW + timedelta(seconds=20)
        self.assertIs(await self.manager.renew_claim(self.claim, now=NOW), True)
        self.assertEqual(self.row["locked_at"], NOW + timedelta(seconds=20))
        self.row["locked_at"] = None
        self.assertIs(await self.manager.renew_claim(self.claim, now=NOW), True)
        self.assertEqual(self.row["locked_at"], NOW)

    async def test_full_claim_and_running_predicates_are_in_atomic_update(self):
        await self.manager.renew_claim(self.claim, now=NOW)
        self.assertEqual(self.query.criteria, [
            ("id", 7), ("tenant_id", 31), ("job_type", "learn"), ("agent_task_id", None),
            ("status", "running"), ("attempts", 2), ("started_at", NOW),
        ])
        self.assertIs(self.query.returned, self.model.id)

    async def test_each_changed_identity_or_status_prevents_renewal(self):
        for key, value in [("id", 8), ("tenant_id", 99), ("job_type", "collect"),
                           ("agent_task_id", 9), ("status", "pending"), ("attempts", 3),
                           ("started_at", NOW + timedelta(seconds=1))]:
            with self.subTest(field=key):
                self.setUp()
                self.row[key] = value
                before = copy.deepcopy(self.row)
                self.assertIs(await self.manager.renew_claim(self.claim, now=NOW + timedelta(seconds=20)), False)
                self.assertEqual(self.row, before)

    async def test_terminal_rows_are_not_revived(self):
        for status in ("done", "failed"):
            with self.subTest(status=status):
                self.setUp()
                self.row["status"] = status
                self.assertIs(await self.manager.renew_claim(self.claim), False)
                self.assertEqual(self.row["locked_at"], NOW)

    async def test_missing_malformed_or_foreign_scope_fails_before_sql(self):
        for tenant in (None, 0, -1, True, "31", 31.0, 99):
            with self.subTest(tenant=tenant):
                self.setUp()
                self.tenant.set(tenant)
                with self.assertRaises(TenantContextError):
                    await self.manager.renew_claim(self.claim)
                self.factory.assert_not_called()
                self.session.execute.assert_not_awaited()

    async def test_explicit_bypass_still_binds_one_claim_tenant(self):
        self.tenant.set(99)
        self.bypass.set(True)
        self.assertIs(await self.manager.renew_claim(self.claim, now=NOW), True)
        self.assertIn(("tenant_id", 31), self.query.criteria)

    async def test_non_null_task_is_part_of_claim_identity(self):
        self.claim = replace(self.claim, agent_task_id=9)
        self.assertIs(await self.manager.renew_claim(self.claim, now=NOW), False)
        self.row["agent_task_id"] = 9
        self.assertIs(await self.manager.renew_claim(self.claim, now=NOW), True)

    async def test_unvalidated_claim_fails_before_sql(self):
        for claim in (None, SimpleNamespace(**self.row)):
            with self.subTest(claim_type=type(claim).__name__), self.assertRaises(ValueError):
                await self.manager.renew_claim(claim)
        self.factory.assert_not_called()

    async def test_invalid_timestamp_fails_before_sql(self):
        for stamp in (NOW.replace(tzinfo=None), "now", False):
            with self.subTest(stamp_type=type(stamp).__name__), self.assertRaises(ValueError):
                await self.manager.renew_claim(self.claim, now=stamp)
        self.factory.assert_not_called()

    async def test_default_timestamp_is_aware(self):
        self.assertIs(await self.manager.renew_claim(self.claim), True)
        self.assertIsNotNone(self.row["locked_at"].utcoffset())

    async def test_ack_is_not_returned_before_commit_completes(self):
        self.release = asyncio.Event()
        task = asyncio.create_task(self.manager.renew_claim(self.claim, now=NOW))
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        self.assertFalse(task.done())
        self.assertFalse(self.committed)
        self.release.set()
        self.assertIs(await task, True)
        self.assertTrue(self.committed)

    async def test_commit_error_is_not_misreported_as_claim_loss(self):
        self.commit_error = RuntimeError("injected commit failure")
        before = copy.deepcopy(self.row)
        with self.assertRaises(RuntimeError):
            await self.manager.renew_claim(self.claim, now=NOW + timedelta(seconds=20))
        self.assertEqual(self.row, before)
        self.assertFalse(self.committed)

    async def test_sql_error_propagates_without_ack(self):
        self.session.execute.side_effect = RuntimeError("injected SQL failure")
        with self.assertRaises(RuntimeError):
            await self.manager.renew_claim(self.claim)
        self.assertFalse(self.committed)

    async def test_actual_cancellation_propagates_without_ack(self):
        self.session.execute.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.manager.renew_claim(self.claim)
        self.assertFalse(self.committed)


if __name__ == "__main__":
    unittest.main()
