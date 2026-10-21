"""Telegram adapter: channel pull plus explicit unsupported lanes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.collection.adapters.telegram import TelegramCollectionAdapter


def _adapter(items=None):
    client = SimpleNamespace(collect_data=AsyncMock(return_value=items or []))
    return TelegramCollectionAdapter(client), client


@pytest.mark.asyncio
async def test_fetch_wall_uses_existing_channel_pull_and_reports_limit():
    adapter, client = _adapter(
        [
            {"external_id": "1", "text": "one"},
            {"external_id": "2", "text": "two"},
        ]
    )
    page = await adapter.fetch_wall(SimpleNamespace(name="chat"), None, None, 2, None)

    assert [item["external_id"] for item in page.items] == ["1", "2"]
    assert page.items[0]["platform"] == "telegram"
    assert page.items[0]["content_type"] == "post"
    assert page.limit_reached is True
    assert page.next_cursor is None
    client.collect_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_and_engagement_lanes_are_explicitly_unsupported():
    adapter, _ = _adapter()
    city = await adapter.resolve_city("Киров")
    people = await adapter.search_people("дизайнер", None, 10, None)
    communities = await adapter.search_communities("дизайн", None, 10, None)
    posts = await adapter.search_posts("Древиз", None, None, 10, None)
    comments = await adapter.fetch_comments({}, 10, None)
    likes = await adapter.fetch_likes({}, 10, None)
    reposts = await adapter.fetch_reposts({}, 10, None)

    assert city is None
    assert people.unsupported_reason == "telegram_people_search_unsupported"
    assert communities.unsupported_reason == "telegram_community_search_unsupported"
    assert posts.unsupported_reason == "telegram_post_search_unsupported"
    assert comments.unsupported_reason == "telegram_comments_unsupported"
    assert likes.unsupported_reason == "telegram_likes_unsupported"
    assert reposts.unsupported_reason == "telegram_reposts_unsupported"
    assert await adapter.fetch_profiles(["1"]) == []
