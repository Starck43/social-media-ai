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
from datetime import datetime, timedelta, timezone

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


class _Row:
    """A stand-in for a saved AIAnalytics row.

    Carries `summary_data["content_hashes"]` because that is the whole basis of
    retirement: the collector drops exactly the raw items whose hash an
    analysis really stored.
    """

    def __init__(self, hashes):
        self.summary_data = {"content_hashes": list(hashes)}


class _Analyzer:
    """Records what it was asked to analyse; `result` decides if it saved.

    `result` is either None (nothing stored — the raw copy must survive) or a
    list of `_Row`s, which a caller can narrow to simulate a partial analysis.
    """

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def analyze_content(self, content, source, **kwargs):
        self.calls.append(list(content))
        return self.result


def _saved(items):
    """An analysis that stored every one of `items`."""
    from app.services.ai.dedup import item_hash

    return [_Row([item_hash(i) for i in items])]


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


async def test_inline_analysis_stages_first_then_retires_what_it_saved(source):
    """The raw copy is written *before* the analysis, dropped after it saved.

    Staging used to be the alternative to analysing, so the one run that
    actually called the LLM kept its batch in a local variable: a crashed
    worker or an outage lost the content outright. Now the rows exist for the
    whole call and go only once an analysis really stored their hashes.
    """
    items = _items(3)
    analyzer = _Analyzer(result=_saved(items))
    c = _collector(items, analyzer=analyzer)

    result = await c.collect_from_source(source, analyze=True, run_id=4242)

    # The batch was written (3 rows reported), then retired by the save.
    assert result["staged"] == 3
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0
    # The content still reached the analyser.
    assert len(analyzer.calls[0]) == 3


async def test_rows_exist_while_the_analysis_is_still_running(source):
    """The write happens *before* the LLM call, not after it."""
    items = _items(3)
    seen: dict = {}

    class _SlowAnalyzer(_Analyzer):
        async def analyze_content(self, content, source, **kwargs):
            # Mid-call the raw copy must already be on disk.
            seen["rows_during_call"] = len(await CollectedItem.objects.filter(source_id=source.id))
            return await super().analyze_content(content, source, **kwargs)

    c = _collector(items, analyzer=_SlowAnalyzer(result=None))

    await c.collect_from_source(source, analyze=True, run_id=4242)

    assert seen["rows_during_call"] == 3
    # The analysis stored nothing, so the batch stays for a later attempt.
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 3


async def test_inline_analysis_failure_keeps_the_raw_copy(source):
    """A failed inline analysis must not cost us the only copy of the content."""
    c = _collector(_items(3), analyzer=_Analyzer(result=None))

    result = await c.collect_from_source(source, analyze=True, run_id=4242)

    assert result["analytics_count"] == 0
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 3


async def test_partial_analysis_retires_only_what_it_covered(source):
    """Retiring by run would have eaten the days that never got analysed.

    A three-day batch analysed one day at a time: the rows of the two days that
    failed are still the only copy of that content, so they must survive.
    """
    items = _items(3)
    covered = items[:1]
    analyzer = _Analyzer(result=_saved(covered))
    c = _collector(items, analyzer=analyzer)

    await c.collect_from_source(source, analyze=True, run_id=4242)

    rows = await CollectedItem.objects.filter(source_id=source.id)
    assert len(rows) == 2
    assert {r.text for r in rows} == {"пост номер 1", "пост номер 2"}


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
        # A saved analysis reports the item hashes it covers — that is what the
        # retirement keys on.
        from app.services.ai.dedup import item_hash

        return [_Row([item_hash(i) for i in content])]

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


async def test_retiring_one_source_leaves_the_others_alone(source):
    """A job collects every source, so retirement must not sweep the workspace.

    One collect run stages rows for several sources at the same `run_id`.
    Retiring "the run" instead of "this source's analysed items" deleted the raw
    copy of sources whose analysis never happened — the exact loss this table
    exists to prevent.
    """
    other = await Source.objects.create(
        platform_id=source.platform_id,
        name="stage-src-2",
        source_type=SourceType.CHANNEL.name,
        external_id="stage-2",
        is_active=True,
    )
    try:
        c = _collector(_items(2))
        # Same run for both sources: this is what one job does.
        await c.collect_from_source(source, analyze=False, run_id=777)
        await c.collect_from_source(other, analyze=False, run_id=777)
        assert len(await CollectedItem.objects.filter(source_id=other.id)) == 2

        from app.jobs.handlers import _retire_staged

        items = _items(2)
        removed = await _retire_staged(_saved(items), source.id)

        assert removed == 2
        assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0
        # The neighbour's content is untouched.
        assert len(await CollectedItem.objects.filter(source_id=other.id)) == 2
    finally:
        await CollectedItem.objects.filter(source_id=other.id).delete()
        await Source.objects.delete_by_id(other.id)


