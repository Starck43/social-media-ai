"""Deterministic task intent -> collection lanes.

The planner is pure: it reads a parsed :class:`CollectionIntent`, known source
refs and provider capabilities, and returns ordered lanes plus query-source
specs. It never touches the database, creates rows, advances cursors or calls a
provider. The executor (a later stage) owns those effects.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.collection.intent import CONTENT_COMMENTS, CollectionIntent
from app.services.collection.pages import ProviderCapabilities

SEARCH_SOURCE_TYPE = "search"
WALL_SOURCE_TYPES = frozenset({"group", "channel", "page", "user", "chat"})
POST_LANE_TYPES = frozenset({"wall_posts", "post_search"})


class CollectionPlanError(ValueError):
    """The intent cannot be turned into a valid plan."""


@dataclass(frozen=True)
class SourceRef:
    """The source fields the planner needs, without loading an ORM object."""

    source_id: int
    platform: str
    source_type: str
    external_id: str
    name: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_source(cls, source: Any) -> "SourceRef":
        platform = getattr(source, "platform", None)
        platform_name = getattr(platform, "platform_type", platform)
        return cls(
            source_id=int(source.id),
            platform=_enum_value(platform_name).lower(),
            source_type=_enum_value(source.source_type).lower(),
            external_id=str(source.external_id or ""),
            name=str(getattr(source, "name", "") or ""),
            params=dict(getattr(source, "params", None) or {}),
        )


@dataclass(frozen=True)
class CollectionLane:
    lane_id: str
    lane_type: str
    platform: str
    output_type: str  # items | candidates | profiles
    limit: int | None = None
    depends_on: tuple[str, ...] = ()
    source_id: int | None = None
    query: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuerySourceSpec:
    """A task-scoped Source to create for one query lane, outside dry-run."""

    platform: str
    external_id: str
    name: str
    query: str
    owner_task_id: int
    source_type: str = "SEARCH"
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionPlan:
    task_id: int | None
    tenant_id: int
    lanes: tuple[CollectionLane, ...]
    query_sources: tuple[QuerySourceSpec, ...] = ()
    total_limit: int = 0
    warnings: tuple[str, ...] = ()


def plan_collection(
    *,
    task_id: int | None,
    tenant_id: int,
    intent: CollectionIntent,
    sources: Sequence[SourceRef],
    capabilities: Mapping[str, ProviderCapabilities],
) -> CollectionPlan:
    """Expand one task intent into ordered typed lanes.

    Raises :class:`CollectionPlanError` for a query lane without a task id or a
    broken dependency graph. Unsupported platform operations are warnings, not
    silent omissions.
    """
    if intent.queries and task_id is None:
        raise CollectionPlanError("query_lane_requires_task")

    warnings: list[str] = []
    seen_warnings: set[str] = set()

    def warn(reason: str) -> None:
        if reason not in seen_warnings:
            seen_warnings.add(reason)
            warnings.append(reason)

    lanes: list[CollectionLane] = []
    query_sources: list[QuerySourceSpec] = []
    seen_query_sources: set[str] = set()
    seen_queries: set[str] = set()

    ordered_sources = sorted(sources, key=lambda item: (item.platform, item.source_id))
    platforms = tuple(dict.fromkeys(source.platform for source in ordered_sources))

    for source in ordered_sources:
        caps = capabilities.get(source.platform)
        if caps is None:
            warn(f"platform_unsupported:{source.platform}")
            continue
        source_type = (source.source_type or "").lower()
        if source_type == SEARCH_SOURCE_TYPE:
            query = _query_value(source.params.get("query") or source.external_id)
            if not query:
                warn("search_source_query_missing")
                continue
            seen_queries.add(_query_key(query))
            if not caps.post_search:
                warn(caps.missing("post_search"))
                continue
            lanes.append(
                CollectionLane(
                    lane_id=f"search:{source.platform}:source:{source.source_id}",
                    lane_type="post_search",
                    platform=source.platform,
                    output_type="items",
                    limit=intent.limit,
                    source_id=source.source_id,
                    query=query,
                )
            )
            continue
        if source_type in WALL_SOURCE_TYPES:
            if not caps.wall:
                warn(caps.missing("wall_posts"))
                continue
            lanes.append(
                CollectionLane(
                    lane_id=f"wall:{source.platform}:{source.source_id}",
                    lane_type="wall_posts",
                    platform=source.platform,
                    output_type="items",
                    limit=intent.limit,
                    source_id=source.source_id,
                )
            )
            continue
        warn(f"source_type_unsupported:{source_type or 'unknown'}")

    # A global phrase search does not need a known wall; VK is the only stage-B
    # provider with post_search, so a taskless-source query plan defaults there.
    search_platforms = platforms or (("vk",) if intent.queries else ())
    for platform in search_platforms:
        caps = capabilities.get(platform)
        if caps is None:
            warn(f"platform_unsupported:{platform}")
            continue
        for query in intent.queries:
            key = _query_key(query)
            if key in seen_queries:
                continue
            seen_queries.add(key)
            if not caps.post_search:
                warn(caps.missing("post_search"))
                continue
            spec = query_source_spec(task_id=task_id, platform=platform, query=query)
            if spec.external_id not in seen_query_sources:
                seen_query_sources.add(spec.external_id)
                query_sources.append(spec)
            lanes.append(
                CollectionLane(
                    lane_id=f"search:{platform}:q:{_digest(query)}",
                    lane_type="post_search",
                    platform=platform,
                    output_type="items",
                    limit=intent.limit,
                    query=query,
                    params={"query_source": spec.external_id},
                )
            )

    consumer_platforms = platforms or (("vk",) if intent.queries else ())
    if intent.discovery.people or intent.discovery.communities:
        if not intent.discovery.queries:
            warn("discovery_query_missing")
        if not consumer_platforms:
            warn("discovery_platform_missing")
        for platform in consumer_platforms:
            caps = capabilities.get(platform)
            if caps is None:
                warn(f"platform_unsupported:{platform}")
                continue
            for query in intent.discovery.queries:
                if intent.discovery.people:
                    if caps.people_search:
                        lanes.append(_candidate_lane(platform, "person", query, intent))
                    else:
                        warn(caps.missing("candidate_search", kind="person"))
                if intent.discovery.communities:
                    if caps.community_search:
                        lanes.append(_candidate_lane(platform, "community", query, intent))
                    else:
                        warn(caps.missing("candidate_search", kind="community"))

    post_lanes = [lane for lane in lanes if lane.lane_type in POST_LANE_TYPES]
    comments_lanes: list[CollectionLane] = []
    if intent.includes_comments:
        if not post_lanes:
            warn("comments_without_post_lane")
        for parent in post_lanes:
            caps = capabilities.get(parent.platform)
            if caps is None:
                continue
            if not caps.comments:
                warn(caps.missing("comments"))
                continue
            comments_lanes.append(
                CollectionLane(
                    lane_id=f"comments:{parent.lane_id}",
                    lane_type="comments",
                    platform=parent.platform,
                    output_type="items",
                    limit=intent.comments.total,
                    depends_on=(parent.lane_id,),
                    params={"max_per_post": intent.comments.max_per_post, "parent_lane": parent.lane_id},
                )
            )
    lanes.extend(comments_lanes)

    if intent.engagement_types:
        if not post_lanes:
            warn("engagement_without_post_lane")
        for engagement in intent.engagement_types:
            for parent in post_lanes:
                caps = capabilities.get(parent.platform)
                if caps is None:
                    continue
                if not caps.supports(engagement):
                    warn(caps.missing(engagement))
                    continue
                lanes.append(
                    CollectionLane(
                        lane_id=f"{engagement}:{parent.lane_id}",
                        lane_type=engagement,
                        platform=parent.platform,
                        output_type="candidates",
                        limit=intent.candidates.limit,
                        depends_on=(parent.lane_id,),
                        params={"parent_lane": parent.lane_id},
                    )
                )

    if intent.city is not None or intent.discovery.people or intent.discovery.communities:
        candidate_lanes = [lane for lane in lanes if lane.lane_type == "candidate_search"]
        profile_parents: dict[str, list[str]] = {}
        for lane in candidate_lanes or post_lanes:
            profile_parents.setdefault(lane.platform, []).append(lane.lane_id)
        if not profile_parents:
            warn("profile_enrichment_without_parent")
        for platform, parents in profile_parents.items():
            caps = capabilities.get(platform)
            if caps is None:
                continue
            if not caps.profiles:
                warn(caps.missing("profile_enrichment"))
                continue
            lanes.append(
                CollectionLane(
                    lane_id=f"profiles:{platform}",
                    lane_type="profile_enrichment",
                    platform=platform,
                    output_type="profiles",
                    limit=intent.candidates.limit,
                    depends_on=tuple(parents),
                    params={"city": intent.city.name if intent.city is not None else None},
                )
            )

    ordered = _topological(lanes)
    return CollectionPlan(
        task_id=task_id,
        tenant_id=tenant_id,
        lanes=tuple(ordered),
        query_sources=tuple(query_sources),
        total_limit=intent.limit,
        warnings=tuple(warnings),
    )


def dedupe_items(items: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """Cross-lane item identity before staging.

    A post returned by both a wall lane and a search lane is one post. A
    comment with the same platform id is a different object, so `content_type`
    is part of the key. The first occurrence wins; the count is the number of
    duplicates removed.
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[Mapping[str, Any]] = []
    removed = 0
    for item in items:
        key = item_identity(item)
        if key is None:
            result.append(item)
            continue
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        result.append(item)
    return result, removed


