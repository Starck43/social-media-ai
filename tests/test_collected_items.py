"""Staging of raw collected content and the "seen" ledger behind «Новых».

Covers the three promises this feature makes:

1. a collect run that will not analyse inline parks the raw batch in
   `collected_items`, and one that analyses inline parks nothing;
2. an analysis consumes staged rows and retires them, but a *failed* analysis
   leaves every row in place (the only copy of the content must not vanish);
3. re-collecting an unchanged wall reports 0 new the second time — the bug this
   whole table exists to fix, where an empty `ai_analytics` ledger made every
   run look like 32 brand-new posts.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models import AgentTask, CollectedItem, Platform, Source
from app.types import SourceType


def _items(n=3):
    """A stable batch: same ids, same text, no changing metrics."""
    return [
        {"platform": "vk", "external_id": f"1_{i}", "text": f"пост номер {i}", "published_at": "2026-10-02T10:00:00Z"}
        for i in range(n)
    ]


@pytest.fixture
async def source():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    s = await Source.objects.create(
        platform_id=p.id,
        name="stage-src",
        source_type=SourceType.CHANNEL.name,
        external_id="stage",
        is_active=True,
    )
    yield s
    await CollectedItem.objects.filter(source_id=s.id).delete()
    await Source.objects.delete_by_id(s.id)
    await Platform.objects.delete_by_id(p.id)


def _collector(items, analyzer=None):
    """A collector whose platform hands back `items` without touching the net."""
    from app.services.monitoring import collector as mod

    class _Client:
        async def collect_data(self, source, content_type="posts"):
            return [dict(i) for i in items]

    mod.get_social_client = lambda platform: _Client()
    c = mod.ContentCollector()
    if analyzer is not None:
        c.analyzer = analyzer
    return c


class _Analyzer:
    """Records what it was asked to analyse; `result` decides if it saved."""

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def analyze_content(self, content, source, **kwargs):
        self.calls.append(list(content))
        return self.result


async def test_collect_without_inline_analysis_stages_raw_items(source):
    """A run that will not analyse inline leaves the content readable in the DB."""
    c = _collector(_items(3))

    result = await c.collect_from_source(source, analyze=False, run_id=4242)

    assert result["staged"] == 3
    rows = await CollectedItem.objects.filter(source_id=source.id)
    assert len(rows) == 3
    assert {r.text for r in rows} == {"пост номер 0", "пост номер 1", "пост номер 2"}
    # The run id is what lets a later analysis retire exactly this batch.
    assert all(r.run_id == 4242 for r in rows)


async def test_inline_analysis_stages_nothing(source):
    """Analysing in the same pass needs no staging — and does none."""
    analyzer = _Analyzer(result=[object()])
    c = _collector(_items(3), analyzer=analyzer)

    result = await c.collect_from_source(source, analyze=True, run_id=4242)

    assert result["staged"] == 0
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0
    # The content still reached the analyser.
    assert len(analyzer.calls[0]) == 3


async def test_run_carries_hashes_even_when_analysis_fails(source):
    """The ledger must not depend on the analysis having saved anything.

    This is the regression behind «Новых: 32» on every run: the only record of
    having seen an item lived in `ai_analytics`, which a failing analysis leaves
    empty, so the next collection reported the whole wall as new again.
    """
    analyzer = _Analyzer(result=None)  # analysis produces nothing
    c = _collector(_items(3), analyzer=analyzer)

    result = await c.collect_from_source(source, analyze=True, run_id=4242)

    assert result["analytics_count"] == 0
    assert len(result["content_hashes"]) == 3


async def test_second_identical_collection_reports_zero_new(source):
    """The user-visible bug: 32 then 32, because nothing recorded the first run."""
    from app.services.ai.dedup import count_new_items

    items = _items(32)
    c = _collector(items, analyzer=_Analyzer(result=None))

    first = await c.collect_from_source(source, analyze=True, run_id=1)
    assert first["new_items"] == 32

    # The job result is where the dispatcher persists `content_hashes`; mirror
    # that here, then re-collect the identical wall.
    from app.models import Job

    await Job.objects.create(
        job_type="collect",
        status="success",
        run_at=datetime.now(timezone.utc),
        result={"per_source": [{"source_id": source.id, "content_hashes": first["content_hashes"]}]},
    )

    second = await c.collect_from_source(source, analyze=True, run_id=2)
    assert second["content_count"] == 32
    assert second["new_items"] == 0


async def test_staged_items_count_as_seen_even_before_analysis(source):
    """Seen-but-unanalysed is a real state: not new, but still to be analysed."""
    from app.services.ai.dedup import count_new_items

    items = _items(4)
    c = _collector(items, analyzer=_Analyzer(result=None))
    await c.collect_from_source(source, analyze=False, run_id=7)

    # Same content, different delivery: nothing new, yet nothing analysed either.
    assert await count_new_items(items, source.id) == 0

async def _task_with_scenario(job_type="analyze"):
    """A task owning an active scenario, so handle_analyze gets past its gates."""
    from app.models import AgentScenario, AgentTask

    scenario = await AgentScenario.objects.create(
        name=f"sc_{uuid.uuid4().hex[:6]}",
        analysis_types=["summary"],
        content_types=["posts"],
        is_active=True,
    )
    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type=job_type,
        cron_expr="@once",
        timezone="Europe/Moscow",
        payload={},
        agent_scenario_id=scenario.id,
        is_active=True,
    )
    return task, scenario


async def test_analyze_retires_staged_rows_after_a_saved_analysis(source, monkeypatch):
    """Content the analysis has actually consumed is no longer kept around."""
    from app.jobs import handlers
    from app.services.ai import analyzer as analyzer_mod

    c = _collector(_items(2))
    await c.collect_from_source(source, analyze=False, run_id=555)
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 2

    seen = {}

    async def fake_analyze(self, content, source_, **kwargs):
        seen["count"] = len(content)
        return [object()]

    monkeypatch.setattr(analyzer_mod.AIAnalyzer, "analyze_content", fake_analyze)
    task, scenario = await _task_with_scenario()
    try:
        await handlers.handle_analyze({"agent_task_id": task.id})
    finally:
        from app.models import AgentTask as _T

        await _T.objects.delete_by_id(task.id)

    # The staged batch reached the analyser...
    assert seen["count"] == 2
    # ...and its rows are gone.
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0


async def test_failed_analysis_keeps_staged_rows(source, monkeypatch):
    """A failed analysis must not cost us the only copy of the content."""
    from app.jobs import handlers
    from app.services.ai import analyzer as analyzer_mod

    c = _collector(_items(3))
    await c.collect_from_source(source, analyze=False, run_id=666)

    async def failing(self, content, source_, **kwargs):
        return None

    monkeypatch.setattr(analyzer_mod.AIAnalyzer, "analyze_content", failing)
    task, scenario = await _task_with_scenario()
    try:
        await handlers.handle_analyze({"agent_task_id": task.id})
    finally:
        from app.models import AgentTask as _T

        await _T.objects.delete_by_id(task.id)

    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 3
