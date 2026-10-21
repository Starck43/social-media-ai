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
from types import SimpleNamespace

import pytest

from app.core.tenant_context import tenant_scope
from app.models import (
    AgentTask,
    CollectedItem,
    CollectionCandidate,
    CollectionLaneRun,
    CollectionSeen,
    Platform,
    Source,
)
from app.services.collection.pages import Page
from app.services.monitoring.selection import SelectionScope
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
    await CollectionCandidate.objects.filter(source_id=s.id).delete()
    await CollectionLaneRun.objects.filter(lane_id=f"source:{s.id}").delete()
    await Source.objects.delete_by_id(s.id)
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture(autouse=True)
def _source_tenant_scope(request):
    """Give this file's raw-SQL paths the source tenant, without global bypass.

    `@pytest.mark.tenancy` tests build their own workspaces and scopes instead.
    """
    if request.node.get_closest_marker("tenancy"):
        yield
        return
    source = request.getfixturevalue("source")
    with tenant_scope(source.tenant_id):
        yield


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
    assert {r.content_type for r in rows} == {"post"}
    # The run id is what lets a later analysis retire exactly this batch.
    assert all(r.run_id == 4242 for r in rows)


async def test_post_and_comment_identity_do_not_collide(source):
    """A comment is a different object from its parent post, even with the same id."""
    post = await CollectedItem.objects.create(
        source_id=source.id,
        external_id="1_2",
        content_type="post",
        content_hash="a" * 64,
        text="post",
    )
    comment = await CollectedItem.objects.create(
        source_id=source.id,
        external_id="1_2",
        content_type="comment",
        parent_external_id="1",
        content_hash="b" * 64,
        text="comment",
    )

    assert post.content_type == "post"
    assert comment.content_type == "comment"
    assert comment.parent_external_id == "1"
    rows = await CollectedItem.objects.filter(source_id=source.id)
    assert {(row.content_type, row.external_id) for row in rows} == {("post", "1_2"), ("comment", "1_2")}


async def test_analysis_drain_skips_needs_review_but_keeps_legacy_rows(source):
    selected = await CollectedItem.objects.create(
        source_id=source.id,
        external_id="3_1",
        content_hash="1" * 64,
        selection_status="selected",
    )
    review = await CollectedItem.objects.create(
        source_id=source.id,
        external_id="3_2",
        content_hash="2" * 64,
        selection_status="needs_review",
    )
    legacy = await CollectedItem.objects.create(
        source_id=source.id,
        external_id="3_3",
        content_hash="3" * 64,
    )

    rows = await CollectedItem.objects.for_source(source.id)
    assert {row.id for row in rows} == {selected.id, legacy.id}
    assert review.id not in {row.id for row in rows}


async def test_collection_selection_stages_only_selected_rows_and_records_evidence(source):
    """Rejected text is not staged; its identity still lands in the seen ledger."""
    items = [
        {"platform": "vk", "external_id": "1_1", "text": "Поездка в Италию", "published_at": "2026-10-02T10:00:00Z"},
        {"platform": "vk", "external_id": "1_2", "text": "Совсем другое", "published_at": "2026-10-02T10:00:00Z"},
    ]
    c = _collector(items)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9101,
        selection_scope=SelectionScope(keywords=("Италия",)),
    )

    assert result["selected"] == 1
    assert result["rejected"] == 1
    assert result["staged"] == 1
    rows = await CollectedItem.objects.filter(source_id=source.id)
    assert [row.external_id for row in rows] == ["1_1"]
    assert rows[0].selection_status == "selected"
    assert rows[0].selection_reason == "text_matched"
    seen = {row.external_id: row.status for row in await CollectionSeen.objects.filter(source_id=source.id)}
    assert seen == {"1_1": "selected", "1_2": "rejected"}
    lane = await CollectionLaneRun.objects.filter(job_id=9101).first()
    try:
        assert lane.fetched == 2
        assert lane.selected == 1
        assert lane.rejected == 1
    finally:
        await CollectionLaneRun.objects.filter(job_id=9101).delete()