def item_identity(item: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """Stable post/comment identity shared by planner and runtime deduplication.

    Items without a provider id or content hash cannot be safely compared and
    therefore never suppress another item.
    """
    identity = item.get("external_id") or item.get("content_hash") or item.get("id")
    if not identity:
        return None
    return (
        str(item.get("platform") or ""),
        str(item.get("content_type") or "post"),
        str(identity),
    )


def dedupe_candidates(candidates: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """Cross-lane candidate identity: platform + kind + external id."""
    seen: set[tuple[str, str, str]] = set()
    result: list[Mapping[str, Any]] = []
    removed = 0
    for candidate in candidates:
        key = (
            str(candidate.get("platform") or ""),
            str(candidate.get("kind") or ""),
            str(candidate.get("external_id") or ""),
        )
        if not key[2]:
            result.append(candidate)
            continue
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        result.append(candidate)
    return result, removed


def query_source_spec(*, task_id: int, platform: str, query: str) -> QuerySourceSpec:
    """Stable task-scoped Source spec for one query.

    `sources` has a unique (tenant, platform, external_id) key that does not
    include `owner_task_id`, so the generated id must be unique per task and
    query. The human name stays readable; the id is bounded to 100 chars.
    """
    digest = _digest(query)
    slug = _slug(query)
    external_id = f"task-{task_id}-{platform}-{slug}-{digest}"[:100]
    return QuerySourceSpec(
        platform=platform,
        external_id=external_id,
        name=f"Поиск: {query}",
        query=query,
        owner_task_id=task_id,
        params={"query": query, "mode": "auto"},
    )


def _candidate_lane(platform: str, kind: str, query: str, intent: CollectionIntent) -> CollectionLane:
    return CollectionLane(
        lane_id=f"candidate:{platform}:{kind}:{_digest(query)}",
        lane_type="candidate_search",
        platform=platform,
        output_type="candidates",
        limit=intent.candidates.limit,
        query=query,
        params={
            "query": query,
            "kind": kind,
            "city": intent.city.name if intent.city is not None else None,
            "city_mode": intent.city.mode if intent.city is not None else None,
        },
    )


def _topological(lanes: Sequence[CollectionLane]) -> list[CollectionLane]:
    by_id: dict[str, CollectionLane] = {}
    for lane in lanes:
        if lane.lane_id in by_id:
            raise CollectionPlanError(f"duplicate_lane:{lane.lane_id}")
        by_id[lane.lane_id] = lane

    permanent: set[str] = set()
    temporary: set[str] = set()
    ordered: list[CollectionLane] = []

    def visit(lane_id: str) -> None:
        if lane_id in permanent:
            return
        if lane_id in temporary:
            raise CollectionPlanError(f"lane_dependency_cycle:{lane_id}")
        lane = by_id.get(lane_id)
        if lane is None:
            raise CollectionPlanError(f"lane_dependency_missing:{lane_id}")
        temporary.add(lane_id)
        for dependency in lane.depends_on:
            visit(dependency)
        temporary.remove(lane_id)
        permanent.add(lane_id)
        ordered.append(lane)

    for lane in lanes:
        visit(lane.lane_id)
    return ordered


def _enum_value(value: Any) -> str:
    raw = getattr(value, "db_value", None)
    if raw is None:
        raw = getattr(value, "value", value)
    return str(raw or "")


def _query_value(value: Any) -> str:
    return " ".join(str(value or "").split())


def _query_key(query: str) -> str:
    return _query_value(query).casefold()


def _digest(value: str) -> str:
    return hashlib.sha1(_query_key(value).encode("utf-8")).hexdigest()[:10]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _query_key(value)).strip("-")
    return slug[:40] or "query"
