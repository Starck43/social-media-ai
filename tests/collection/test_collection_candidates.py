"""Engagement lanes produce candidates, never text rows.

D3 executes the likes/reposts lanes the planner emits. The properties worth
proving with a double are the ones that make "candidates" different from
"items": identity is deduplicated across lanes and runs, one shared cap bounds
the whole task, and a failing lane cannot take the post batch with it.
"""

from __future__ import annotations

import pytest

from app.services.collection.candidates import (
    LIMIT_REACHED,
    CandidateBudget,
    expand_candidates,
)
from app.services.collection.pages import CollectionProviderError, Page
from app.services.monitoring.selection import SelectionScope, parse_tracked_user


def _post(external_id="1_10", author_id="1"):
    return {
        "platform": "vk",
        "content_type": "post",
        "external_id": external_id,
        "text": "пост",
        "author": {"platform": "vk", "external_id": author_id},
    }


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


class _Fetcher:
    """A provider double that pages through ``people`` by post external id."""

    def __init__(self, by_post):
        self.by_post = by_post
        self.calls = []

    async def __call__(self, post, limit, cursor):
        key = str(post.get("external_id"))
        offset = int(cursor or 0)
        rows = self.by_post.get(key, [])
        window = rows[offset : offset + limit]
        next_offset = offset + len(window)
        has_more = next_offset < len(rows)
        self.calls.append((key, limit, cursor))
        return Page(
            items=tuple(window),
            next_cursor=str(next_offset) if has_more else None,
            limit_reached=has_more,
            total_available=len(rows),
        )


def _scope(tracked=("vk:1",)):
    return SelectionScope(tracked_users=tuple(parse_tracked_user(value) for value in tracked))


@pytest.mark.asyncio
async def test_two_lanes_return_one_identity_with_lane_evidence():
    likes = _Fetcher({"1_10": [_person(5)]})
    reposts = _Fetcher({"1_10": [_person(5)]})

    expansion = await expand_candidates(
        [_post()],
        [("likes", likes), ("reposts", reposts)],
        budget=CandidateBudget(limit=10),
        scope=_scope(),
    )

    assert len(expansion.items) == 1
    assert expansion.by_lane == {"likes": 1, "reposts": 1}
    assert expansion.duplicates == 1
    assert expansion.items[0]["evidence"] == {"lane": "likes", "post_external_id": "1_10"}
    assert expansion.warnings == ()


@pytest.mark.asyncio
async def test_the_shared_cap_bounds_the_whole_task():
    likes = _Fetcher({"1_10": [_person(5), _person(6), _person(7)]})
    reposts = _Fetcher({"1_10": [_person(8)]})

    expansion = await expand_candidates(
        [_post()],
        [("likes", likes), ("reposts", reposts)],
        budget=CandidateBudget(limit=2),
        scope=_scope(),
    )

    assert len(expansion.items) == 2
    assert expansion.limit_reached is True
    assert expansion.warnings == (LIMIT_REACHED,)
    # The cap is task-wide: the second lane is never called.
    assert reposts.calls == []


@pytest.mark.asyncio
async def test_a_candidate_without_identity_is_not_stored():
    likes = _Fetcher({"1_10": [{"platform": "vk", "kind": "person", "external_id": ""}]})

    expansion = await expand_candidates(
        [_post()],
        [("likes", likes)],
        budget=CandidateBudget(limit=5),
        scope=_scope(),
    )

    assert expansion.items == []
    assert expansion.invalid == 1


@pytest.mark.asyncio
async def test_a_failed_lane_is_counted_and_the_next_lane_still_runs():
    async def failing(post, limit, cursor):  # noqa: ARG001 - adapter signature
        raise CollectionProviderError("vk_likes_failed", "denied")

    reposts = _Fetcher({"1_10": [_person(6)]})

    expansion = await expand_candidates(
        [_post()],
        [("likes", failing), ("reposts", reposts)],
        budget=CandidateBudget(limit=5),
        scope=_scope(),
    )

    assert expansion.errors == 1
    assert expansion.error_codes == ("vk_likes_failed",)
    assert len(expansion.items) == 1
    assert expansion.items[0]["evidence"]["lane"] == "reposts"


@pytest.mark.asyncio
async def test_a_post_from_an_untracked_author_is_not_engaged():
    likes = _Fetcher({"1_10": [_person(5)]})

    expansion = await expand_candidates(
        [_post(author_id="2")],
        [("likes", likes)],
        budget=CandidateBudget(limit=5),
        scope=_scope(),
    )

    assert expansion.fetched == 0
    assert likes.calls == []


def test_budget_rejects_a_non_positive_limit():
    with pytest.raises(ValueError):
        CandidateBudget(limit=0)