async def test_rejected_item_is_seen_not_new_and_can_be_reconsidered(source):
    items = [
        {"platform": "vk", "external_id": "4_1", "text": "Поездка в Италию", "published_at": "2026-10-02T10:00:00Z"},
        {"platform": "vk", "external_id": "4_2", "text": "Совсем другое", "published_at": "2026-10-02T10:00:00Z"},
    ]
    c = _collector(items)
    first = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9201,
        selection_scope=SelectionScope(keywords=("Италия",)),
    )
    assert first["staged"] == 1

    second = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9202,
        selection_scope=SelectionScope(keywords=("Италия",)),
    )
    assert second["new_items"] == 0

    third = await c.collect_from_source(source, analyze=False, run_id=9203, selection_scope=SelectionScope())
    assert third["selected"] == 2
    rows = {row.external_id: row for row in await CollectedItem.objects.filter(source_id=source.id)}
    assert rows["4_2"].selection_status == "selected"
    assert rows["4_2"].selection_reason == "no_tracked_users"


async def test_thread_selection_keeps_a_comment_matching_a_post(source):
    items = [
        {"platform": "vk", "external_id": "2_1", "content_type": "post", "text": "пост без фразы"},
        {
            "platform": "vk",
            "external_id": "2_1:9",
            "content_type": "comment",
            "parent_external_id": "2_1",
            "text": "в комментарии Италия",
            "author": {"platform": "vk", "external_id": "5", "id": 5},
        },
    ]
    c = _collector(items)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9102,
        selection_scope=SelectionScope(keywords=("Италия",)),
    )

    assert result["staged"] == 2
    rows = {row.external_id: row for row in await CollectedItem.objects.filter(source_id=source.id)}
    assert set(rows) == {"2_1", "2_1:9"}
    assert rows["2_1:9"].parent_external_id == "2_1"
    assert rows["2_1:9"].selection_status == "selected"


class _CommentAdapter:
    """The provider primitive the collector calls for one parent post."""

    def __init__(self, by_post):
        self.by_post = by_post
        self.calls = []

    async def fetch_comments(self, post, limit, cursor):
        rows = self.by_post.get(str(post.get("external_id")), [])
        self.calls.append((str(post.get("external_id")), limit, cursor))
        return Page(items=tuple(rows[:limit]))


class _CommentContext:
    """A comment-enabled per-source context backed by the adapter double."""

    includes_comments = True
    capabilities = SimpleNamespace(comments=True)
    intent = SimpleNamespace(comments=SimpleNamespace(max_per_post=5, total=50))

    def __init__(self, adapter):
        self._adapter = adapter

    def adapter(self, client):  # noqa: ARG002 - the context owns the client choice
        return self._adapter

    def summary(self):
        return {"comments": {"enabled": True}}


async def test_comment_context_expands_comments_and_stages_the_thread(source):
    """A comment-enabled task fetches the lane the context asked for.

    The parent post carries none of the phrase: the comment is what makes the
    thread relevant, so both rows are staged and the comment keeps its parent.
    """
    posts = [
        {
            "platform": "vk",
            "external_id": "3_1",
            "content_type": "post",
            "text": "пост без фразы",
            "published_at": "2026-10-02T10:00:00Z",
        }
    ]
    adapter = _CommentAdapter(
        {
            "3_1": [
                {
                    "platform": "vk",
                    "external_id": "3_1:9",
                    "content_type": "comment",
                    "parent_external_id": "3_1",
                    "text": "в комментарии Италия",
                    "author": {"platform": "vk", "external_id": "5", "id": 5},
                }
            ]
        }
    )
    c = _collector(posts)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9103,
        selection_scope=SelectionScope(keywords=("Италия",)),
        collection_context=_CommentContext(adapter),
    )

    assert adapter.calls == [("3_1", 5, None)]
    assert result["comments"] == {
        "posts": 1,
        "fetched": 1,
        "limit_reached": False,
        "warnings": [],
        "errors": 0,
        "error_codes": [],
    }
    assert result["staged"] == 2
    rows = {row.external_id: row for row in await CollectedItem.objects.filter(source_id=source.id)}
    assert set(rows) == {"3_1", "3_1:9"}
    assert rows["3_1"].selection_reason == "thread_matched"
    assert rows["3_1:9"].content_type == "comment"
    assert rows["3_1:9"].parent_external_id == "3_1"
    assert rows["3_1:9"].selection_reason == "thread_comment_matched"


