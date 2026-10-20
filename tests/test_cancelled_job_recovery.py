"""Cancellation recovery classification and prune predicates, without app/DB startup.

Owner: .venv/bin/python tests/test_cancelled_job_recovery.py
Actual source is loaded with explicit import doubles. SQL three-valued logic is
modelled here; the read-only PostgreSQL runner verifies the same prune predicate.
"""

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_job_claim_outcomes as claim_fixtures
import test_jobs_queue_signals as signal_fixtures
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
BUDGET = load_isolated_source("_cancel_recovery_budget", ROOT / "app/jobs/attempt_budget.py")
POLICY = load_isolated_source(
    "_cancel_recovery_policy", ROOT / "app/jobs/recovery_policy.py", {"app.jobs.attempt_budget": BUDGET}
)
RETENTION = load_isolated_source("_cancel_recovery_retention", ROOT / "app/utils/retention.py")


class PolicyTests(unittest.TestCase):
    def test_exact_cancellation_value_is_unconfirmed(self):
        self.assertTrue(POLICY.outcome_unconfirmed("Cancelled by operator"))
        self.assertEqual(POLICY.OPERATOR_CANCELLED_ERROR, "Cancelled by operator")

    def test_other_uncertainty_markers_remain_unconfirmed(self):
        for error in (POLICY.OUTCOME_UNCONFIRMED_PREFIX + "old", BUDGET.ATTEMPT_BUDGET_STOP_PREFIX + "old"):
            with self.subTest(error=error):
                self.assertTrue(POLICY.outcome_unconfirmed(error))

    def test_noncanonical_errors_and_malformed_values_do_not_gain_classification(self):
        class Text(str):
            pass

        errors = (
            None,
            False,
            1,
            {},
            "",
            "known_failure",
            "Cancelled by operator suffix",
            Text("Cancelled by operator"),
        )
        for error in errors:
            with self.subTest(error=error):
                self.assertFalse(POLICY.outcome_unconfirmed(error))


class CancellationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    setUp = claim_fixtures.ManagerBoundaryTests.setUp

    async def project(self, *args, **kwargs):
        self.fail("Cancellation must not invoke detached task projection")

    async def test_actual_cancel_is_unconfirmed_and_late_outcomes_cannot_overwrite_evidence(self):
        self.stored.result = {"external_effect": "unresolved"}
        self.stored.llm_cost = 0.25
        before = deepcopy(vars(self.stored))
        self.assertTrue(await self.manager.cancel_running(self.claim))
        self.assertTrue(POLICY.outcome_unconfirmed(self.stored.error))
        self.assertEqual(self.stored.status, "failed")
        for field, value in before.items():
            if field not in {"status", "error", "finished_at"}:
                self.assertEqual(getattr(self.stored, field), value, field)
        cancelled = deepcopy(vars(self.stored))
        receipt = await self.manager.mark_done(7, result={"late": "success"}, llm_cost=99, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, claim_fixtures.CLAIMS.OutcomeAck.CLAIM_LOST)
        self.assertEqual(vars(self.stored), cancelled)
        receipt = await self.manager.mark_failed(7, "late failure", allow_retry=True, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, claim_fixtures.CLAIMS.OutcomeAck.CLAIM_LOST)
        self.assertEqual(vars(self.stored), cancelled)
        self.manager._record_task_result.assert_not_awaited()


class QueueSurfaceTests(unittest.IsolatedAsyncioTestCase):
    setUp = signal_fixtures.QueueSignalTests.setUp

    def test_cancellation_counts_as_unconfirmed_without_exposing_evidence(self):
        rows = [
            signal_fixtures.job("failed", error=POLICY.OPERATOR_CANCELLED_ERROR),
            signal_fixtures.job("done", error=POLICY.OPERATOR_CANCELLED_ERROR),
            signal_fixtures.job("failed", error="known_failure"),
        ]
        signals = self.source.queue_signals(rows, signal_fixtures.NOW)
        self.assertEqual(signals["unconfirmed_failed"], 1)
        self.assertNotIn("Cancelled", repr(signals))

    async def test_actual_job_list_flags_cancelled_row_for_review(self):
        row = SimpleNamespace(
            id=7,
            tenant_id=31,
            status="failed",
            error=POLICY.OPERATOR_CANCELLED_ERROR,
            job_type="learn",
            result={},
            payload={},
            attempts=1,
            created_at=datetime.now(timezone.utc),
        )
        self.manager.page_for_web.return_value = ([row], 1)
        self.manager.overview_for_web.return_value = (
            {"pending": 0, "running": 0, "done": 0, "failed": 1, "attention": 1},
            {"unconfirmed_failed": 1, "overdue_pending": 0, "stale_running": 0, "oldest_overdue_minutes": 0},
        )
        request = SimpleNamespace(
            state=SimpleNamespace(web_user=SimpleNamespace(is_active=True), tenant_id=31),
            query_params={},
        )
        await self.source.jobs_list(request)
        rendered = self.deps.render.call_args.kwargs
        self.assertEqual(rendered["unconfirmed_jobs"], {7})
        self.assertEqual(rendered["signals"]["unconfirmed_failed"], 1)
        # The page is workspace-scoped through the manager, not through a bare
        # queryset filter.
        self.assertEqual(self.manager.page_for_web.await_args.args[0], 31)

    def test_existing_template_hides_retry_for_unconfirmed_records(self):
        template = (ROOT / "app/web/templates/web/jobs.html").read_text()
        self.assertIn("job.id in unconfirmed_jobs", template)
        self.assertIn("результат не подтверждён", template)
        # Retry is offered only for the rows the server marked replayable; an
        # unconfirmed result is not replayable (`can_replay` blocks it), so the
        # row keeps its evidence and loses the button.
        self.assertIn("job.id in retryable", template)


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return lambda row: None if getattr(row, self.name) is None else getattr(row, self.name) == value

    def __lt__(self, value):
        return lambda row: getattr(row, self.name) < value

    def is_(self, value):
        return lambda row: getattr(row, self.name) is value

    def startswith(self, value):
        return lambda row: None if getattr(row, self.name) is None else getattr(row, self.name).startswith(value)


