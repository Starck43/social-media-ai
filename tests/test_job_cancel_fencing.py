"""Prepared source-isolated cancellation tests; owner execution only.

Reuses existing session/route doubles without running their test cases. No app,
DB, provider or server startup. Real locking is covered in the separate DB file.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_job_claim_outcomes as manager_fixtures
import test_web_job_run_outcome as web_fixtures
from source_import_isolation import load_isolated_source

Redirect = web_fixtures.Redirect
ROOT = web_fixtures.ROOT
Router = web_fixtures.Router

CLAIMS = manager_fixtures.CLAIMS


class CancelManagerTests(unittest.IsolatedAsyncioTestCase):
    setUp = manager_fixtures.ManagerBoundaryTests.setUp

    async def project(self, *args, **kwargs):
        self.fail("Cancellation must not call the legacy Task projection")

    async def test_matching_snapshot_returns_only_after_commit_and_preserves_evidence(self):
        before = vars(self.stored).copy()
        requested_at = datetime.now(timezone.utc)
        self.assertTrue(await self.manager.cancel_running(self.claim))
        self.assertTrue(self.committed)
        self.assertTrue(self.queries[0].locked)
        self.assertEqual((self.stored.status, self.stored.error), ("failed", "Cancelled by operator"))
        self.assertGreaterEqual(self.stored.finished_at, requested_at)
        self.assertIsNotNone(self.stored.finished_at.utcoffset())
        for key, value in before.items():
            if key not in {"status", "error", "finished_at"}:
                self.assertEqual(getattr(self.stored, key), value, key)
        self.manager._record_task_result.assert_not_awaited()

    async def test_every_identity_component_and_running_status_is_required(self):
        for changes in (
            {"id": 8},
            {"tenant_id": 32},
            {"job_type": "reflect"},
            {"agent_task_id": None},
            {"attempts": 3},
            {"started_at": self.claim.started_at + timedelta(seconds=1)},
            {"status": "done"},
            {"status": "pending"},
            {"status": "failed"},
        ):
            with self.subTest(changes=changes):
                self.stored = manager_fixtures.row(**changes)
                before = vars(self.stored).copy()
                self.assertFalse(await self.manager.cancel_running(self.claim))
                self.assertEqual(vars(self.stored), before)
        self.stored = None
        self.assertFalse(await self.manager.cancel_running(self.claim))

    async def test_scope_missing_or_foreign_fails_before_session(self):
        for tenant in (None, 32):
            self.tenant_id = tenant
            with self.assertRaises(self.TenantError):
                await self.manager.cancel_running(self.claim)
        self.assertEqual(self.sessions, 0)

    async def test_bypass_still_requires_concrete_matching_tenant(self):
        self.bypass = True
        self.tenant_id = None
        self.stored.tenant_id = 32
        self.assertFalse(await self.manager.cancel_running(self.claim))
        self.assertEqual(self.stored.status, "running")

    async def test_heartbeat_is_not_generation(self):
        self.stored.locked_at += timedelta(minutes=1)
        self.assertTrue(await self.manager.cancel_running(self.claim))

    async def test_invalid_snapshot_is_rejected_before_session(self):
        with self.assertRaises(ValueError):
            await self.manager.cancel_running(SimpleNamespace(id=7))
        self.assertEqual(self.sessions, 0)

    async def test_commit_exception_is_not_success_or_loss_and_never_retried(self):
        before = vars(self.stored).copy()
        self.commit_error = ConnectionError("commit uncertain")
        with self.assertRaisesRegex(ConnectionError, "commit uncertain"):
            await self.manager.cancel_running(self.claim)
        self.assertEqual(self.sessions, 1)
        self.assertEqual(vars(self.stored), before)  # This double models rollback, not ACK loss.


class CancelRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse guard/scope setup only, not the existing run-route test methods.
        web_fixtures.JobRunOutcomeTests.setUp(self)
        self.job = manager_fixtures.row()
        self.get.return_value = self.job
        self.cancel = AsyncMock(return_value=True)
        self.lookup_tenant = 31
        self.source = load_isolated_source(
            "app.web._cancel_route_isolated",
            ROOT / "app/web/jobs.py",
            {
                "fastapi": SimpleNamespace(APIRouter=Router, Form=lambda default=None, **kw: default, Request=object),
                "fastapi.responses": SimpleNamespace(RedirectResponse=Redirect),
                "app.models.job": SimpleNamespace(
                    Job=SimpleNamespace(objects=SimpleNamespace(get=self.get, cancel_running=self.cancel))
                ),
                "app.web.deps": SimpleNamespace(
                    action_tenant_id=lambda request, tenant: self.lookup_tenant,
                    add_flash=lambda request, kind, text: self.flashes.append((kind, text)),
                    ensure_csrf=lambda *args: self.csrf,
                    guard_superuser=lambda *args, **kw: self.superuser_denial,
                    guard_web=lambda *args, **kw: self.permission_denial,
                    plural=lambda *args: "",
                    render=AsyncMock(),
                    tenant_filter_context=AsyncMock(),
                ),
                "app.core.tenant_context": SimpleNamespace(tenant_scope=self.scope),
                "app.jobs.claim_outcomes": CLAIMS,
            },
        )

    def scope(self, tenant_id, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def enter():
            self.scopes.append((tenant_id, kwargs))
            yield

        return enter()

    async def attempt(self):
        response = await self.source.job_cancel(self.request, 7, token="csrf", tenant_id=31)
        self.assertEqual((response.url, response.status_code), ("/app/jobs", 302))

    async def test_matching_cancel_uses_snapshot_and_reports_recorded_not_stopped(self):
        await self.attempt()
        self.get.assert_awaited_once_with(id=7, tenant_id=31)
        self.cancel.assert_awaited_once_with(CLAIMS.JobClaim.capture(self.job))
        self.assertEqual(
            self.flashes,
            [("success", "Отмена задания #7 записана. Выполнение внешних действий могло продолжиться.")],
        )

    async def test_lost_snapshot_has_error_not_success_and_no_retry(self):
        self.cancel.return_value = False
        await self.attempt()
        self.cancel.assert_awaited_once()
        self.assertEqual(
            self.flashes,
            [("error", "Отмена не выполнена: состояние запуска изменилось. Проверьте задание.")],
        )

    async def test_malformed_generation_fails_closed_without_manager_write(self):
        for changes in ({"attempts": 0}, {"started_at": None}, {"tenant_id": None}):
            with self.subTest(changes=changes):
                self.flashes.clear()
                self.get.return_value = manager_fixtures.row(**changes)
                await self.attempt()
                self.assertEqual(self.flashes[-1][0], "error")
        self.cancel.assert_not_awaited()

    async def test_missing_or_nonrunning_job_never_writes(self):
        for value in (None, manager_fixtures.row(status="done"), manager_fixtures.row(status="pending")):
            self.get.return_value = value
            await self.attempt()
            self.assertEqual(self.flashes[-1][0], "error")
        self.cancel.assert_not_awaited()

    async def test_commit_or_database_error_propagates_without_flash_or_retry(self):
        self.cancel.side_effect = ConnectionError("commit uncertain")
        with self.assertRaisesRegex(ConnectionError, "commit uncertain"):
            await self.attempt()
        self.assertEqual(self.flashes, [])
        self.cancel.assert_awaited_once()

    async def test_csrf_superuser_and_permission_guards_still_precede_lookup(self):
        for attribute, denial in (
            ("csrf", False),
            ("superuser_denial", Redirect("/app/", 302)),
            ("permission_denial", Redirect("/app/jobs", 302)),
        ):
            old = getattr(self, attribute)
            setattr(self, attribute, denial)
            await self.source.job_cancel(self.request, 7, token="csrf", tenant_id=31)
            setattr(self, attribute, old)
        self.get.assert_not_awaited()
        self.cancel.assert_not_awaited()

    async def test_global_operator_bypass_captures_rows_concrete_tenant(self):
        self.lookup_tenant = None
        await self.attempt()
        self.get.assert_awaited_once_with(id=7)
        self.assertEqual(self.cancel.await_args.args[0].tenant_id, 31)
        self.assertEqual(self.scopes, [(None, {"bypass": True})])


if __name__ == "__main__":
    unittest.main()