async def test_prune_sweeps_raw_content_nobody_analysed(source):
    """The age sweep the table promises, wired to something for once.

    `delete_older_than` existed from the start but was called from nowhere, so
    "a failed analysis keeps the content" also meant "keeps it forever". The
    ceiling on that has to be a job that actually runs.
    """
    from app.jobs.handlers import handle_prune

    c = _collector(_items(2))
    await c.collect_from_source(source, analyze=False, run_id=888)
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 2

    # Nothing is old enough yet — a sweep must not eat fresh content.
    fresh = await handle_prune({"staged_days": 7})
    assert fresh["staged_deleted"] == 0
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 2

    # Age the rows past the budget and the same job reclaims them.
    await CollectedItem.objects.filter(source_id=source.id).update(
        created_at=datetime.now(timezone.utc) - timedelta(days=30)
    )
    swept = await handle_prune({"staged_days": 7})

    assert swept["staged_deleted"] == 2
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0


async def test_analyze_window_filters_staged_items_by_published_at(source, monkeypatch):
    """An analyze run only drains staged items inside the task's content window.

    Regression: the task's `cli_dates.start_date` gated collection (the VK
    request) but not analysis — `handle_analyze` drained every staged row, so a
    window of 2026-06-01 still analysed content from 2010.
    """
    from app.jobs import handlers
    from app.services.ai import analyzer as analyzer_mod

    # Two batches: one inside the window, one outside.
    inside = [
        {"platform": "vk", "external_id": f"in_{i}", "text": f"внутри {i}", "published_at": "2026-06-15T10:00:00Z"}
        for i in range(2)
    ]
    outside = [
        {"platform": "vk", "external_id": f"out_{i}", "text": f"снаружи {i}", "published_at": "2010-05-22T10:00:00Z"}
        for i in range(2)
    ]
    c = _collector(inside + outside)
    await c.collect_from_source(source, analyze=False, run_id=111)
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 4

    seen = {}

    async def fake_analyze(self, content, source_, **kwargs):
        seen["count"] = len(content)
        from app.services.ai.dedup import item_hash

        return [_Row([item_hash(i) for i in content])]

    monkeypatch.setattr(analyzer_mod.AIAnalyzer, "analyze_content", fake_analyze)
    task, scenario = await _task_with_scenario()
    # Window: only 2026-06-01 onward.
    await AgentTask.objects.update_by_id(task.id, payload={"cli_dates": {"start_date": "2026-06-01"}})
    task = await AgentTask.objects.get(id=task.id)
    try:
        await handlers.handle_analyze({"agent_task_id": task.id})
    finally:
        from app.models import AgentTask as _T

        await _T.objects.delete_by_id(task.id)

    # Only the two in-window rows reached the analyser...
    assert seen["count"] == 2
    # ...and only those were retired; the out-of-window rows stay staged.
    remaining = await CollectedItem.objects.filter(source_id=source.id)
    assert len(remaining) == 2
    assert {r.text for r in remaining} == {"снаружи 0", "снаружи 1"}


async def test_analyze_without_window_drains_all_staged(source, monkeypatch):
    """No window configured → the old behaviour: drain everything staged."""
    from app.jobs import handlers
    from app.services.ai import analyzer as analyzer_mod

    c = _collector(_items(3))
    await c.collect_from_source(source, analyze=False, run_id=222)

    seen = {}

    async def fake_analyze(self, content, source_, **kwargs):
        seen["count"] = len(content)
        from app.services.ai.dedup import item_hash

        return [_Row([item_hash(i) for i in content])]

    monkeypatch.setattr(analyzer_mod.AIAnalyzer, "analyze_content", fake_analyze)
    task, scenario = await _task_with_scenario()
    try:
        await handlers.handle_analyze({"agent_task_id": task.id})
    finally:
        from app.models import AgentTask as _T

        await _T.objects.delete_by_id(task.id)

    assert seen["count"] == 3
    assert len(await CollectedItem.objects.filter(source_id=source.id)) == 0


async def test_staging_json_metrics_author_and_original_survive_deferred_replay(source):
    from app.services.ai.analysis_render import render_analysis
    from app.services.ai.analyzer import AIAnalyzer

    items = _items(1)
    items[0].update(
        {
            "reactions": 0,
            "comments": 0,
            "views": 0,
            "from_id": 42,
            "permalink": "https://example.com/original",
            "metric_availability": {"reactions": True, "comments": False, "views": True},
        }
    )
    collector = _collector(items)
    result = await collector.collect_from_source(source, analyze=False, run_id=8501)
    assert result["staged"] == 1
    row = await CollectedItem.objects.filter(source_id=source.id).first()
    replay = row.as_agent_item()
    assert replay["metric_availability"] == items[0]["metric_availability"]
    assert replay["permalink"] == "https://example.com/original" and replay["author"]["id"] == 42
    stats = AIAnalyzer()._calculate_content_stats([replay])
    display = render_analysis({"content_statistics": stats})
    assert display["content_statistics"]["total_reactions"] == 0
    assert display["content_statistics"]["total_comments"] is None
    assert display["content_statistics"]["active_users"] == 1
    assert display["original_links"] == ["https://example.com/original"]
