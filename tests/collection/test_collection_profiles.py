"""Profile enrichment: the city of a post comes from its author's profile.

D4 adds the `profile_enrichment` lane. What matters with a fetch double is that
identity mapping is stable, that one failing batch cannot lose another, and
that an absent profile stays "unknown" instead of being invented from text.
"""

from __future__ import annotations

import pytest

from app.services.collection.pages import CollectionProviderError
from app.services.collection.profiles import (
    CITY_SOURCE_PROFILE,
    PROFILE_CITY_CONFIDENCE,
    enrich_candidates,
    enrich_posts,
)


def _post(external_id="1_1", author="1", content_type="post", parent=None):
    item = {
        "platform": "vk",
        "external_id": external_id,
        "content_type": content_type,
        "text": "пост",
        "author": {"platform": "vk", "external_id": author},
    }
    if parent:
        item["parent_external_id"] = parent
    return item


def _profile(external_id, city="Киров", platform="vk"):
    return {"platform": platform, "external_id": str(external_id), "city": city}


class _Fetch:
    """A provider double that answers from ``cities`` and records every call."""

    def __init__(self, cities=None):
        self.cities = cities or {}
        self.calls: list[list[str]] = []

    async def __call__(self, ids):
        self.calls.append(list(ids))
        return [
            _profile(external_id, self.cities[str(external_id)])
            for external_id in ids
            if str(external_id) in self.cities
        ]


@pytest.mark.asyncio
async def test_one_profile_answers_every_post_of_its_author():
    posts = [_post("1_1", author="7"), _post("1_2", author="7"), _post("1_3", author="8")]
    fetch = _Fetch({"7": "Киров", "8": "Москва"})

    enrichment = await enrich_posts(posts, fetch)

    assert fetch.calls == [["7", "8"]]
    assert enrichment.requested == 2
    assert enrichment.resolved == 2
    assert enrichment.unknown == 0
    assert posts[0]["author"]["city"] == "Киров"
    assert posts[0]["author"]["city_source"] == CITY_SOURCE_PROFILE
    assert posts[0]["author"]["city_confidence"] == PROFILE_CITY_CONFIDENCE
    assert posts[1]["author"]["city"] == "Киров"
    assert posts[2]["author"]["city"] == "Москва"


@pytest.mark.asyncio
async def test_a_missing_profile_stays_unknown_and_is_never_guessed():
    post = _post(author="9")

    enrichment = await enrich_posts([post], _Fetch({}))

    assert enrichment.requested == 1
    assert enrichment.resolved == 0
    assert enrichment.unknown == 1
    assert "city" not in post["author"]


@pytest.mark.asyncio
async def test_a_profile_without_a_city_is_unknown_too():
    fetch = _Fetch({"9": ""})

    enrichment = await enrich_posts([_post(author="9")], fetch)

    assert enrichment.unknown == 1
    assert enrichment.resolved == 0


@pytest.mark.asyncio
async def test_comments_are_not_enriched():
    post = _post("1_1", author="7")
    comment = _post("1_1:9", author="2", content_type="comment", parent="1_1")
    fetch = _Fetch({"7": "Киров", "2": "Киров"})

    await enrich_posts([post, comment], fetch)

    # A commenter's city says nothing about whether the thread belongs to the task.
    assert fetch.calls == [["7"]]
    assert "city" not in comment["author"]


@pytest.mark.asyncio
async def test_batches_are_bounded_and_one_failure_does_not_lose_the_next():
    class _Flaky:
        def __init__(self):
            self.calls = 0

        async def __call__(self, ids):
            self.calls += 1
            if self.calls == 1:
                raise CollectionProviderError("vk_profiles_failed", "denied")
            return [_profile(identifier, "Киров") for identifier in ids]

    posts = [_post("1_1", author="1"), _post("1_2", author="2")]
    flaky = _Flaky()

    enrichment = await enrich_posts(posts, flaky, batch_size=1)

    assert flaky.calls == 2
    assert enrichment.errors == 1
    assert enrichment.error_codes == ("vk_profiles_failed",)
    assert enrichment.requested == 2
    assert enrichment.resolved == 1
    assert enrichment.unknown == 1
    assert "city" not in posts[0]["author"]
    assert posts[1]["author"]["city"] == "Киров"


@pytest.mark.asyncio
async def test_candidates_keep_their_city_and_source():
    candidates = [
        {"platform": "vk", "kind": "person", "external_id": "5", "city": None},
        {"platform": "vk", "kind": "person", "external_id": "6", "city": None},
    ]

    enrichment = await enrich_candidates(candidates, _Fetch({"5": "Киров"}))

    assert enrichment.requested == 2
    assert enrichment.resolved == 1
    assert enrichment.unknown == 1
    assert candidates[0]["city"] == "Киров"
    assert candidates[0]["city_source"] == CITY_SOURCE_PROFILE
    assert candidates[0]["city_confidence"] == PROFILE_CITY_CONFIDENCE
    assert candidates[1]["city"] is None