class _CollectionAdapter:
    """The provider primitives the collector calls: engagement and profiles."""

    def __init__(self, likes=None, reposts=None, profiles=None):
        self.likes = likes or {}
        self.reposts = reposts or {}
        self.profiles = profiles or {}
        self.profile_calls: list[list[str]] = []

    async def fetch_likes(self, post, limit, cursor):
        return self._page(self.likes, post, limit, cursor)

    async def fetch_reposts(self, post, limit, cursor):
        return self._page(self.reposts, post, limit, cursor)

    async def fetch_profiles(self, ids):
        self.profile_calls.append([str(identifier) for identifier in ids])
        return [
            {"platform": "vk", "external_id": str(identifier), "city": self.profiles[str(identifier)]}
            for identifier in ids
            if str(identifier) in self.profiles
        ]

    @staticmethod
    def _page(by_post, post, limit, cursor):
        rows = by_post.get(str(post.get("external_id")), [])
        offset = int(cursor or 0)
        return Page(items=tuple(rows[offset : offset + limit]))


class _CollectionContext:
    """A per-source context double: lanes, city preference and capabilities."""

    def __init__(
        self,
        adapter,
        *,
        engagements=("likes", "reposts"),
        city=None,
        mode="prefer",
        limit=10,
        confirm="manual",
    ):
        self._adapter = adapter
        self.engagement_types = tuple(engagements)
        self.capabilities = SimpleNamespace(
            comments=False,
            profiles=city is not None,
            supports=lambda lane: lane in self.engagement_types,
        )
        self.intent = SimpleNamespace(
            candidates=SimpleNamespace(limit=limit, confirm=confirm),
            comments=SimpleNamespace(max_per_post=1, total=1),
            city=SimpleNamespace(name=city, mode=mode) if city is not None else None,
        )

    def adapter(self, client):  # noqa: ARG002 - the context owns the client choice
        return self._adapter

    def summary(self):
        return {"engagement_types": list(self.engagement_types)}


def _person(external_id, name="Анна"):
    return {
        "platform": "vk",
        "kind": "person",
        "external_id": str(external_id),
        "name": name,
        "url": f"https://vk.com/id{external_id}",
        "metrics": {"followers_count": 10},
        "evidence": {},
    }


async def test_engagement_candidates_are_stored_outside_the_text_batch(source):
    """Likes and reposts produce candidates, never `collected_items` rows."""
    posts = [{"platform": "vk", "external_id": "3_2", "content_type": "post", "text": "пост"}]
    adapter = _CollectionAdapter(likes={"3_2": [_person(5)]}, reposts={"3_2": [_person(6)]})
    c = _collector(posts)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9104,
        collection_context=_CollectionContext(adapter),
    )

    assert result["content_count"] == 1
    assert result["staged"] == 1
    assert result["candidates"]["fetched"] == 2
    assert result["candidates"]["stored"] == 2
    assert result["candidates"]["confirm"] == "manual"
    # Only the post is raw content; a candidate is not text.
    rows = await CollectedItem.objects.filter(source_id=source.id)
    assert [row.external_id for row in rows] == ["3_2"]
    candidates = {
        row.external_id: row for row in await CollectionCandidate.objects.filter(source_id=source.id)
    }
    assert set(candidates) == {"5", "6"}
    assert {row.kind for row in candidates.values()} == {"person"}
    assert {row.status for row in candidates.values()} == {"new"}
    assert {row.source_id for row in candidates.values()} == {source.id}
    assert candidates["5"].evidence["lane"] == "likes"
    assert candidates["5"].evidence["post_external_id"] == "3_2"


