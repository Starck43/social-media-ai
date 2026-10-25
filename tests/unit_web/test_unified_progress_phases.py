import asyncio
from types import SimpleNamespace

from app.jobs.progress import ProgressSession, read_snapshot


def test_progress_phase_resets_source_position_and_keeps_phase_label():
    async def run():
        snapshots = []

        async def writer(_claim, snapshot):
            snapshots.append(snapshot)
            return True

        session = ProgressSession(SimpleNamespace(job_type="analyze"), writer=writer)
        sources = [SimpleNamespace(id=1, name="Источник 1"), SimpleNamespace(id=2, name="Источник 2")]
        await session.begin_phase("collection")
        await session.step(sources, [])
        await session.step(sources, [{"source_id": 1, "outcome": "collected"}])
        await session.begin_phase("analysis")
        await session.step(sources, [])

        assert [(item["phase"], item["completed"]) for item in snapshots] == [
            ("collection", 0),
            ("collection", 1),
            ("analysis", 0),
        ]
        assert read_snapshot({"progress": {**snapshots[-1], "attempt": 1}}, 1)["phase"] == "analysis"

    asyncio.run(run())
