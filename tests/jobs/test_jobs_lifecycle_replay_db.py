"""PostgreSQL acceptance for built-in Job retention and immutable replay admission.

Uses only the reviewed test schema, generated fixture rows, and local database APIs.
No schema creation/reset, migrations, provider calls, or external writes.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.setup_test_db import resolve_and_redirect

resolve_and_redirect()  # Refuses any schema other than the configured test schema.

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs.lifecycle import cleanup_terminal_job_history
from app.jobs.recovery_policy import OPERATOR_CANCELLED_ERROR, OUTCOME_UNCONFIRMED_PREFIX
from app.models import AgentTask, Job, Platform, PlatformRole, Source, Tenant, User
from app.models.managers.job_manager import JobManager
from app.types import PlatformType, SourceType

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


class JobLifecycleReplayDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.platform = None
        self.user = None
        self.addAsyncCleanup(self.cleanup_rows)
        for _ in range(2):
            self.tenants.append(
                await Tenant.objects.create(
                    name="Job lifecycle acceptance", slug=f"job-lifecycle-{uuid4().hex}", plan="business"
                )
            )
        self.own, self.other = self.tenants
        self.platform = await Platform.objects.create(
            name=f"joblife-{uuid4().hex[:12]}",
            platform_type=PlatformType.VK,
            base_url="https://example.invalid",
            params={},
        )
        platform_user_role = await PlatformRole.objects.filter(codename="user").first()
        if platform_user_role is None:
            raise RuntimeError("test schema must contain the seeded platform user role")
        self.user = await User.objects.create_user(
            username=f"joblife-{uuid4().hex[:12]}",
            password="not-a-real-login",
            email=f"{uuid4().hex}@example.invalid",
            role_id=platform_user_role.id,
        )
        with tenant_scope(bypass=True):
            self.source = await Source.objects.create(
                tenant_id=self.own.id,
                platform_id=self.platform.id,
                name="Replay fixture source",
                external_id=f"joblife-{uuid4().hex[:12]}",
                source_type=SourceType.GROUP,
                is_active=True,
                params={},
            )

    async def cleanup_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
            if self.user is not None:
                await User.objects.delete_user(self.user.id)
        finally:
            from app.core.database import async_engine

            await async_engine.dispose()

    async def create_task(self, *, job_type="prune", payload=None, is_active=True, sources=None, cron_expr="@once"):
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                tenant_id=self.own.id,
                name=f"lifecycle-{uuid4().hex}",
                job_type=job_type,
                cron_expr=cron_expr,
                payload=payload or {},
                is_active=is_active,
            )
            if sources:
                await AgentTask.objects.set_sources(task.id, [source.id for source in sources])
            return task

    async def create_job(self, tenant, **changes):
        # The suite runs under the platform-owner bypass, where BaseManager stamps
        # an omitted tenant_id with the bootstrap workspace instead of the scope
        # tenant; name the owner explicitly so the row lands where the contract
        # expects it.
        values = {
            "tenant_id": tenant.id,
            "job_type": "learn",
            "payload": {},
            "run_at": NOW,
            "status": "done",
            "finished_at": NOW - timedelta(hours=100),
            "attempts": 1,
            "max_attempts": 3,
            "result": {},
        }
        explicit_tenant = changes.get("tenant_id")
        if explicit_tenant is not None and explicit_tenant != tenant.id:
            raise ValueError(f"create_job tenant {tenant.id} conflicts with explicit tenant_id {explicit_tenant}")
        with tenant_scope(tenant.id):
            return await Job.objects.create(**(values | changes))

    async def test_retention_override_scope_protection_and_deactivation(self):
        policy = await self.create_task(
            payload={"mode": "job_history", "success_hours": 48, "failure_hours": 72},
            cron_expr="0 9 * * *",
        )
        retained_30h = await self.create_job(self.own, finished_at=NOW - timedelta(hours=30))
        deleted_success_60h = await self.create_job(self.own, finished_at=NOW - timedelta(hours=60))
        deleted_failure_90h = await self.create_job(
            self.own, status="failed", finished_at=NOW - timedelta(hours=90), error="ordinary failure"
        )
        retained_review_100h = await self.create_job(
            self.own,
            status="failed",
            finished_at=NOW - timedelta(hours=100),
            error="ordinary failure",
            result={"diagnostic_code": "partial_delivery_requires_review"},
        )
        retained_cancelled = await self.create_job(
            self.own,
            status="failed",
            finished_at=NOW - timedelta(hours=100),
            error=OPERATOR_CANCELLED_ERROR,
        )
        retained_pending = await self.create_job(self.own, status="pending", finished_at=NOW - timedelta(hours=100))
        retained_running = await self.create_job(self.own, status="running", finished_at=NOW - timedelta(hours=100))
        foreign = await self.create_job(self.other, finished_at=NOW - timedelta(hours=60))

        with tenant_scope(self.own.id):
            self.assertEqual(await cleanup_terminal_job_history(tenant_id=self.own.id, now=NOW), 2)
            self.assertEqual(await cleanup_terminal_job_history(tenant_id=self.own.id, now=NOW), 0)
            for row in (retained_30h, retained_review_100h, retained_cancelled, retained_pending, retained_running):
                self.assertIsNotNone(await Job.objects.get(id=row.id))
            for row in (deleted_success_60h, deleted_failure_90h):
                self.assertIsNone(await Job.objects.get(id=row.id))
        with tenant_scope(self.other.id):
            self.assertIsNotNone(await Job.objects.get(id=foreign.id))

        with tenant_scope(bypass=True):
            await AgentTask.objects.update_by_id(policy.id, is_active=False)
        reverted = await self.create_job(self.own, finished_at=NOW - timedelta(hours=30))
        with tenant_scope(self.own.id):
            # Deactivation restores the 24-hour built-in default, so both
            # 30-hour successful rows become eligible.
            self.assertEqual(await cleanup_terminal_job_history(tenant_id=self.own.id, now=NOW), 2)
            self.assertIsNone(await Job.objects.get(id=retained_30h.id))
            self.assertIsNone(await Job.objects.get(id=reverted.id))

    async def test_overview_counts_quarantined_rows_within_the_active_tenant_only(self):
        quarantined = await self.create_job(self.own, status="failed", error=f"{OUTCOME_UNCONFIRMED_PREFIX}lease lost")
        cancelled = await self.create_job(self.own, status="failed", error=OPERATOR_CANCELLED_ERROR)
        ordinary = await self.create_job(self.own, status="failed", error="ordinary failure")
        foreign_marker = f"foreign-{uuid4().hex}"
        foreign = await self.create_job(
            self.other, status="failed", error=f"{OUTCOME_UNCONFIRMED_PREFIX}{foreign_marker}"
        )

        # Fixture visibility: the three own failed rows must be readable from the
        # own tenant scope before the aggregate is exercised, so a zero count
        # points at the overview query rather than at the created rows.
        with tenant_scope(self.own.id):
            for row in (quarantined, cancelled, ordinary):
                stored = await Job.objects.get(id=row.id)
                self.assertIsNotNone(stored)
                self.assertEqual(stored.tenant_id, self.own.id)
                self.assertEqual(stored.status, "failed")

        with tenant_scope(self.own.id):
            stats, signals = await JobManager().overview_for_web(self.own.id, now=NOW)

        self.assertEqual(stats["failed"], 3)
        self.assertEqual(signals["quarantined"], 1)
        # The generic review count keeps the recovery vocabulary: quarantine and
        # operator cancel both require a manual effect check; the foreign row never counts.
        self.assertEqual(signals["unconfirmed_failed"], 2)
        self.assertNotIn(foreign_marker, repr((stats, signals)))
        self.assertIsNotNone(await Job.objects.get(id=quarantined.id))
        self.assertIsNotNone(await Job.objects.get(id=cancelled.id))
        with tenant_scope(self.other.id):
            self.assertIsNotNone(await Job.objects.get(id=foreign.id))

    async def test_active_page_includes_attention_and_clean_history_excludes_it(self):
        clean = await self.create_job(self.own, result={"new_items": 3})
        staged_error = await self.create_job(self.own, result={"staged_errors": 2})
        review = await self.create_job(self.own, result={"review_required": True})
        malformed = await self.create_job(self.own, result={"errors": {"unexpected": "shape"}})

        with tenant_scope(self.own.id):
            active_rows, active_total = await JobManager().page_for_web(
                self.own.id,
                statuses=["pending", "running", "failed"],
                page=1,
                include_attention=True,
            )
            history_rows, history_total = await JobManager().page_for_web(
                self.own.id,
                statuses=["done"],
                page=1,
                include_attention=False,
            )
            stats, _signals = await JobManager().overview_for_web(self.own.id, now=NOW)

        self.assertEqual(active_total, 3)
        self.assertEqual({row.id for row in active_rows}, {staged_error.id, review.id, malformed.id})
        self.assertEqual(history_total, 1)
        self.assertEqual([row.id for row in history_rows], [clean.id])
        self.assertEqual(stats["attention"], 3)

    async def test_replay_inserts_detached_job_and_preserves_parent_and_actor(self):
        task = await self.create_task(
            job_type="collect",
            payload={"cli_dates": {"start_date": "2026-01-01", "end_date": "2026-02-01"}},
            sources=[self.source],
        )
        frozen = {
            "version": 1,
            "task_payload": {
                "source_ids": [self.source.id],
                "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-02-01"},
            },
            "scenario_id": None,
            "action_type": None,
        }
        parent = await self.create_job(
            self.own,
            job_type="collect",
            agent_task_id=task.id,
            payload={"source_ids": [self.source.id], "_effective_snapshot": frozen},
            status="failed",
            error="ordinary failure",
            result={"collected": 2},
        )
        original = (parent.status, parent.error, dict(parent.payload), dict(parent.result))
        with tenant_scope(self.own.id), permission_scope(self.user, is_owner=True):
            replay = await JobManager().enqueue_replay(parent.id, initiated_by_user_id=self.user.id)
            with self.assertRaisesRegex(ValueError, "уже поставлен"):
                await JobManager().enqueue_replay(parent.id, initiated_by_user_id=self.user.id)
        self.assertNotEqual(replay.id, parent.id)
        self.assertIsNone(replay.agent_task_id)
        self.assertEqual(replay.initiated_by_user_id, self.user.id)
        self.assertEqual(replay.payload["retry_of_job_id"], parent.id)
        self.assertEqual(replay.payload["retry_of_task_id"], task.id)
        self.assertEqual(replay.payload["_effective_snapshot"], frozen)
        with tenant_scope(self.own.id):
            stored = await Job.objects.get(id=parent.id)
            self.assertEqual((stored.status, stored.error, stored.payload, stored.result), original)

    async def test_replay_refuses_occupied_source_and_wrong_actor(self):
        task = await self.create_task(job_type="analyze", payload={}, sources=[self.source])
        parent = await self.create_job(
            self.own,
            job_type="analyze",
            agent_task_id=task.id,
            payload={
                "source_ids": [self.source.id],
                "_effective_snapshot": {
                    "version": 1,
                    "task_payload": {
                        "source_ids": [self.source.id],
                        "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-02-01"},
                    },
                    "action_type": None,
                },
            },
            status="failed",
        )
        await self.create_job(
            self.own,
            job_type="collect",
            payload={"source_ids": [self.source.id]},
            status="pending",
            finished_at=None,
        )
        with tenant_scope(self.own.id), permission_scope(self.user, is_owner=True):
            with self.assertRaisesRegex(ValueError, "уже есть"):
                await JobManager().enqueue_replay(parent.id, initiated_by_user_id=self.user.id)
            with self.assertRaisesRegex(Exception, "authenticated workspace actor"):
                await JobManager().enqueue_replay(parent.id, initiated_by_user_id=self.user.id + 1)


if __name__ == "__main__":
    unittest.main()
