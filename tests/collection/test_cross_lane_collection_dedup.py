"""Cross-lane collection deduplication: one identity claim per run.

Two sources of the same task can return the same wall or search item. The
collector must stage and analyse that identity once per `handle_collect`
invocation while every source still records its own selection evidence.
Identity is platform + content type + provider id, so a post and a comment
sharing an id stay distinct, and an item without an id is never suppressed.

The run-scoped seen set is in memory: a dry run may claim identities, a failed
staging must not, and a separate job starts empty. All dependencies here are
doubles; no engine, session, provider or network call is made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services.collection.comments import CommentExpansion
from app.services.collection.context import build_collection_context
from app.services.collection.planner import item_identity
from app.services.monitoring.collector import ContentCollector
from app.services.monitoring.selection import SelectionScope
from app.types import SourceType

SHARED_IDENTITY = ("vk", "post", "100_1")


class _Analyzer:
    """Records what inline analysis was asked to save, without a provider."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, list[object]]] = []
        self.reported_errors = 0

    async def analyze_content(self, items, source, force_reanalyze=False):
        self.calls.append((source.id, [item.get("external_id") for item in items]))
        return [{"saved": item.get("external_id")} for item in items]


class _Stager:
    """Stands in for `_stage_items`; can refuse one source to model a failure."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_for: set[int] = set()

    async def __call__(self, content, source, run_id, *, durable=False, selection=None, raise_on_error=False):
        self.calls.append(
            {
                "source_id": source.id,
                "ids": [item.get("external_id") for item in content],
                "items": [dict(item) for item in content],
                "raise_on_error": raise_on_error,
                "durable": durable,
            }
        )
        if source.id in self.fail_for:
            raise RuntimeError("staging unavailable")
        return len(content)


class _Ledger:
    """Captures the per-source selection evidence the collector records."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, source, content, selection, *, run_id, task_id, limit_count=None, limit_reached=False):
        self.calls.append(
            {
                "source_id": source.id,
                "content_count": len(content),
                "statuses": [decision.status for decision in selection.decisions],
                "limit_count": limit_count,
                "limit_reached": limit_reached,
            }
        )


def _source(source_id: int = 7, name: str = "wall", source_type: SourceType = SourceType.USER):
    return SimpleNamespace(
        id=source_id,
        name=name,
        tenant_id=1,
        platform_id=1,
        source_type=source_type,
        external_id=f"source-{source_id}",
        params={},
        last_checked=None,
        platform=SimpleNamespace(params={}, platform_type="vk"),
    )


def _item(external_id: str | None = "100_1", *, content_type: str = "post", platform: str = "vk", text: str = "text"):
    item = {"platform": platform, "content_type": content_type, "text": text}
    if external_id is not None:
        item["external_id"] = external_id
    return item


def _install_client(monkeypatch, content_by_source: dict[int, list[dict]]):
    async def fake_collect_data(source, content_type="posts"):
        return [dict(item) for item in content_by_source[source.id]]

    monkeypatch.setattr(
        "app.services.monitoring.collector.get_social_client",
        lambda _platform: SimpleNamespace(collect_data=fake_collect_data),
    )


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    """Pin the deployment caps so no `.env` value can truncate a test batch."""
    monkeypatch.setattr(settings, "SOCIAL_COLLECT_LIMIT", 50)
    monkeypatch.setattr(settings, "SOCIAL_COLLECT_HARD_LIMIT", 50)


@pytest.fixture(autouse=True)
def _no_external_calls():
    """Any SQL/HTTP from under these tests is a failure, not a real call."""
    import asyncpg
    import httpx
    from sqlalchemy import engine as sa_engine
    from sqlalchemy import pool as sa_pool

    def panic(*_args, **_kwargs):
        raise AssertionError("cross-lane dedup tests must not touch the database or network")

    with (
        patch.object(sa_engine.Engine, "connect", panic),
        patch.object(sa_pool.Pool, "connect", panic),
        patch.object(asyncpg.Connection, "execute", panic),
        patch.object(httpx.Client, "send", panic),
    ):
        yield


