"""Normalised pages and capability contracts for collection lanes.

A provider returns one page at a time; the planner/executor owns budgets,
dedupe and persistence. An unsupported operation is not an empty page: it
carries an explicit reason so the UI can say "platform cannot do this" instead
of "nothing found".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, TypeVar

T = TypeVar("T")

Item = dict[str, Any]
Candidate = dict[str, Any]
Profile = dict[str, Any]


class CollectionProviderError(RuntimeError):
    """A bounded provider failure that a lane can report without a traceback."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class UnsupportedCollectionOperation(CollectionProviderError):
    """The provider cannot perform this lane at all."""

    def __init__(self, reason: str) -> None:
        super().__init__("unsupported", reason)


@dataclass(frozen=True)
class Page(Generic[T]):
    """One bounded provider page.

    ``limit_reached`` means the lane returned its bound and more may exist.
    ``unsupported_reason`` means the operation was not attempted; it is never
    a substitute for a genuine empty page.
    """

    items: tuple[T, ...] = ()
    next_cursor: str | None = None
    limit_reached: bool = False
    total_available: int | None = None
    warnings: tuple[str, ...] = ()
    unsupported_reason: str | None = None

    @classmethod
    def empty(cls) -> "Page[T]":
        return cls()

    @classmethod
    def unsupported(cls, reason: str, *, warnings: tuple[str, ...] = ()) -> "Page[T]":
        return cls(unsupported_reason=reason, warnings=warnings)


@dataclass(frozen=True)
class ProviderCapabilities:
    """What one platform adapter can actually do.

    A missing capability is reported as a planner warning; it must never be
    silently converted into an empty collection.
    """

    city_lookup: bool = False
    people_search: bool = False
    community_search: bool = False
    post_search: bool = False
    wall: bool = False
    comments: bool = False
    likes: bool = False
    reposts: bool = False
    profiles: bool = False

    def supports(self, lane_type: str, *, kind: str | None = None) -> bool:
        if lane_type == "wall_posts":
            return self.wall
        if lane_type == "post_search":
            return self.post_search
        if lane_type == "candidate_search":
            if kind == "community":
                return self.community_search
            return self.people_search
        if lane_type == "comments":
            return self.comments
        if lane_type == "likes":
            return self.likes
        if lane_type == "reposts":
            return self.reposts
        if lane_type == "profile_enrichment":
            return self.profiles
        return False

    def missing(self, lane_type: str, *, kind: str | None = None) -> str:
        suffix = f"_{kind}" if kind else ""
        return f"{lane_type}{suffix}_unsupported"


@dataclass(frozen=True)
class CityRef:
    """A resolved city; id is the provider's filter value, not our PK."""

    id: int | None
    title: str
    region: str | None = None
    country: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


def encode_offset_cursor(offset: int) -> str:
    if offset < 0:
        raise ValueError("cursor_offset_negative")
    return str(offset)


def decode_offset_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        offset = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor_invalid") from exc
    if offset < 0:
        raise ValueError("cursor_offset_negative")
    return offset
