"""Page/capability contract for collection providers."""

from __future__ import annotations

import pytest

from app.services.collection.pages import (
    CollectionProviderError,
    Page,
    ProviderCapabilities,
    decode_offset_cursor,
    encode_offset_cursor,
)


def test_page_defaults_are_empty_and_not_unsupported():
    page = Page.empty()
    assert page.items == ()
    assert page.next_cursor is None
    assert page.limit_reached is False
    assert page.total_available is None
    assert page.warnings == ()
    assert page.unsupported_reason is None


def test_unsupported_is_not_an_empty_page():
    page = Page.unsupported("telegram_post_search_unsupported", warnings=("one",))
    assert page.items == ()
    assert page.unsupported_reason == "telegram_post_search_unsupported"
    assert page.warnings == ("one",)


def test_capabilities_map_lane_types():
    caps = ProviderCapabilities(
        people_search=True,
        community_search=False,
        post_search=True,
        wall=True,
        comments=True,
        likes=True,
        reposts=False,
        profiles=True,
    )
    assert caps.supports("wall_posts")
    assert caps.supports("post_search")
    assert caps.supports("candidate_search", kind="person")
    assert not caps.supports("candidate_search", kind="community")
    assert caps.supports("comments")
    assert caps.supports("likes")
    assert not caps.supports("reposts")
    assert caps.supports("profile_enrichment")
    assert caps.missing("candidate_search", kind="community") == "candidate_search_community_unsupported"


def test_offset_cursor_round_trip_and_validation():
    assert decode_offset_cursor(None) == 0
    assert decode_offset_cursor(encode_offset_cursor(25)) == 25
    with pytest.raises(ValueError, match="cursor_invalid"):
        decode_offset_cursor("not-an-int")
    with pytest.raises(ValueError, match="cursor_offset_negative"):
        decode_offset_cursor("-1")
    with pytest.raises(ValueError, match="cursor_offset_negative"):
        encode_offset_cursor(-1)


def test_provider_error_carries_bounded_code():
    error = CollectionProviderError("credentials", "VK search is unavailable", retryable=False)
    assert error.code == "credentials"
    assert error.message == "VK search is unavailable"
    assert error.retryable is False
    assert str(error) == "VK search is unavailable"