async def test_a_second_run_does_not_duplicate_or_reset_a_candidate_decision(source):
    posts = [{"platform": "vk", "external_id": "3_3", "content_type": "post", "text": "пост"}]
    adapter = _CollectionAdapter(likes={"3_3": [_person(5), _person(6)]})
    c = _collector(posts)
    context = _CollectionContext(adapter)

    first = await c.collect_from_source(source, analyze=False, run_id=9105, collection_context=context)
    assert first["candidates"]["stored"] == 2
    confirmed = await CollectionCandidate.objects.get(source_id=source.id, external_id="5")
    await CollectionCandidate.objects.filter(id=confirmed.id).update(status="confirmed")

    second = await c.collect_from_source(source, analyze=False, run_id=9106, collection_context=context)

    assert second["candidates"]["stored"] == 0
    rows = {row.external_id: row for row in await CollectionCandidate.objects.filter(source_id=source.id)}
    assert set(rows) == {"5", "6"}
    # A re-collection is not a reason to reopen a decision an operator made.
    assert rows["5"].status == "confirmed"
    assert rows["6"].status == "new"


async def test_auto_confirmation_marks_candidates_confirmed(source):
    posts = [{"platform": "vk", "external_id": "3_4", "content_type": "post", "text": "пост"}]
    adapter = _CollectionAdapter(likes={"3_4": [_person(7)]})
    c = _collector(posts)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9107,
        collection_context=_CollectionContext(adapter, confirm="auto"),
    )

    assert result["candidates"]["confirm"] == "auto"
    row = await CollectionCandidate.objects.get(source_id=source.id, external_id="7")
    assert row.status == "confirmed"


async def test_a_dry_run_previews_candidates_without_storing_them(source):
    posts = [{"platform": "vk", "external_id": "3_5", "content_type": "post", "text": "пост"}]
    adapter = _CollectionAdapter(likes={"3_5": [_person(8)]})
    c = _collector(posts)

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9108,
        dry_run=True,
        collection_context=_CollectionContext(adapter),
    )

    assert result["candidates"]["fetched"] == 1
    assert result["candidates"]["stored"] == 0
    assert result["candidates"]["dry_run"] is True
    assert await CollectionCandidate.objects.filter(source_id=source.id) == []


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


async def test_receipt_only_staged_skip_does_not_replay_old_analytics_actions(source, monkeypatch):
    """A deleted-analytics receipt skip cannot replay recent results as actions."""
    from datetime import date
    from unittest.mock import AsyncMock

    from app.jobs import handlers
    from app.models import AIAnalytics, BotAction
    from app.services.ai import analyzer as analyzer_mod
    from app.services.ai.dedup import item_hash
    from app.types import AgentActionType

    items = _items(1)
    await _collector(items).collect_from_source(source, analyze=False, run_id=667)
    task, _scenario = await _task_with_scenario()
    await AgentTask.objects.update_by_id(task.id, action_type=AgentActionType.COMMENT)
    old = await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today(),
        summary_data={"summary": "old saved result", "recent_posts": []},
        response_payload={"response": "old response"},
    )

    async def receipt_hit(self, content, source_, **kwargs):
        self.completed_hashes = {item_hash(items[0])}
        self.current_fingerprint = "a" * 64
        return None

    monkeypatch.setattr(analyzer_mod.AIAnalyzer, "analyze_content", receipt_hit)
    monkeypatch.setattr(handlers, "_retire_staged", AsyncMock(return_value=0))
    monkeypatch.setattr(handlers, "_count_failed_staged", AsyncMock(return_value=0))
    action_create = AsyncMock(side_effect=AssertionError("old analytics must not create an action"))
    monkeypatch.setattr(BotAction.objects, "create", action_create)
    try:
        result = await handlers.handle_analyze({"agent_task_id": task.id})
    finally:
        await AIAnalytics.objects.delete_by_id(old.id)
        await AgentTask.objects.delete_by_id(task.id)

    assert result["actions_created"] == 0
    assert result["analyzed"] == 0
    action_create.assert_not_awaited()


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