@pytest.fixture
def collector_env(monkeypatch):
    collector = ContentCollector.__new__(ContentCollector)
    analyzer = _Analyzer()
    stager = _Stager()
    ledger = _Ledger()
    cursor = AsyncMock(return_value=None)

    collector.analyzer = analyzer
    monkeypatch.setattr(ContentCollector, "_stage_items", stager)
    monkeypatch.setattr(
        ContentCollector,
        "_count_new_items",
        AsyncMock(side_effect=lambda content, source: len(content)),
    )
    monkeypatch.setattr(ContentCollector, "_retire_staged", AsyncMock(return_value=0))
    monkeypatch.setattr("app.services.monitoring.selection_store.record_collection_selection", ledger)
    monkeypatch.setattr("app.models.Source.objects.update_last_checked", cursor)
    # A staging failure re-raises; keep the failure-notification path out of it.
    monkeypatch.setattr("app.services.monitoring.collector.NOTIFICATIONS_AVAILABLE", False)

    return SimpleNamespace(
        collector=collector,
        analyzer=analyzer,
        stager=stager,
        ledger=ledger,
        cursor=cursor,
    )


@pytest.mark.asyncio
async def test_same_identity_across_two_sources_is_staged_and_analysed_once(collector_env, monkeypatch):
    env = collector_env
    wall, search = _source(7, "wall"), _source(8, "search", SourceType.SEARCH)
    _install_client(monkeypatch, {7: [_item("100_1")], 8: [_item("100_1")]})
    seen: set[tuple[str, str, str]] = set()

    first = await env.collector.collect_from_source(wall, analyze=True, seen_items=seen)
    second = await env.collector.collect_from_source(search, analyze=True, seen_items=seen)

    assert first["content_count"] == 1
    assert second["content_count"] == 1
    assert first["staged"] == 1
    assert second["staged"] == 0
    assert second["duplicate_count"] == 1
    # Only the first lane paid for staging and analysis.
    assert env.stager.calls[0]["ids"] == ["100_1"]
    assert [call for call in env.stager.calls[1:]] == []
    assert env.analyzer.calls == [(7, ["100_1"])]
    assert seen == {SHARED_IDENTITY}
    # Run-scoped suppression never removes a source's own selection evidence.
    assert [call["source_id"] for call in env.ledger.calls] == [7, 8]
    assert [call["content_count"] for call in env.ledger.calls] == [1, 1]
    assert all(call["statuses"] == ["selected"] for call in env.ledger.calls)
    # The duplicate is also excluded from the fresh-item counter.
    assert first["new_items"] == 1
    assert second["new_items"] == 0


def test_post_and_comment_with_the_same_external_id_are_distinct_identities():
    post = _item("55", content_type="post")
    comment = _item("55", content_type="comment")

    assert item_identity(post) == ("vk", "post", "55")
    assert item_identity(comment) == ("vk", "comment", "55")
    assert item_identity(post) != item_identity(comment)


@pytest.mark.asyncio
async def test_a_post_and_a_comment_sharing_an_id_are_both_kept(collector_env, monkeypatch):
    env = collector_env
    source = _source(7, "wall")
    _install_client(
        monkeypatch,
        {7: [_item("55", content_type="post"), _item("55", content_type="comment", text="comment")]},
    )
    seen: set[tuple[str, str, str]] = set()

    result = await env.collector.collect_from_source(source, analyze=True, seen_items=seen)

    assert result["staged"] == 2
    assert result["duplicate_count"] == 0
    assert env.stager.calls[0]["ids"] == ["55", "55"]
    assert env.analyzer.calls == [(7, ["55", "55"])]


