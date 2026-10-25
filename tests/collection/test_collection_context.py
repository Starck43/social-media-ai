"""Task collection context: intent + provider capability contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.collection.context import (
    build_collection_context,
    normalise_platform,
    platform_name,
)
from app.services.collection.intent import CollectionIntentError


def _payload(**collection):
    return {"collection": collection}


def test_missing_collection_block_keeps_post_only_context():
    context = build_collection_context({}, platform="vk", source_id=7)

    assert context.platform == "vk"
    assert context.source_id == 7
    assert context.intent.content_types == ("posts",)
    assert context.includes_comments is False
    assert context.engagement_types == ()
    assert context.warnings == ()


def test_full_vk_context_exposes_requested_features_without_warnings():
    context = build_collection_context(
        _payload(
            content_types=["posts", "comments"],
            engagement_types=["likes", "reposts"],
            comments={"max_per_post": 5, "total": 40},
            candidates={"limit": 12, "confirm": "auto"},
            city={"name": "Киров", "mode": "prefer"},
        ),
        platform="vk",
        source_id=7,
    )

    assert context.includes_comments is True
    assert context.engagement_types == ("likes", "reposts")
    assert context.capabilities.comments is True
    assert context.capabilities.likes is True
    assert context.capabilities.reposts is True
    assert context.capabilities.city_lookup is True
    assert context.warnings == ()
    summary = context.summary()
    assert summary["comments"] == {"enabled": True, "max_per_post": 5, "total": 40}
    assert summary["candidates"] == {"limit": 12, "confirm": "auto"}
    assert summary["city"] == {"name": "Киров", "mode": "prefer"}


def test_telegram_requested_lanes_are_explicit_warnings():
    context = build_collection_context(
        _payload(
            content_types=["posts", "comments"],
            engagement_types=["likes"],
            city="Киров",
        ),
        platform="telegram",
        source_id=9,
    )

    assert context.includes_comments is True
    assert "comments_unsupported" in context.warnings
    assert "likes_unsupported" in context.warnings
    assert "city_lookup_unsupported" in context.warnings
    assert "profiles_unsupported" in context.warnings
    assert context.capabilities.comments is False


def test_comments_or_engagement_without_a_post_lane_are_warned():
    comments_only = build_collection_context(_payload(content_types=["comments"]), platform="vk", source_id=1)
    assert "comments_require_posts" in comments_only.warnings

    comments_engagement = build_collection_context(
        _payload(content_types=["comments"], engagement_types=["likes"]),
        platform="vk",
        source_id=1,
    )
    assert "engagement_require_posts" in comments_engagement.warnings


def test_malformed_collection_fails_closed():
    with pytest.raises(CollectionIntentError, match="collection_limit_out_of_range"):
        build_collection_context(_payload(limit=0), platform="vk", source_id=1)


def test_platform_name_accepts_orm_enum_and_test_double():
    enum_value = SimpleNamespace(db_value="vk")
    source = SimpleNamespace(platform=SimpleNamespace(platform_type=enum_value))
    assert platform_name(source) == "vk"
    assert normalise_platform(" Telegram ") == "telegram"


def test_context_builds_the_matching_adapter():
    vk = build_collection_context({}, platform="vk", source_id=1)
    telegram = build_collection_context({}, platform="telegram", source_id=2)
    client = object()

    assert type(vk.adapter(client)).__name__ == "VKCollectionAdapter"
    assert type(telegram.adapter(client)).__name__ == "TelegramCollectionAdapter"
    assert vk.adapter(client).client is client