@pytest.mark.tenancy
async def test_prune_deletes_analyses_older_than_data_retention(monkeypatch):
    """`DATA_RETENTION_MONTHS` governs analysis results by last write, not publication date.

    Runs like the worker: `execute_job` wraps the handler in
    `tenant_scope(job.tenant_id)`, so the real tenant guard is on (no legacy
    bypass). A stale row goes; a fresh row with an old `analysis_date` stays; a
    stale row a bot action still references stays as that action's evidence;
    another workspace's stale row is out of scope.
    """
    from app.core.config import settings
    from app.core.permissions import service_permission_scope
    from app.jobs.handlers import handle_prune
    from app.models import AgentScenario, AIAnalytics, BotAction, Tenant
    from app.types import AgentActionType, BotActionStatus, PeriodType

    monkeypatch.setattr(settings, "DATA_RETENTION_MONTHS", 1)
    old = datetime.now(timezone.utc) - timedelta(days=70)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=400)).date()
    suffix = uuid.uuid4().hex[:8]
    tenants, sources = [], []
    platform = scenario = action = None

    def make(source_id, day):
        return AIAnalytics.objects.create(
            source_id=source_id, analysis_date=day, period_type=PeriodType.DAY, summary_data={"retention": True}
        )

    try:
        with tenant_scope(bypass=True):
            for label in ("own", "other"):
                tenants.append(await Tenant.objects.create(name=f"ret-{label}-{suffix}", slug=f"ret-{label}-{suffix}"))
            platform = await Platform.objects.create(
                name=f"vk_{suffix}", platform_type="vk", base_url="https://vk.com", params={}
            )
        own, other = tenants
        for tenant in tenants:
            with tenant_scope(tenant.id), service_permission_scope("source", "create"):
                sources.append(
                    await Source.objects.create(
                        platform_id=platform.id,
                        name=f"ret-src-{tenant.id}",
                        source_type=SourceType.CHANNEL.name,
                        external_id=f"ret-{suffix}-{tenant.id}",
                        is_active=True,
                    )
                )
        own_source, other_source = sources

        with tenant_scope(own.id):
            stale = await make(own_source.id, long_ago)
            fresh_old_date = await make(own_source.id, long_ago - timedelta(days=1))
            evidence = await make(own_source.id, long_ago - timedelta(days=2))
            with service_permission_scope("agentscenario", "create"):
                scenario = await AgentScenario.objects.create(
                    name=f"ret-sc-{suffix}", analysis_types=["summary"], content_types=["posts"], is_active=True
                )
            action = await BotAction.objects.create(
                agent_scenario_id=scenario.id,
                source_id=own_source.id,
                analytics_id=evidence.id,
                action_type=AgentActionType.COMMENT,
                status=BotActionStatus.PENDING,
                payload={"text": "t", "post_id": 1},
                dry_run=True,
            )
            await AIAnalytics.objects.filter(id__in=[stale.id, evidence.id]).update(updated_at=old)
        with tenant_scope(other.id):
            foreign = await make(other_source.id, long_ago)
            await AIAnalytics.objects.filter(id=foreign.id).update(updated_at=old)

        with tenant_scope(own.id):
            result = await handle_prune({"days": 7})

        assert result["retention_months"] == 1
        assert result["analytics_deleted"] == 1
        assert "analytics_retention_failed" not in result
        with tenant_scope(own.id):
            assert await AIAnalytics.objects.get(id=stale.id) is None
            assert await AIAnalytics.objects.get(id=fresh_old_date.id) is not None
            assert await AIAnalytics.objects.get(id=evidence.id) is not None
        with tenant_scope(other.id):
            assert await AIAnalytics.objects.get(id=foreign.id) is not None
    finally:
        with tenant_scope(bypass=True):
            if action is not None:
                await BotAction.objects.delete_by_id(action.id)
            for src in sources:
                await AIAnalytics.objects.filter(source_id=src.id).delete()
            if scenario is not None:
                await AgentScenario.objects.delete_by_id(scenario.id)
            for src in sources:
                await Source.objects.delete_by_id(src.id)
            if platform is not None:
                await Platform.objects.delete_by_id(platform.id)
            for tenant in reversed(tenants):
                await Tenant.objects.delete_by_id(tenant.id)