@pytest.mark.asyncio
async def test_items_without_a_known_identity_are_never_suppressed(collector_env, monkeypatch):
    env = collector_env
    source = _source(7, "wall")
    first = _item(None, text="identical text")
    second = _item(None, text="identical text")

    assert item_identity(first) is None
    assert item_identity(second) is None

    _install_client(monkeypatch, {7: [first, second]})
    seen: set[tuple[str, str, str]] = set()

    result = await env.collector.collect_from_source(source, analyze=True, seen_items=seen)

    assert result["staged"] == 2
    assert result["duplicate_count"] == 0
    assert env.stager.calls[0]["ids"] == [None, None]
    assert seen == set()


@pytest.mark.asyncio
async def test_a_staging_failure_does_not_claim_the_identity(collector_env, monkeypatch):
    env = collector_env
    wall, search = _source(7, "wall"), _source(8, "search", SourceType.SEARCH)
    _install_client(monkeypatch, {7: [_item("42")], 8: [_item("42")]})
    env.stager.fail_for = {7}
    seen: set[tuple[str, str, str]] = set()

    with pytest.raises(RuntimeError):
        await env.collector.collect_from_source(wall, analyze=True, seen_items=seen)

    # Nothing was staged, so the identity stays claimable by a later lane.
    assert seen == set()
    assert env.analyzer.calls == []

    second = await env.collector.collect_from_source(search, analyze=True, seen_items=seen)

    assert second["staged"] == 1
    assert second["duplicate_count"] == 0
    assert seen == {("vk", "post", "42")}


@pytest.mark.asyncio
async def test_a_rejected_first_lane_does_not_claim_an_identity(collector_env, monkeypatch):
    env = collector_env
    wall, search = _source(7, "wall"), _source(8, "search", SourceType.SEARCH)
    item = _item("43", text="not a matching phrase")
    _install_client(monkeypatch, {7: [item], 8: [item]})
    seen: set[tuple[str, str, str]] = set()

    first = await env.collector.collect_from_source(
        wall,
        analyze=True,
        selection_scope=SelectionScope(keywords=("absent phrase",)),
        seen_items=seen,
    )
    second = await env.collector.collect_from_source(search, analyze=True, seen_items=seen)

    assert first["staged"] == 0
    assert first["duplicate_count"] == 0
    assert seen == {("vk", "post", "43")}
    assert second["staged"] == 1
    assert second["duplicate_count"] == 0
    assert env.stager.calls[0]["source_id"] == 8


@pytest.mark.asyncio
async def test_dry_run_claims_an_identity_in_memory_only(collector_env, monkeypatch):
    env = collector_env
    wall, search = _source(7, "wall"), _source(8, "search", SourceType.SEARCH)
    _install_client(monkeypatch, {7: [_item("7_1")], 8: [_item("7_1")]})
    seen: set[tuple[str, str, str]] = set()

    first = await env.collector.collect_from_source(wall, analyze=True, dry_run=True, seen_items=seen)
    second = await env.collector.collect_from_source(search, analyze=True, dry_run=True, seen_items=seen)

    assert first["duplicate_count"] == 0
    assert second["duplicate_count"] == 1
    assert first["staged"] == second["staged"] == 0
    assert first["analyzed"] is False
    assert second["analyzed"] is False
    assert env.stager.calls == []
    assert env.analyzer.calls == []
    assert env.ledger.calls == []
    env.cursor.assert_not_awaited()
    # The rehearsal still remembers what it saw for the rest of this run.
    assert seen == {("vk", "post", "7_1")}


def _task():
    return SimpleNamespace(
        id=5,
        payload={},
        tenant_id=1,
        agent_scenario=None,
        trigger_config=None,
        dry_run=False,
    )


def _job_source(source_id: int, name: str, source_type: SourceType):
    return SimpleNamespace(
        id=source_id,
        name=name,
        external_id=f"lane-{source_id}",
        params={},
        tenant_id=1,
        source_type=source_type,
        platform=SimpleNamespace(platform_type="vk"),
    )