def sql_not(expression):
    def evaluate(row):
        value = expression(row)
        return None if value is None else not value

    return evaluate


def sql_or(*expressions):
    def evaluate(row):
        values = [expression(row) for expression in expressions]
        return True if True in values else None if None in values else False

    return evaluate


class Query:
    def __init__(self, rows, tenant_id):
        self.rows = rows
        self.tenant_id = tenant_id
        self.predicates = []
        self.deleted = []

    def filter(self, *expressions, **filters):
        self.predicates.extend(expressions)
        for name, value in filters.items():
            if name != "status__in":
                raise AssertionError("Unexpected prune filter")
            self.predicates.append(lambda row, statuses=value: row.status in statuses)
        return self

    async def delete(self):
        self.deleted = [
            row.id
            for row in self.rows
            if row.tenant_id == self.tenant_id and all(predicate(row) is True for predicate in self.predicates)
        ]
        return len(self.deleted)


class Session:
    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def close(self):
        pass


def load_prune(query, fields, sqlalchemy):
    """Use actual handler source, substituting only its declared I/O dependencies."""
    return load_isolated_source(
        "_cancel_recovery_handlers",
        ROOT / "app/jobs/handlers.py",
        {
            "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=RuntimeError),
            "sqlalchemy": sqlalchemy,
            "app.core.config": SimpleNamespace(settings=SimpleNamespace(DATA_RETENTION_MONTHS=1)),
            "app.core.database": SimpleNamespace(new_session=Session),
            "app.jobs.attempt_budget": BUDGET,
            "app.jobs.recovery_policy": POLICY,
            "app.models": SimpleNamespace(
                Job=SimpleNamespace(objects=query, **fields),
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(delete_older_than=AsyncMock(return_value=0))),
                AIAnalytics=SimpleNamespace(objects=SimpleNamespace(delete_expired=AsyncMock(return_value=0))),
            ),
            "app.utils.retention": RETENTION,
        },
    ).handle_prune


class PruneTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_prune_leaves_job_history_to_the_lifecycle_policy(self):
        """The legacy prune manages raw and analytics data, never Job rows.

        Job history is a built-in retention policy
        (`cleanup_terminal_job_history`, exercised against a database in
        `test_jobs_lifecycle_replay_db.py`), so a legacy task cannot drop a run
        — and in particular cannot drop the evidence of an unconfirmed result.
        """
        old = datetime.now(timezone.utc) - timedelta(days=90)
        errors = [
            POLICY.OPERATOR_CANCELLED_ERROR,
            POLICY.OPERATOR_CANCELLED_ERROR,
            POLICY.OUTCOME_UNCONFIRMED_PREFIX + "old",
            BUDGET.ATTEMPT_BUDGET_STOP_PREFIX + "old",
            None,
            "",
            "known_failure",
        ]
        rows = [
            SimpleNamespace(
                id=index,
                tenant_id=31,
                status="failed",
                error=error,
                created_at=old,
                attempts=2,
                result={"keep": True},
                llm_cost=0.25,
            )
            for index, error in enumerate(errors)
        ]
        # Old done rows remain ordinarily eligible; running/recent/foreign rows do not.
        rows.extend(
            [
                SimpleNamespace(id=7, tenant_id=31, status="done", error=None, created_at=old),
                SimpleNamespace(id=8, tenant_id=31, status="running", error=None, created_at=old),
                SimpleNamespace(id=9, tenant_id=31, status="failed", error=None, created_at=datetime.now(timezone.utc)),
                SimpleNamespace(id=10, tenant_id=32, status="failed", error=None, created_at=old),
            ]
        )
        before = deepcopy([vars(row) for row in rows])
        query = Query(rows, tenant_id=31)
        prune = load_prune(
            query,
            {"created_at": Field("created_at"), "error": Field("error")},
            SimpleNamespace(not_=sql_not, or_=sql_or),
        )
        result = await prune({"days": 7})
        self.assertEqual(query.deleted, [])
        self.assertEqual(result["deleted"], 0)
        self.assertEqual([vars(row) for row in rows], before)


if __name__ == "__main__":
    unittest.main()
