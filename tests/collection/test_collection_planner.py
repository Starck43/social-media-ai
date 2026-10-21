"""Deterministic planner for task-owned collection lanes."""

from __future__ import annotations

import pytest

from app.services.collection.intent import (
    CandidateLimits,
    CityIntent,
    CollectionIntent,
    CommentLimits,
    DiscoveryIntent,
)
from app.services.collection.pages import ProviderCapabilities
from app.services.collection.planner import (
    CollectionPlanError,
    SourceRef,
    dedupe_candidates,
    dedupe_items,
    plan_collection,
    query_source_spec,
)

VK = ProviderCapabilities(
    city_lookup=True,
    people_search=True,
    community_search=True,
    post_search=True,
    wall=True,
    comments=True,
    likes=True,
    reposts=True,
    profiles=True,
)


def _source(source_id=1, source_type="group", external_id="-7", platform="vk", params=None):
    return SourceRef(
        source_id=source_id,
        platform=platform,
        source_type=source_type,
        external_id=external_id,
        name=f"source-{source_id}",
        params=params or {},
    )


def test_source_ref_from_orm_like_object_normalises_enums():
    from types import SimpleNamespace

    from app.types import SourceType

    ref = SourceRef.from_source(
        SimpleNamespace(
            id=4,
            platform=SimpleNamespace(platform_type="vk"),
            source_type=SourceType.GROUP,
            external_id="-7",
            name="wall",
            params={"mode": "auto"},
        )
    )

    assert ref.source_id == 4
    assert ref.platform == "vk"
    assert ref.source_type == "group"
    assert ref.external_id == "-7"


def _plan(intent, sources, *, task_id=2027, capabilities=None):
    return plan_collection(
        task_id=task_id,
        tenant_id=1,
        intent=intent,
        sources=sources,
        capabilities=capabilities or {"vk": VK},
    )


def test_posts_only_plans_known_walls_without_extra_lanes():
    plan = _plan(CollectionIntent(limit=100), [_source(1), _source(2, external_id="-8")])

    assert [lane.lane_type for lane in plan.lanes] == ["wall_posts", "wall_posts"]
    assert [lane.source_id for lane in plan.lanes] == [1, 2]
    assert plan.query_sources == ()
    assert plan.total_limit == 100
    assert plan.warnings == ()


def test_two_walls_and_two_queries_expand_in_dependency_order():
    intent = CollectionIntent(queries=("Древиз", "Кабанов"), limit=100)
    plan = _plan(intent, [_source(1), _source(2, external_id="-8")])

    assert [lane.lane_type for lane in plan.lanes] == ["wall_posts", "wall_posts", "post_search", "post_search"]
    assert [lane.query for lane in plan.lanes if lane.lane_type == "post_search"] == ["Древиз", "Кабанов"]
    assert len(plan.query_sources) == 2
    assert all(spec.owner_task_id == 2027 for spec in plan.query_sources)
    assert len({spec.external_id for spec in plan.query_sources}) == 2
    assert all(lane.lane_id.startswith("search:vk:q:") for lane in plan.lanes if lane.lane_type == "post_search")


def test_comments_likes_and_reposts_depend_on_their_post_lane():
    intent = CollectionIntent(
        content_types=("posts", "comments"),
        engagement_types=("likes", "reposts"),
        limit=100,
        comments=CommentLimits(max_per_post=10, total=50),
        candidates=CandidateLimits(limit=20),
    )
    plan = _plan(intent, [_source()])

    assert [lane.lane_type for lane in plan.lanes] == ["wall_posts", "comments", "likes", "reposts"]
    wall = plan.lanes[0]
    for lane in plan.lanes[1:]:
        assert lane.depends_on == (wall.lane_id,)
    comments = plan.lanes[1]
    assert comments.limit == 50
    assert comments.params["max_per_post"] == 10
    assert plan.lanes[2].limit == 20


