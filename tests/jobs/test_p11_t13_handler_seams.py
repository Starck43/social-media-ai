"""Actual digest/prune implementations and facade routing, no sends or DB."""

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


class HandlerSeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.publish = AsyncMock(return_value={"status": "ok"})
        self.module = load_isolated_source(
            "_p11_digest",
            ROOT / "app/jobs/job_handlers/digest.py",
            {
                "app.services.digest.builder": NS(build_and_publish=self.publish),
            },
        )

    async def test_task_scope_and_options_win_over_payload(self):
        task = NS(sources=[NS(id=4)], agent_scenario_id=8)
        load = AsyncMock(return_value=task)
        result = await self.module.handle_digest(
            {"agent_task_id": 2, "period": "day", "scenario_id": 10},
            load_task=load,
            task_payload=lambda row: {"period": "week", "group_by": "sources", "time_breakdown": True},
        )
        self.assertEqual(result, {"status": "ok"})
        load.assert_awaited_once_with(2)
        self.publish.assert_awaited_once_with(
            period="week",
            agent_task_id=2,
            force=False,
            source_ids=[4],
            group_by="sources",
            time_breakdown=True,
            scenario_id=10,
        )

    async def test_invalid_period_never_publishes(self):
        result = await self.module.handle_digest(
            {"period": "invalid"}, load_task=AsyncMock(return_value=None), task_payload=lambda task: {}
        )
        self.assertEqual(result, {"status": "failed", "error": "Invalid period: invalid"})
        self.publish.assert_not_awaited()

    async def test_public_dispatch_stays_on_the_facade(self):
        tree = ast.parse((ROOT / "app/jobs/handlers.py").read_text())
        for name in ("handle_digest", "handle_prune"):
            function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            self.assertEqual([a.arg for a in function.args.args], ["payload"])
            self.assertTrue(
                any(
                    isinstance(n, ast.ImportFrom) and n.module.startswith("app.jobs.job_handlers.")
                    for n in ast.walk(function)
                )
            )
        registry = next(
            n.value
            for n in tree.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HANDLERS" for t in n.targets)
        )
        self.assertEqual(
            {k.value: v.id for k, v in zip(registry.keys, registry.values)},
            {
                "collect": "handle_collect",
                "analyze": "handle_analyze",
                "digest": "handle_digest",
                "prune": "handle_prune",
                "learn": "handle_learn",
                "reflect": "handle_reflect",
            },
        )


class PruneSeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.audit = AsyncMock(return_value=2)
        self.staged = AsyncMock(return_value=1)
        self.analytics = AsyncMock(return_value=3)
        self.cleanup_jobs = AsyncMock(return_value=5)
        self.logger = MagicMock()
        self.session = MagicMock()
        self.session.close = AsyncMock()
        self.cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.module = load_isolated_source(
            "_p11_prune",
            ROOT / "app/jobs/job_handlers/prune.py",
            {
                "app.core.config": NS(settings=NS(DATA_RETENTION_MONTHS=1)),
                "app.core.database": NS(new_session=lambda: self.session),
                "app.core.tenant_context": NS(current_tenant_id=lambda: 7),
                "app.models": NS(
                    ActionAuditEvent=NS(objects=NS(delete_expired=self.audit)),
                    CollectedItem=NS(objects=NS(delete_older_than=self.staged)),
                    AIAnalytics=NS(objects=NS(delete_expired=self.analytics)),
                ),
                "app.utils.retention": NS(months_before=lambda *_: self.cutoff),
                "app.jobs.lifecycle": NS(
                    retention_policy=lambda _: (24, 72), cleanup_terminal_job_history=self.cleanup_jobs
                ),
            },
        )

    async def prune(self, payload):
        return await self.module.handle_prune(
            payload, logger=self.logger, log_id=lambda value: value, error_kind=lambda _: "runtime_error"
        )

    async def test_default_retention_keeps_audit_and_content_results(self):
        result = await self.prune({"job_id": 23})
        self.assertEqual(result["action_audit_deleted"], 2)
        self.assertEqual(result["analytics_deleted"], 3)
        self.assertEqual(result["staged_deleted"], 1)
        self.audit.assert_awaited_once()
        self.assertIsNotNone(self.audit.await_args.kwargs["now"].utcoffset())
        self.analytics.assert_awaited_once_with(self.cutoff)
        self.session.close.assert_awaited_once()
        self.logger.warning.assert_not_called()

    async def test_job_history_mode_keeps_audit_counts_and_policy(self):
        result = await self.prune({"mode": "job_history"})
        self.assertEqual(
            result,
            {
                "status": "policy_saved",
                "success_hours": 24,
                "failure_hours": 72,
                "deleted": 5,
                "action_audit_deleted": 2,
                "action_audit_retention_failed": False,
            },
        )
        self.cleanup_jobs.assert_awaited_once_with(tenant_id=7)
        self.staged.assert_not_awaited()

    async def test_audit_failure_is_redacted_and_does_not_stop_content_retention(self):
        self.audit.side_effect = RuntimeError("synthetic-private-provider-token")
        result = await self.prune({"job_id": 23})
        self.assertTrue(result["action_audit_retention_failed"])
        self.assertEqual(result["action_audit_deleted"], 0)
        self.assertEqual(result["staged_deleted"], 1)
        self.assertEqual(result["analytics_deleted"], 3)
        self.logger.warning.assert_called_once_with(
            "action_audit_retention_failed tenant_id=%s job_id=%s error_code=%s", 7, 23, "runtime_error"
        )


if __name__ == "__main__":
    unittest.main()