@pytest.mark.asyncio
async def test_seen_scope_is_one_handle_collect_invocation_and_counts_are_reported(monkeypatch):
    from app.jobs import handlers as module

    task = _task()
    sources = [_job_source(11, "wall", SourceType.GROUP), _job_source(12, "search", SourceType.SEARCH)]
    invocations: list[dict] = []

    async def fake_collect(source, **kwargs):
        seen = kwargs["seen_items"]
        invocations.append({"source_id": source.id, "seen": seen, "snapshot": set(seen)})
        if source.id == 11:
            seen.add(SHARED_IDENTITY)
            return {
                "content_count": 1,
                "new_items": 1,
                "duplicate_count": 0,
                "staged": 1,
                "limit_count": 50,
                "limit_reached": False,
            }
        return {
            "content_count": 1,
            "new_items": 0,
            "duplicate_count": 1,
            "staged": 0,
            "limit_count": 50,
            "limit_reached": False,
        }

    collector = SimpleNamespace(collect_from_source=fake_collect)
    monkeypatch.setattr(module, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(module, "_resolve_sources", AsyncMock(return_value=sources))
    monkeypatch.setattr("app.services.monitoring.collector.ContentCollector", lambda: collector)
    monkeypatch.setattr("app.services.collection.lane_runs.record_lane_run", AsyncMock(return_value=None))
    progress = SimpleNamespace(step=AsyncMock(), update_current=AsyncMock(), begin_phase=AsyncMock())
    monkeypatch.setattr("app.jobs.progress.step", progress.step)
    monkeypatch.setattr("app.jobs.progress.update_current", progress.update_current)
    monkeypatch.setattr("app.jobs.progress.begin_phase", progress.begin_phase)

    first = await module.handle_collect({"agent_task_id": task.id})
    second = await module.handle_collect({"agent_task_id": task.id})

    # One run shares one scope: the second lane sees the first lane's claim.
    assert invocations[0]["seen"] is invocations[1]["seen"]
    assert invocations[0]["snapshot"] == set()
    assert invocations[1]["snapshot"] == {SHARED_IDENTITY}
    assert first["duplicate_count"] == 1
    assert [row["duplicate_count"] for row in first["per_source"]] == [0, 1]
    # The next invocation starts from an empty scope, not the previous job's.
    assert invocations[2]["seen"] is not invocations[0]["seen"]
    assert invocations[2]["snapshot"] == set()
    assert [row["duplicate_count"] for row in second["per_source"]] == [0, 1]


@pytest.mark.asyncio
async def test_a_comment_enabled_lane_keeps_a_new_comment_when_its_parent_was_claimed(collector_env, monkeypatch):
    """A bounded parent snapshot keeps context after run dedup suppresses its post."""
    env = collector_env
    source = _source(7, "wall")
    _install_client(monkeypatch, {7: [_item("100_1")]})
    context = build_collection_context(
        {
            "collection": {
                "content_types": ["posts", "comments"],
                "comments": {"max_per_post": 2, "total": 10},
            }
        },
        platform="vk",
        source_id=7,
    )
    assert context.includes_comments is True
    seen = {SHARED_IDENTITY}

    async def expand(_self, _content, _client, _context, _scope):
        return CommentExpansion(
            items=[
                {
                    "platform": "vk",
                    "content_type": "comment",
                    "external_id": "100_1:2",
                    "parent_external_id": "100_1",
                    "text": "a short reply",
                }
            ],
            posts=1,
            fetched=1,
        )

    monkeypatch.setattr(ContentCollector, "_expand_comments", expand)

    result = await env.collector.collect_from_source(
        source,
        analyze=True,
        collection_context=context,
        seen_items=seen,
    )

    assert result["duplicate_count"] == 1, "the parent identity is suppressed across lanes"
    assert result["staged"] == 1
    assert env.stager.calls[0]["ids"] == ["100_1:2"]
    assert env.stager.calls[0]["items"][0]["parent_context"]["external_id"] == "100_1"
    assert env.stager.calls[0]["items"][0]["parent_context"]["text"] == "text"
    assert env.analyzer.calls == [(7, ["100_1:2"])]
    assert seen == {SHARED_IDENTITY, ("vk", "comment", "100_1:2")}
    assert env.stager.calls[0]["durable"] is False
    assert result["content_hashes"]
