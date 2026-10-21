"""Per-source run progress: snapshot, claim-bound writer and web view (no DB)."""

import asyncio
import unittest
from types import SimpleNamespace

from app.jobs import progress as job_progress


def _sources(*names):
    return [SimpleNamespace(id=index + 1, name=name) for index, name in enumerate(names)]


def _claim(job_type="collect", attempts=1):
    return SimpleNamespace(job_id=5, tenant_id=3, job_type=job_type, attempts=attempts)


class SnapshotTests(unittest.TestCase):
    def test_states_follow_position_and_breakdown(self):
        sources = _sources("A", "B", "C", "D")
        per_source = [
            {"source_id": 1, "outcome": "collected", "items": 4, "new_items": 2, "content_hashes": ["x" * 64]},
        ]
        snap = job_progress.build_snapshot(sources, 2, per_source)
        self.assertEqual([item["state"] for item in snap["items"]], ["done", "skipped", "running", "waiting"])
        self.assertEqual(snap["completed"], 2)
        self.assertEqual(snap["current"], "C")
        self.assertEqual(snap["items"][0]["new_items"], 2)
        self.assertNotIn("content_hashes", snap["items"][0])

    def test_long_lists_are_bounded_and_keep_current_visible(self):
        sources = _sources(*[f"S{i}" for i in range(120)])
        snap = job_progress.build_snapshot(sources, 100, [])
        self.assertEqual(len(snap["items"]), job_progress.MAX_ITEMS)
        visible = [item["name"] for item in snap["items"]]
        self.assertIn("S100", visible)
        self.assertEqual(snap["first"] + visible.index("S100"), 100)

    def test_names_are_truncated(self):
        snap = job_progress.build_snapshot([SimpleNamespace(id=1, name="x" * 500)], 0, [])
        self.assertEqual(len(snap["items"][0]["name"]), job_progress.MAX_NAME)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_step_is_noop_without_bound_session(self):
        await job_progress.step(_sources("A"), [])

    async def test_untracked_job_type_never_writes(self):
        writes = []

        async def writer(claim, snap):
            writes.append(snap)
            return True

        session = job_progress.ProgressSession(_claim("digest"), writer=writer)
        token = job_progress.bind(session)
        try:
            await job_progress.step(_sources("A"), [])
        finally:
            job_progress.unbind(token)
        self.assertEqual(writes, [])

    async def test_each_step_advances_position(self):
        positions = []

        async def writer(claim, snap):
            positions.append(snap["completed"])
            return True

        session = job_progress.ProgressSession(_claim(), writer=writer)
        sources = _sources("A", "B")
        await session.step(sources, [])
        await session.step(sources, [{"source_id": 1, "outcome": "empty"}])
        self.assertEqual(positions, [0, 1])

    async def test_lost_claim_stops_handler(self):
        async def writer(claim, snap):
            return False

        session = job_progress.ProgressSession(_claim(), writer=writer)
        with self.assertRaises(job_progress.ProgressStopped):
            await session.step(_sources("A"), [])
        self.assertTrue(session.lost)
        with self.assertRaises(job_progress.ProgressStopped):
            await session.step(_sources("A"), [])

    async def test_current_counter_refresh_does_not_advance_source_position(self):
        writes = []

        async def writer(claim, snap):
            writes.append(snap)
            return True

        session = job_progress.ProgressSession(_claim("analyze"), writer=writer)
        sources = _sources("A", "B")
        await session.step(sources, [])
        await session.update_current(
            sources, [], {"source_id": 1, "raw_total": 12, "raw_selected": 5, "raw_processed": 3}
        )
        self.assertEqual([snap["completed"] for snap in writes], [0, 0])
        self.assertEqual(writes[-1]["items"][0]["raw_processed"], 3)

    async def test_claim_loss_on_current_counter_refresh_stops_generation(self):
        async def writer(claim, snap):
            return False

        session = job_progress.ProgressSession(_claim("analyze"), writer=writer)
        with self.assertRaises(job_progress.ProgressStopped):
            await session.update_current(
                _sources("A"), [], {"source_id": 1, "raw_total": 1, "raw_selected": 1, "raw_processed": 0}
            )
        self.assertTrue(session.lost)

    async def test_write_error_never_fails_the_run(self):
        async def writer(claim, snap):
            raise ConnectionError("PRIVATE-SQL")

        session = job_progress.ProgressSession(_claim(), writer=writer)
        await session.step(_sources("A"), [])
        self.assertFalse(session.lost)

    async def test_bound_session_reaches_child_task(self):
        seen = []

        async def writer(claim, snap):
            seen.append(snap["current"])
            return True

        token = job_progress.bind(job_progress.ProgressSession(_claim(), writer=writer))
        try:
            task = asyncio.create_task(job_progress.step(_sources("A"), []))
        finally:
            job_progress.unbind(token)
        await task
        self.assertEqual(seen, ["A"])

    async def test_settle_only_for_collect_and_never_raises(self):
        calls = []

        async def settler(claim):
            calls.append(claim.job_type)
            raise RuntimeError("db down")

        await job_progress.ProgressSession(_claim("analyze"), settler=settler).settle()
        await job_progress.ProgressSession(_claim("collect"), settler=settler).settle()
        self.assertEqual(calls, ["collect"])


class ReadSnapshotTests(unittest.TestCase):
    def _result(self, attempt=2):
        snap = job_progress.build_snapshot(_sources("A", "B"), 1, [{"source_id": 1, "outcome": "collected"}])
        return {"progress": {**snap, "attempt": attempt}}

    def test_current_attempt_is_read(self):
        view = job_progress.read_snapshot(self._result(2), 2)
        self.assertEqual(view["completed"], 1)
        self.assertEqual([item["state"] for item in view["items"]], ["done", "running"])

    def test_previous_attempt_or_malformed_progress_is_ignored(self):
        self.assertIsNone(job_progress.read_snapshot(self._result(1), 2))
        malformed = {"progress": {"attempt": 2, "total": 1, "completed": 5, "items": []}}
        self.assertIsNone(job_progress.read_snapshot(malformed, 2))
        self.assertIsNone(job_progress.read_snapshot(None, 2))
        self.assertIsNone(job_progress.read_snapshot({"progress": "x"}, 2))


if __name__ == "__main__":
    unittest.main()
