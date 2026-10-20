"""Collection intent contract: pure parsing, no database or provider calls."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.config import settings
from app.services.collection.intent import (
    CONTENT_COMMENTS,
    CONTENT_POSTS,
    ENGAGEMENT_LIKES,
    CollectionIntentError,
    parse_collection_intent,
)


def test_missing_block_keeps_post_only_behaviour():
    intent = parse_collection_intent({"cli_dates": {"start_date": "2026-10-01", "end_date": None}})

    assert intent.content_types == (CONTENT_POSTS,)
    assert intent.engagement_types == ()
    assert intent.limit == min(settings.SOCIAL_COLLECT_LIMIT, settings.SOCIAL_COLLECT_HARD_LIMIT)
    assert intent.start_date == date(2026, 10, 1)
    assert intent.end_date is None
    assert intent.queries == ()
    assert intent.city is None
    assert intent.discovery.people is False
    assert intent.discovery.communities is False
    assert intent.includes_comments is False


def test_full_block_is_normalised():
    intent = parse_collection_intent(
        {
            "collection": {
                "version": 1,
                "content_types": ["posts", "comments"],
                "engagement_types": ["likes", "reposts"],
                "limit": "250",
                "date_window": {"start": "2026-09-01T12:00:00", "end": "2026-10-01"},
                "queries": [" Древиз  Италия ", "древиз италия", "Кабанов"],
                "city": {"name": " Киров ", "mode": "require"},
                "discovery": {
                    "people": True,
                    "communities": True,
                    "queries": ["дизайн интерьера"],
                },
                "comments": {"max_per_post": 50, "total": 500},
                "candidates": {"limit": 25, "confirm": "auto"},
            }
        }
    )

    assert intent.content_types == (CONTENT_POSTS, CONTENT_COMMENTS)
    assert intent.engagement_types == (ENGAGEMENT_LIKES, "reposts")
    assert intent.limit == 250
    assert intent.start_date == date(2026, 9, 1)
    assert intent.end_date == date(2026, 10, 1)
    assert intent.queries == ("Древиз Италия", "Кабанов")
    assert intent.city is not None and intent.city.name == "Киров"
    assert intent.city.mode == "require"
    assert intent.discovery.people is True
    assert intent.discovery.communities is True
    assert intent.discovery.queries == ("дизайн интерьера",)
    assert intent.comments.max_per_post == 50
    assert intent.comments.total == 500
    assert intent.candidates.limit == 25
    assert intent.candidates.confirm == "auto"


def test_cli_dates_are_the_legacy_fallback():
    intent = parse_collection_intent({"cli_dates": {"start_date": "2026-01-02", "end_date": "2026-02-03"}})

    assert (intent.start_date, intent.end_date) == (date(2026, 1, 2), date(2026, 2, 3))


def test_explicit_date_window_wins_over_cli_dates():
    intent = parse_collection_intent(
        {
            "cli_dates": {"start_date": "2020-01-01"},
            "collection": {"date_window": {"start": "2026-03-04"}},
        }
    )

    assert intent.start_date == date(2026, 3, 4)


def test_empty_engagement_list_is_allowed():
    intent = parse_collection_intent({"collection": {"engagement_types": []}})

    assert intent.engagement_types == ()


def test_city_string_is_a_prefer_preference():
    intent = parse_collection_intent({"collection": {"city": "Киров"}})

    assert intent.city is not None
    assert intent.city.name == "Киров"
    assert intent.city.mode == "prefer"


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"collection": {"content_types": []}}, "collection_content_types_empty"),
        ({"collection": {"content_types": ["videos"]}}, "collection_content_types_unsupported"),
        ({"collection": {"limit": 0}}, "collection_limit_out_of_range"),
        ({"collection": {"limit": 10**9}}, "collection_limit_out_of_range"),
        ({"collection": {"city": {"name": ""}}}, "collection_city_name_invalid"),
        ({"collection": {"city": {"name": "Киров", "mode": "strict"}}}, "collection_city_mode_invalid"),
        (
            {"collection": {"date_window": {"start": "2026-02-01", "end": "2026-01-01"}}},
            "collection_date_window_reversed",
        ),
        ({"collection": {"candidates": {"confirm": "sometimes"}}}, "collection_candidates_confirm_invalid"),
        ({"collection": {"comments": {"max_per_post": 0}}}, "collection_comments_max_per_post_out_of_range"),
        ({"collection": []}, "collection_block_invalid"),
    ],
)
def test_malformed_known_values_fail_closed(payload, error):
    with pytest.raises(CollectionIntentError) as caught:
        parse_collection_intent(payload)

    assert str(caught.value) == error


def test_unknown_keys_are_ignored_for_forward_compatibility():
    intent = parse_collection_intent({"collection": {"content_types": ["posts"], "future_key": {"anything": True}}})

    assert intent.content_types == (CONTENT_POSTS,)
