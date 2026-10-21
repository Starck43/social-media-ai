"""The shared collection writer every save path goes through.

The web editor, CLI, API and chat tools must store the same canonical block, so
the parser plus `collection_block` is the single writer. These tests pin the
round trip, the job-type rule and the explicit clear, without a database.
"""

from __future__ import annotations

import pytest

from app.services.collection.intent import (
    CollectionIntentError,
    apply_collection,
    collection_block,
    collection_error_message,
    parse_collection_intent,
)

RAW = {
    "content_types": ["posts", "comments"],
    "engagement_types": ["likes"],
    "limit": 250,
    "queries": ["доставка", " Доставка "],
    "city": {"name": " Киров ", "mode": "prefer"},
    "discovery": {"people": True, "queries": ["дизайн"]},
    "comments": {"max_per_post": 5, "total": 40},
    "candidates": {"limit": 25, "confirm": "auto"},
    "date_window": {"start": "2026-10-01", "end": None},
}


def test_block_is_canonical_from_the_parser() -> None:
    block = collection_block(parse_collection_intent({"collection": RAW}))

    assert block["version"] == 1
    assert block["content_types"] == ["posts", "comments"]
    assert block["engagement_types"] == ["likes"]
    assert block["limit"] == 250
    assert block["queries"] == ["доставка"], "phrases are de-duplicated case-insensitively"
    assert block["city"] == {"name": "Киров", "mode": "prefer"}
    assert block["discovery"] == {"people": True, "communities": False, "queries": ["дизайн"]}
    assert block["comments"] == {"max_per_post": 5, "total": 40}
    assert block["candidates"] == {"limit": 25, "confirm": "auto"}
    assert block["date_window"] == {"start": "2026-10-01", "end": None}


def test_writer_round_trips_its_own_output() -> None:
    once = apply_collection({"period": "week"}, RAW, job_type="analyze")
    twice = apply_collection(once, once["collection"], job_type="analyze")

    assert twice == once
    assert twice["period"] == "week", "unrelated payload keys survive"


def test_writer_clears_on_none() -> None:
    payload = {"period": "week", "collection": {"content_types": ["posts"]}}
    assert apply_collection(payload, None, job_type="analyze") == {"period": "week"}


def test_writer_drops_the_block_for_job_types_that_never_collect() -> None:
    payload = {"period": "week", "collection": {"content_types": ["posts"]}}
    assert apply_collection(payload, {"content_types": ["posts"]}, job_type="digest") == {"period": "week"}


def test_writer_keeps_the_block_for_collect_and_analyze() -> None:
    for job_type in ("collect", "analyze"):
        payload = apply_collection({}, {"content_types": ["comments"]}, job_type=job_type)
        assert payload["collection"]["content_types"] == ["comments"]


def test_writer_rejects_a_malformed_block_with_an_operator_message() -> None:
    with pytest.raises(CollectionIntentError) as error:
        apply_collection({}, {"limit": 0}, job_type="analyze")
    message = collection_error_message(str(error.value))
    assert "лимит сбора" in message
    assert "collection_limit_out_of_range" in message
