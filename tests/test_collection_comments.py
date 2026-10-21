"""Comment expansion: which posts to read, how much, and what the caps hid.

Stage D2 executes the comments lane the planner already emits. The properties
worth proving without a platform are exactly the ones a silent implementation
gets wrong: a post is expanded on the *author* filter only, both caps bind
independently, and every cap that stopped the batch is reported.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.collection.comments import (
    LIMIT_PER_POST,
    LIMIT_TOTAL,
    CommentBudget,
    comment_parents,
    expand_comments,
)
from app.services.collection.pages import CollectionProviderError, Page
from app.services.monitoring.selection import (
    THREAD_COMMENT_MATCHED,
    THREAD_MATCHED,
    SelectionScope,
    parse_tracked_user,
    select_for_collection,
)
from app.utils.collected_content import build_staged_rows

PHRASE = "древиз"


def _post(external_id="1_10", author_id="1", text="приглашаем на встречу"):
    return {
        "platform": "vk",
        "content_type": "post",
        "external_id": external_id,
        "permalink": f"https://vk.com/wall{external_id}",
        "text": text,
        "author": {"platform": "vk", "external_id": author_id},
    }


def _comment(parent="1_10", comment_id=1, author_id="5", text="были в Древизе в Италии"):
    return {
        "platform": "vk",
        "content_type": "comment",
        "external_id": f"{parent}:{comment_id}",
        "parent_external_id": parent,
        "permalink": f"https://vk.com/wall{parent}?reply={comment_id}",
        "text": text,
        "author": {"platform": "vk", "external_id": author_id},
    }


class _Fetcher:
    """A provider double that pages through ``comments`` by external id."""

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


def _scope(*, keywords=(PHRASE,), tracked=("vk:1",)):
    return SelectionScope(
        tracked_users=tuple(parse_tracked_user(value) for value in tracked),
        keywords=tuple(keywords),
    )


def test_comment_parents_keep_a_post_whose_own_text_does_not_match():
    posts = [_post(author_id="1"), _post("1_11", author_id="2"), _comment()]

    parents = comment_parents(posts, _scope())

    assert [item["external_id"] for item in parents] == ["1_10"]


def test_a_post_without_tracked_author_is_not_expanded():
    assert comment_parents([_post(author_id="1")], SelectionScope()) == [_post(author_id="1")]

    assert comment_parents([_post(author_id="2")], _scope()) == []


@pytest.mark.asyncio
async def test_per_post_cap_stops_the_post_and_reports_it():
    fetcher = _Fetcher({"1_10": [_comment(comment_id=i) for i in range(5)]})

    expansion = await expand_comments(
        [_post()],
        fetcher,
        budget=CommentBudget(max_per_post=2, total=10),
        scope=_scope(),
    )

    assert expansion.fetched == 2
    assert expansion.posts == 1
    assert expansion.limit_reached is True
    assert expansion.warnings == (LIMIT_PER_POST,)


@pytest.mark.asyncio
async def test_total_cap_is_shared_across_parents():
    fetcher = _Fetcher(
        {
            "1_10": [_comment(comment_id=i) for i in range(3)],
            "1_11": [_comment("1_11", comment_id=i) for i in range(3)],
        }
    )

    expansion = await expand_comments(
        [_post(), _post("1_11")],
        fetcher,
        budget=CommentBudget(max_per_post=5, total=4),
        scope=_scope(),
    )

    assert expansion.fetched == 4
    assert expansion.posts == 2
    assert expansion.warnings == (LIMIT_TOTAL,)
    # The first post used three, so the second was only asked for the rest.
    assert fetcher.calls == [("1_10", 4, None), ("1_11", 1, None)]


@pytest.mark.asyncio
async def test_a_short_page_is_a_complete_post_not_a_truncation():
    fetcher = _Fetcher({"1_10": [_comment(comment_id=1), _comment(comment_id=2)]})

    expansion = await expand_comments(
        [_post()],
        fetcher,
        budget=CommentBudget(max_per_post=5, total=10),
        scope=_scope(),
    )

    assert expansion.fetched == 2
    assert expansion.limit_reached is False
    assert expansion.warnings == ()


@pytest.mark.asyncio
async def test_a_failed_post_is_counted_and_the_next_post_still_runs():
    parent = _post()

    async def fetch(post, limit, cursor):  # noqa: ARG001 - signature of the adapter
        if post.get("external_id") == "1_10":
            raise CollectionProviderError("vk_comment_failed", "denied")
        return Page(items=(_comment("1_11", comment_id=9),))

    expansion = await expand_comments(
        [parent, _post("1_11")],
        fetch,
        budget=CommentBudget(max_per_post=3, total=10),
        scope=_scope(),
    )

    assert expansion.errors == 1
    assert expansion.error_codes == ("vk_comment_failed",)
    assert expansion.fetched == 1
    assert expansion.posts == 2


@pytest.mark.asyncio
async def test_budget_rejects_a_non_positive_cap():
    with pytest.raises(ValueError):
        CommentBudget(max_per_post=0, total=10)
    with pytest.raises(ValueError):
        CommentBudget(max_per_post=1, total=0)


@pytest.mark.asyncio
async def test_a_platform_without_comment_support_requests_nothing():
    from app.services.collection.context import build_collection_context
    from app.services.monitoring.collector import ContentCollector

    context = build_collection_context({"collection": {"content_types": ["posts", "comments"]}}, platform="telegram")

    expansion = await ContentCollector()._expand_comments([_post()], SimpleNamespace(), context, None)

    assert expansion is None
    assert "comments_unsupported" in context.warnings


@pytest.mark.asyncio
async def test_a_matching_comment_keeps_its_parent_through_selection_and_staging():
    fetcher = _Fetcher({"1_10": [_comment(comment_id=1)]})
    # A comment-enabled task that wants anyone discussing the phrase: the author
    # layer would reject a comment by an author the task did not list.
    scope = _scope(tracked=())

    expansion = await expand_comments(
        [_post()],
        fetcher,
        budget=CommentBudget(max_per_post=5, total=10),
        scope=scope,
    )
    selection = select_for_collection([_post(), *expansion.items], scope, thread=True)
    rows = build_staged_rows([*selection.selected], SimpleNamespace(id=7, params={}), run_id=1)

    reasons = {decision.item.get("content_type") or "post": decision.reason for decision in selection.decisions}
    assert reasons == {"post": THREAD_MATCHED, "comment": THREAD_COMMENT_MATCHED}
    comment_row = next(row for row in rows if row["content_type"] == "comment")
    assert comment_row["parent_external_id"] == "1_10"
    assert comment_row["selection_status"] == "selected"