def test_discovery_and_city_add_candidate_and_profile_lanes():
    intent = CollectionIntent(
        city=CityIntent(name="Киров", mode="prefer"),
        discovery=DiscoveryIntent(people=True, communities=True, queries=("дизайн интерьера",)),
        candidates=CandidateLimits(limit=30),
    )
    plan = _plan(intent, [_source()])

    by_type = {}
    for lane in plan.lanes:
        by_type.setdefault(lane.lane_type, []).append(lane)
    assert len(by_type["candidate_search"]) == 2
    profile = by_type["profile_enrichment"][0]
    assert profile.depends_on == tuple(lane.lane_id for lane in by_type["candidate_search"])
    assert profile.params["city"] == "Киров"


def test_known_search_source_and_duplicate_query_share_one_lane():
    source = _source(1, source_type="search", external_id="search-drevis", params={"query": "Древиз"})
    plan = _plan(CollectionIntent(queries=("Древиз", "древиз"), limit=100), [source])

    assert [lane.lane_type for lane in plan.lanes] == ["post_search"]
    assert plan.lanes[0].source_id == 1
    assert plan.lanes[0].query == "Древиз"
    assert plan.query_sources == ()


def test_limits_are_kept_per_lane_and_as_a_total():
    intent = CollectionIntent(
        content_types=("posts", "comments"),
        engagement_types=("likes",),
        limit=77,
        comments=CommentLimits(max_per_post=5, total=11),
        candidates=CandidateLimits(limit=13),
    )
    plan = _plan(intent, [_source()])

    assert plan.total_limit == 77
    assert plan.lanes[0].limit == 77
    assert next(lane for lane in plan.lanes if lane.lane_type == "comments").limit == 11
    assert next(lane for lane in plan.lanes if lane.lane_type == "likes").limit == 13


def test_unsupported_telegram_lanes_are_warnings_not_silent_omissions():
    telegram = ProviderCapabilities(wall=True)
    intent = CollectionIntent(
        content_types=("posts", "comments"),
        engagement_types=("likes",),
        queries=("дизайн",),
        comments=CommentLimits(),
    )
    plan = _plan(
        intent,
        [_source(platform="telegram", source_type="channel", external_id="-1001")],
        capabilities={"telegram": telegram},
    )

    assert [lane.lane_type for lane in plan.lanes] == ["wall_posts"]
    assert "post_search_unsupported" in plan.warnings
    assert "comments_unsupported" in plan.warnings
    assert "likes_unsupported" in plan.warnings


def test_discovery_without_queries_or_platform_reports_warnings():
    intent = CollectionIntent(
        discovery=DiscoveryIntent(people=True, communities=True),
        city=CityIntent(name="Киров"),
    )
    plan = _plan(intent, [])

    assert plan.lanes == ()
    assert "discovery_query_missing" in plan.warnings
    assert "discovery_platform_missing" in plan.warnings
    assert "profile_enrichment_without_parent" in plan.warnings


def test_query_lanes_require_a_task():
    with pytest.raises(CollectionPlanError, match="query_lane_requires_task"):
        _plan(CollectionIntent(queries=("Древиз",)), [_source()], task_id=None)


def test_query_source_spec_is_stable_bounded_and_task_owned():
    first = query_source_spec(task_id=2027, platform="vk", query="Италия поездка")
    second = query_source_spec(task_id=2027, platform="vk", query="Италия поездка")
    other = query_source_spec(task_id=2027, platform="vk", query="Кабанов")

    assert first == second
    assert first.external_id != other.external_id
    assert len(first.external_id) <= 100
    assert first.owner_task_id == 2027
    assert first.source_type == "SEARCH"
    assert first.params["query"] == "Италия поездка"


def test_cross_lane_item_identity_keeps_post_once_but_post_and_comment_separate():
    wall = {"platform": "vk", "content_type": "post", "external_id": "-7_11"}
    search = {"platform": "vk", "content_type": "post", "external_id": "-7_11"}
    comment = {"platform": "vk", "content_type": "comment", "external_id": "-7_11"}

    items, removed = dedupe_items([wall, search, comment])

    assert items == [wall, comment]
    assert removed == 1


def test_candidate_identity_is_platform_kind_and_external_id():
    first = {"platform": "vk", "kind": "person", "external_id": "5"}
    duplicate = {"platform": "vk", "kind": "person", "external_id": "5"}
    community = {"platform": "vk", "kind": "community", "external_id": "5"}

    candidates, removed = dedupe_candidates([first, duplicate, community])

    assert candidates == [first, community]
    assert removed == 1
