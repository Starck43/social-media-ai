"""Parent-post caps: the `source_limit` contract and the collector's overlay.

The cap belongs to parent posts. A source may narrow the task's request but
never raise it, and the deployment ceiling applies on top of both. The
collector used to read the source's stored params directly; it now overlays the
effective cap on a copy for the provider call and restores the exact original
object afterwards, so a collection cannot mutate the stored source settings.
Comment expansion happens after the parent batch is bounded and must not make
the run look like it hit the parent cap.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.collection.comments import CommentExpansion
from app.services.collection.context import build_collection_context
from app.services.monitoring.collector import ContentCollector
from app.services.monitoring.selection_store import source_limit
from app.types import SourceType


def _limits(monkeypatch, *, default=500, hard=500):
    """Pin the deployment numbers so the arithmetic does not depend on `.env`."""
    monkeypatch.setattr(settings, "SOCIAL_COLLECT_LIMIT", default)
    monkeypatch.setattr(settings, "SOCIAL_COLLECT_HARD_LIMIT", hard)


def _source(params=None):
    return SimpleNamespace(
        id=7,
        name="wall",
        tenant_id=1,
        platform_id=1,
        source_type=SourceType.USER,
        params={} if params is None else params,
        last_checked=None,
        platform=SimpleNamespace(params={}),
    )


def _posts(count):
    return [{"external_id": f"7_{index}", "text": f"post-{index}", "platform": "vk"} for index in range(count)]


def _install_client(monkeypatch, collect_data):
    monkeypatch.setattr(
        "app.services.monitoring.collector.get_social_client",
        lambda _platform: SimpleNamespace(collect_data=collect_data),
    )


@pytest.mark.parametrize(
    ("params", "task_limit", "expected"),
    [
        ({}, None, 500),
        ({"collection": {"limit": 30}}, 250, 30),
        ({"collection": {"limit": 800}}, 250, 250),
        ({"collection": {"limit": 800}}, None, 500),
        ({}, 800, 500),
        ({"collection": {"limit": 30}}, None, 30),
        ({"limit": 7}, None, 7),
        ({"collection": {"limit": "abc"}}, 250, 250),
        ({"collection": {"limit": 0}}, 250, 250),
        ({}, 0, 500),
    ],
    ids=[
        "deployment-default",
        "source-narrows-task",
        "source-cannot-raise-task",
        "source-above-hard-clamped",
        "task-above-hard-clamped",
        "legacy-source-cap-without-task",
        "flat-legacy-cap",
        "malformed-source-cap-ignored",
        "non-positive-source-cap-ignored",
        "non-positive-task-cap-falls-back",
    ],
)
def test_source_limit_only_narrows_and_always_honours_the_ceiling(monkeypatch, params, task_limit, expected):
    _limits(monkeypatch)

    assert source_limit(_source(params), task_limit) == expected


def test_an_explicit_source_cap_may_raise_the_deployment_default_but_never_the_task_cap(monkeypatch):
    """An explicit source cap is still a request: it beats the default, not the task."""
    _limits(monkeypatch, default=20, hard=500)
    source = _source({"collection": {"limit": 60}})

    assert source_limit(source, None) == 60, "the legacy explicit source cap may raise the deployment default"
    assert source_limit(source, 40) == 40, "the source cannot raise the task's cap"


@pytest.mark.asyncio
async def test_the_provider_sees_the_effective_cap_in_a_copied_params_object(monkeypatch):
    """The provider call carries the applied cap; the stored params stay untouched."""
    _limits(monkeypatch)
    collector = ContentCollector()
    original = {"mode": "api", "collection": {"content_types": ["posts"], "limit": 100}}
    source = _source(original)
    context = build_collection_context({"collection": {"limit": 60}}, platform="vk", source_id=7)
    seen = {}

    async def collect_data(call_source, _content_type="posts"):
        seen["params"] = call_source.params
        seen["same_object"] = call_source.params is original
        return _posts(2)

    _install_client(monkeypatch, collect_data)
    monkeypatch.setattr(ContentCollector, "_count_new_items", AsyncMock(return_value=2))

    result = await collector.collect_from_source(source, analyze=False, dry_run=True, collection_context=context)

    assert seen["same_object"] is False, "the provider gets an overlay, not the stored object"
    assert seen["params"]["collection"]["limit"] == 60, "the task's narrower cap is what the provider sees"
    assert seen["params"]["mode"] == "api", "unrelated params survive the overlay"
    assert seen["params"]["collection"]["content_types"] == ["posts"]
    assert source.params is original
    assert source.params == {"mode": "api", "collection": {"content_types": ["posts"], "limit": 100}}
    assert result["limit_count"] == 60
    assert result["parent_count"] == 2
    assert result["limit_reached"] is False


@pytest.mark.asyncio
async def test_a_provider_failure_restores_the_exact_original_params(monkeypatch):
    """A failed provider call must not leave the overlaid cap on the source row."""
    _limits(monkeypatch)
    collector = ContentCollector()
    original = {"mode": "api", "collection": {"limit": 100}}
    source = _source(original)
    context = build_collection_context({"collection": {"limit": 60}}, platform="vk", source_id=7)
    seen = {}

    async def collect_data(call_source, _content_type="posts"):
        seen["same_object"] = call_source.params is original
        seen["limit"] = call_source.params["collection"]["limit"]
        raise RuntimeError("provider down")

    _install_client(monkeypatch, collect_data)
    # The failure path sends an operator notification through the ORM; that is
    # outside this contract and is disabled instead of doubled.
    monkeypatch.setattr("app.services.monitoring.collector.NOTIFICATIONS_AVAILABLE", False)

    with pytest.raises(RuntimeError, match="provider down"):
        await collector.collect_from_source(source, analyze=False, dry_run=True, collection_context=context)

    assert seen == {"same_object": False, "limit": 60}
    assert source.params is original
    assert source.params == {"mode": "api", "collection": {"limit": 100}}


@pytest.mark.asyncio
async def test_only_parent_posts_count_toward_the_applied_cap(monkeypatch):
    """A provider that ignores the cap is truncated, and the hit is recorded."""
    _limits(monkeypatch)
    collector = ContentCollector()
    context = build_collection_context({"collection": {"limit": 5}}, platform="vk", source_id=7)

    async def collect_data(_source, _content_type="posts"):
        return _posts(7)

    _install_client(monkeypatch, collect_data)
    monkeypatch.setattr(ContentCollector, "_count_new_items", AsyncMock(return_value=7))

    result = await collector.collect_from_source(_source({}), analyze=False, dry_run=True, collection_context=context)

    assert result["content_count"] == 5, "the batch is bounded before it is analysed"
    assert result["parent_count"] == 5
    assert result["limit_count"] == 5
    assert result["limit_reached"] is True


@pytest.mark.asyncio
async def test_comment_expansion_does_not_fake_a_parent_limit_hit(monkeypatch):
    """The parent batch is measured before comments; comments never fill the cap."""
    _limits(monkeypatch)
    collector = ContentCollector()
    context = build_collection_context(
        {
            "collection": {
                "limit": 5,
                "content_types": ["posts", "comments"],
                "comments": {"max_per_post": 3, "total": 20},
            }
        },
        platform="vk",
        source_id=7,
    )

    async def collect_data(_source, _content_type="posts"):
        return _posts(2)

    async def expand(_self, _content, _client, _context, _scope):
        return CommentExpansion(
            items=[
                {
                    "content_type": "comment",
                    "parent_external_id": "7_0",
                    "external_id": f"c{index}",
                    "text": f"comment-{index}",
                }
                for index in range(4)
            ],
            posts=2,
            fetched=4,
        )

    _install_client(monkeypatch, collect_data)
    monkeypatch.setattr(ContentCollector, "_count_new_items", AsyncMock(return_value=0))
    monkeypatch.setattr(ContentCollector, "_expand_comments", expand)

    result = await collector.collect_from_source(_source({}), analyze=False, dry_run=True, collection_context=context)

    assert result["parent_count"] == 2
    assert result["limit_count"] == 5
    assert result["content_count"] == 6, "posts and comments together make up the batch"
    assert result["limit_reached"] is False, "comments must not satisfy the parent cap"
    assert result["comments"]["fetched"] == 4