def _authored_post(external_id, author_id="7", text="пост"):
    return {
        "platform": "vk",
        "external_id": external_id,
        "content_type": "post",
        "text": text,
        "author": {"platform": "vk", "external_id": author_id},
    }


async def test_an_unknown_city_lands_in_needs_review(source):
    """An author without a declared city waits for an operator, not a guess."""
    c = _collector([_authored_post("4_1")])
    adapter = _CollectionAdapter(profiles={})

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9201,
        collection_context=_CollectionContext(adapter, engagements=(), city="Киров"),
    )

    assert result["selected"] == 0
    assert result["needs_review"] == 1
    assert result["staged"] == 1
    assert result["profiles"]["city"] == "Киров"
    assert result["profiles"]["requested"] == 1
    assert result["profiles"]["unknown"] == 1
    assert result["selection"]["city"] == {
        "name": "Киров",
        "mode": "prefer",
        "matched": 0,
        "unknown": 1,
        "mismatch": 0,
    }
    row = await CollectedItem.objects.get(source_id=source.id, external_id="4_1")
    assert row.selection_status == "needs_review"
    assert row.selection_reason == "city_unknown"


async def test_a_declared_city_selects_the_post_and_enriches_candidates(source):
    c = _collector([_authored_post("4_2")])
    adapter = _CollectionAdapter(
        likes={"4_2": [_person(5)]},
        profiles={"7": "Киров", "5": "Киров"},
    )

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9202,
        collection_context=_CollectionContext(adapter, engagements=("likes",), city="Киров"),
    )

    assert result["selected"] == 1
    assert result["needs_review"] == 0
    assert result["selection"]["city"]["matched"] == 1
    assert result["candidates"]["stored"] == 1
    row = await CollectedItem.objects.get(source_id=source.id, external_id="4_2")
    assert row.selection_status == "selected"
    candidate = await CollectionCandidate.objects.get(source_id=source.id, external_id="5")
    assert candidate.city == "Киров"
    assert candidate.city_source == "profile"
    assert candidate.city_confidence == 1.0


async def test_city_require_rejects_a_post_from_another_city(source):
    c = _collector([_authored_post("4_3")])
    adapter = _CollectionAdapter(profiles={"7": "Москва"})

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9203,
        collection_context=_CollectionContext(adapter, engagements=(), city="Киров", mode="require"),
    )

    assert result["rejected"] == 1
    assert result["staged"] == 0
    assert result["selection"]["city"]["mismatch"] == 1
    assert await CollectedItem.objects.filter(source_id=source.id) == []


async def test_a_city_gate_is_skipped_when_the_platform_cannot_enrich(source):
    """No profile capability means an explicit reason, not a whole batch in review."""
    c = _collector([_authored_post("4_4")])
    adapter = _CollectionAdapter()
    context = _CollectionContext(adapter, engagements=(), city="Киров")
    context.capabilities = SimpleNamespace(comments=False, profiles=False, supports=lambda lane: False)

    result = await c.collect_from_source(source, analyze=False, run_id=9204, collection_context=context)

    assert result["profiles"]["supported"] is False
    assert result["profiles"]["error_code"] == "profiles_unsupported"
    assert result["selected"] == 1
    assert result["needs_review"] == 0
    assert adapter.profile_calls == []


async def test_a_dry_run_previews_the_city_gate_without_storing(source):
    c = _collector([_authored_post("4_5")])
    adapter = _CollectionAdapter(profiles={})

    result = await c.collect_from_source(
        source,
        analyze=False,
        run_id=9205,
        dry_run=True,
        collection_context=_CollectionContext(adapter, engagements=(), city="Киров"),
    )

    assert result["profiles"]["unknown"] == 1
    assert result["selection"]["city"]["unknown"] == 1
    assert result["staged"] == 0
    assert await CollectedItem.objects.filter(source_id=source.id) == []
