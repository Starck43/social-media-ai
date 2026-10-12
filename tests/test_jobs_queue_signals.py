"""Standalone regression tests for the `/app/jobs` queue-health signals.

Owner: python tests/test_jobs_queue_signals.py
No application/DB/provider imports or route-server startup: `app/web/jobs.py` is
loaded with module-local import doubles. The real recovery-policy vocabulary is
used, so "unconfirmed" means exactly what the job list already shows.
"""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
BUDGET = load_isolated_source("app.jobs._signals_budget", ROOT / "app/jobs/attempt_budget.py")
POLICY = load_isolated_source(
    "app.jobs._signals_policy", ROOT / "app/jobs/recovery_policy.py", {"app.jobs.attempt_budget": BUDGET}
)
ATTEMPT_BUDGET_STOP_PREFIX = BUDGET.ATTEMPT_BUDGET_STOP_PREFIX
OUTCOME_UNCONFIRMED_PREFIX = POLICY.OUTCOME_UNCONFIRMED_PREFIX
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
SECRET = "token=sk-live-secret payload chat_id=123"


class Router:
    def __init__(self, **kwargs):
        self.prefix = kwargs["prefix"]

    def get(self, path, **kwargs):
        return lambda function: function

    post = get


class Query:
    """Awaitable double for `Job.objects`; records tenant filters."""

    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def __await__(self):
        async def result():
            return list(self.rows)

        return result().__await__()


def job(status, *, run_at=None, locked_at=None, error=None):
    return SimpleNamespace(status=status, run_at=run_at, locked_at=locked_at, error=error)


def minutes_ago(minutes):
    return NOW - timedelta(minutes=minutes)


class QueueSignalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.query = Query([])
        deps = SimpleNamespace(
            action_tenant_id=lambda *args: None,
            add_flash=lambda *args: None,
            ensure_csrf=lambda *args: True,
            guard_superuser=lambda *args, **kwargs: None,
            guard_web=lambda *args, **kwargs: None,
            plural=lambda *args: "",
            render=Mock(return_value="page"),
            tenant_filter_context=AsyncMock(return_value=(None, [])),
        )
        self.deps = deps

        @contextmanager
        def tenant_scope(**kwargs):
            yield

        self.source = load_isolated_source(
            "app.web._jobs_queue_signals_isolated",
            ROOT / "app/web/jobs.py",
            {
                "fastapi": SimpleNamespace(APIRouter=Router, Form=lambda default=None, **kw: default, Request=object),
                "fastapi.responses": SimpleNamespace(RedirectResponse=object),
                "app.models.job": SimpleNamespace(Job=SimpleNamespace(objects=self.query)),
                "app.web.deps": deps,
                "app.jobs.recovery_policy": POLICY,
                "app.core.tenant_context": SimpleNamespace(tenant_scope=tenant_scope),
                "app.web.tasks": SimpleNamespace(_run_outcome=lambda job_type, result: "ok"),
                "app.models": SimpleNamespace(
                    Source=SimpleNamespace(
                        id=SimpleNamespace(in_=lambda ids: ids),
                        objects=SimpleNamespace(filter=AsyncMock(return_value=[])),
                    )
                ),
            },
        )

    def signals(self, rows):
        return self.source.queue_signals(rows, NOW)

    def test_empty_queue_has_no_signal(self):
        self.assertEqual(
            self.signals([]),
            {"overdue_pending": 0, "oldest_overdue_minutes": None, "stale_running": 0, "unconfirmed_failed": 0},
        )

    def test_overdue_pending_counts_only_due_rows_past_threshold(self):
        threshold = self.source.OVERDUE_PENDING_MINUTES
        rows = [
            job("pending", run_at=minutes_ago(threshold)),  # exactly at the threshold
            job("pending", run_at=minutes_ago(threshold + 47)),
            job("pending", run_at=minutes_ago(threshold - 1)),  # due, but recent
            job("pending", run_at=NOW + timedelta(minutes=30)),  # retry backoff / future schedule
            job("pending", run_at=None),
        ]
        result = self.signals(rows)
        self.assertEqual(result["overdue_pending"], 2)
        self.assertEqual(result["oldest_overdue_minutes"], threshold + 47)

    def test_stale_running_uses_reaper_cutoff(self):
        cutoff = self.source.STALE_RUNNING_MINUTES
        self.assertEqual(cutoff, 30)  # JobManager.reap_stale default
        rows = [
            job("running", locked_at=minutes_ago(cutoff + 1)),
            job("running", locked_at=minutes_ago(cutoff)),  # not older than the cutoff
            job("running", locked_at=minutes_ago(1)),  # heartbeat is renewing it
            job("running", locked_at=None),
        ]
        self.assertEqual(self.signals(rows)["stale_running"], 1)

    def test_naive_timestamps_are_treated_as_utc(self):
        naive = (NOW - timedelta(minutes=120)).replace(tzinfo=None)
        result = self.signals([job("pending", run_at=naive), job("running", locked_at=naive)])
        self.assertEqual((result["overdue_pending"], result["oldest_overdue_minutes"]), (1, 120))
        self.assertEqual(result["stale_running"], 1)

    def test_unconfirmed_failures_use_recovery_vocabulary(self):
        rows = [
            job("failed", error=OUTCOME_UNCONFIRMED_PREFIX + "lease lost"),
            job("failed", error=ATTEMPT_BUDGET_STOP_PREFIX + "budget"),
            job("failed", error="known_delivery_failure"),
            job("failed", error=None),
            job("done", error=OUTCOME_UNCONFIRMED_PREFIX),
        ]
        self.assertEqual(self.signals(rows)["unconfirmed_failed"], 2)

    def test_signals_are_counts_only_and_never_echo_row_content(self):
        rows = [
            job("pending", run_at=minutes_ago(90), error=SECRET),
            job("running", locked_at=minutes_ago(90), error=SECRET),
            job("failed", error=OUTCOME_UNCONFIRMED_PREFIX + SECRET),
        ]
        result = self.signals(rows)
        for value in result.values():
            self.assertTrue(value is None or type(value) is int)
        self.assertNotIn("secret", repr(result))

    async def test_overview_scopes_totals_and_signals_to_the_same_rows(self):
        self.query.rows = [
            job("pending", run_at=datetime.now(timezone.utc) - timedelta(hours=2)),
            job("running", locked_at=datetime.now(timezone.utc)),
            job("done"),
            job("failed", error=OUTCOME_UNCONFIRMED_PREFIX),
        ]
        stats, signals = await self.source._queue_overview(31)
        self.assertEqual(self.query.filters, [{"tenant_id": 31}])
        self.assertEqual(stats, {"pending": 1, "running": 1, "done": 1, "failed": 1})
        self.assertEqual(
            (signals["overdue_pending"], signals["stale_running"], signals["unconfirmed_failed"]), (1, 0, 1)
        )

    async def test_overview_without_tenant_does_not_add_a_filter(self):
        await self.source._queue_overview(None)
        self.assertEqual(self.query.filters, [])

    async def test_jobs_list_passes_signals_and_thresholds_to_the_page(self):
        self.query.rows = [job("running", locked_at=datetime.now(timezone.utc) - timedelta(hours=1))]
        self.query.order_by = lambda *args: self.query
        self.query.limit = lambda *args: self.query
        self.source.Job.created_at = SimpleNamespace(desc=lambda: None)
        for row in self.query.rows:
            row.id, row.job_type, row.result, row.payload = 1, "collect", {}, {}
        request = SimpleNamespace(state=SimpleNamespace(web_user=SimpleNamespace(is_superuser=False), tenant_id=31))
        self.assertEqual(await self.source.jobs_list(request), "page")
        kwargs = self.deps.render.call_args.kwargs
        self.assertEqual(kwargs["signals"]["stale_running"], 1)
        self.assertEqual(kwargs["stats"]["running"], 1)
        self.assertEqual(kwargs["overdue_pending_minutes"], self.source.OVERDUE_PENDING_MINUTES)
        self.assertEqual(kwargs["stale_running_minutes"], self.source.STALE_RUNNING_MINUTES)
        self.assertIn({"tenant_id": 31}, self.query.filters)

    def test_page_renders_signals_without_row_content(self):
        page = (ROOT / "app/web/templates/web/jobs.html").read_text()
        for name in ("overdue_pending", "oldest_overdue_minutes", "stale_running", "unconfirmed_failed"):
            self.assertIn(f"signals.{name}", page)
        start = page.index("{% if signals.overdue_pending or signals.stale_running")
        banner = page[start : page.index("{% endif %}\n\n", start)]
        self.assertNotIn("job.", banner)


if __name__ == "__main__":
    unittest.main()
