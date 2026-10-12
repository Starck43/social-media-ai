"""Standalone tests for the scheduled digest run-row claim (`DigestRunManager.start_run`).

Owner: python tests/test_digest_run_claim.py
No application/DB imports: the manager source is loaded with a recording
`BaseManager` double, so these cases pin the conditional-claim decisions; the
real PostgreSQL race is covered by tests/test_digest_builder.py.
"""

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from source_import_isolation import load_isolated_source
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
START, END = date(2026, 10, 11), date(2026, 10, 11)
STALE = timedelta(minutes=31)


class Filtered:
    def __init__(self, manager, kwargs):
        self.manager, self.kwargs = manager, kwargs

    async def update(self, **values):
        self.manager.updates.append((self.kwargs, values))
        return self.manager.update_count


class BaseManagerDouble:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, model):
        self.rows = {}
        self.updates, self.created = [], []
        self.update_count = 1
        self.create_error = None

    async def get(self, **kwargs):
        if "id" in kwargs:
            return self.rows.get(kwargs["id"])
        return next(iter(self.rows.values()), None)

    def filter(self, **kwargs):
        return Filtered(self, kwargs)

    async def create(self, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        self.created.append(kwargs)
        return SimpleNamespace(id=99, **kwargs)


MODULE = load_isolated_source(
    "app.models.managers._digest_run_claim_isolated",
    ROOT / "app/models/managers/digest_run_manager.py",
    {
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManagerDouble),
        "app.models.digest_run": SimpleNamespace(DigestRun=object),
    },
)


def start(manager, agent_task_id=7, force=False):
    return asyncio.run(
        manager.start_run(agent_task_id=agent_task_id, period="day", period_start=START, period_end=END, force=force)
    )


class StartRunClaimTests(unittest.TestCase):
    def setUp(self):
        self.manager = MODULE.DigestRunManager()

    def existing(self, status, age=timedelta(0)):
        row = SimpleNamespace(id=5, status=status, updated_at=datetime.now(timezone.utc) - age)
        self.manager.rows[5] = row
        return row

    def test_failed_row_is_claimed_conditionally_on_observed_status_and_version(self):
        row = self.existing("failed")
        self.assertIs(start(self.manager), row)
        self.assertEqual(
            self.manager.updates,
            [
                (
                    {"id": 5, "status": "failed", "updated_at": row.updated_at},
                    {"status": "pending", "channel": "auto", "message_id": None, "error": None},
                )
            ],
        )

    def test_pending_row_is_in_progress_without_any_write(self):
        self.existing("pending")
        with self.assertRaises(MODULE.DigestRunInProgress) as raised:
            start(self.manager)
        self.assertEqual(raised.exception.run_id, 5)
        self.assertEqual(str(raised.exception), MODULE.DIGEST_RUN_IN_PROGRESS)
        self.assertEqual((self.manager.updates, self.manager.created), ([], []))

    def test_lost_conditional_claim_is_in_progress(self):
        self.existing("failed")
        self.manager.update_count = 0  # another attempt reset the row first
        with self.assertRaises(MODULE.DigestRunInProgress):
            start(self.manager)

    def test_force_takes_over_only_a_stale_pending_row(self):
        row = self.existing("pending", age=STALE)
        self.assertIs(start(self.manager, force=True), row)
        self.assertEqual(self.manager.updates[0][0], {"id": 5, "status": "pending", "updated_at": row.updated_at})

    def test_force_on_a_fresh_pending_row_is_in_progress_without_any_write(self):
        self.existing("pending", age=timedelta(minutes=5))
        with self.assertRaises(MODULE.DigestRunInProgress) as raised:
            start(self.manager, force=True)
        self.assertEqual(raised.exception.run_id, 5)
        self.assertEqual((self.manager.updates, self.manager.created), ([], []))

    def test_forced_claim_lost_to_a_simultaneous_resend_is_in_progress(self):
        self.existing("pending", age=STALE)
        self.manager.update_count = 0  # the other resend moved the row's version first
        with self.assertRaises(MODULE.DigestRunInProgress):
            start(self.manager, force=True)

    def test_pending_without_timestamp_is_never_taken_over(self):
        row = self.existing("pending")
        row.updated_at = None
        with self.assertRaises(MODULE.DigestRunInProgress):
            start(self.manager, force=True)
        self.assertEqual(self.manager.updates, [])

    def test_staleness_window_boundary_and_naive_utc(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        window = MODULE.FORCED_TAKEOVER_AFTER
        self.assertTrue(MODULE.pending_is_stale(now - window, now))
        self.assertFalse(MODULE.pending_is_stale(now - window + timedelta(seconds=1), now))
        self.assertTrue(MODULE.pending_is_stale((now - window).replace(tzinfo=None), now))
        self.assertFalse(MODULE.pending_is_stale(None, now))

    def test_concurrent_insert_loses_on_unique_constraint(self):
        self.manager.create_error = IntegrityError("insert", {}, Exception("uq_digest_agent_task_period"))
        with self.assertRaises(MODULE.DigestRunInProgress):
            start(self.manager)

    def test_manual_run_always_creates_and_keeps_integrity_errors(self):
        self.existing("pending")
        created = start(self.manager, agent_task_id=None)
        self.assertEqual(created.status, "pending")
        self.assertEqual(self.manager.updates, [])
        self.manager.create_error = IntegrityError("insert", {}, Exception("other"))
        with self.assertRaises(IntegrityError):
            start(self.manager, agent_task_id=None)


class ReviewCodeVisibilityTests(unittest.TestCase):
    def test_digest_review_stops_keep_their_code_on_the_job(self):
        outcomes = load_isolated_source("app.jobs._result_outcomes_digest_codes", ROOT / "app/jobs/result_outcomes.py")
        for code in (MODULE.DIGEST_RUN_IN_PROGRESS, MODULE.PARTIAL_DELIVERY_REVIEW):
            failure = outcomes.returned_failure({"status": "failed", "error": code, "detail": "PRIVATE"})
            self.assertEqual(failure.audit_result(), {"status": "failed", "error": code, "llm_cost": None})
        # A detailed partial-delivery message is not a code and stays redacted.
        detailed = outcomes.returned_failure({"status": "failed", "error": MODULE.PARTIAL_DELIVERY_REVIEW + "\nmax:1"})
        self.assertEqual(detailed.code, "handler_returned_failure")


if __name__ == "__main__":
    unittest.main()
