"""Task-owned collection intent: pure parsing, no database or platform calls.

The task declares *what* to collect, with which filters and how much. A provider
adapter later turns that intent into lanes; this module only normalises the
payload and fails closed on malformed values. Existing tasks have no
``collection`` block and keep the old behaviour: posts only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from app.core.config import settings

CONTENT_POSTS = "posts"
CONTENT_COMMENTS = "comments"
CONTENT_TYPES = (CONTENT_POSTS, CONTENT_COMMENTS)

ENGAGEMENT_LIKES = "likes"
ENGAGEMENT_REPOSTS = "reposts"
ENGAGEMENT_TYPES = (ENGAGEMENT_LIKES, ENGAGEMENT_REPOSTS)

CITY_PREFER = "prefer"
CITY_REQUIRE = "require"
CITY_REVIEW = "review"
CITY_MODES = (CITY_PREFER, CITY_REQUIRE, CITY_REVIEW)

CONFIRM_MANUAL = "manual"
CONFIRM_AUTO = "auto"
CONFIRM_MODES = (CONFIRM_MANUAL, CONFIRM_AUTO)

DEFAULT_LIMIT = 1000
DEFAULT_COMMENT_MAX_PER_POST = 100
DEFAULT_COMMENT_TOTAL = 2_000
MAX_COMMENT_PER_POST = 1_000
MAX_COMMENT_TOTAL = 10_000
DEFAULT_CANDIDATE_LIMIT = 100
MAX_CANDIDATE_LIMIT = 1_000
COLLECTION_VERSION = 1


class CollectionIntentError(ValueError):
    """Malformed collection configuration. Never silently ignored."""


@dataclass(frozen=True)
class CityIntent:
    """A city preference. VK post search cannot filter by it; ranking happens later."""

    name: str
    mode: str = CITY_PREFER


@dataclass(frozen=True)
class DiscoveryIntent:
    """People/community discovery, independent from mention-search queries."""

    people: bool = False
    communities: bool = False
    queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommentLimits:
    """Bounds for the dependent comments lane."""

    max_per_post: int = DEFAULT_COMMENT_MAX_PER_POST
    total: int = DEFAULT_COMMENT_TOTAL


@dataclass(frozen=True)
class CandidateLimits:
    """Bounds and confirmation mode for discovered people/communities."""

    limit: int = DEFAULT_CANDIDATE_LIMIT
    confirm: str = CONFIRM_MANUAL


@dataclass(frozen=True)
class CollectionIntent:
    """Normalised task collection intent.

    An empty ``engagement_types`` means no likes/reposts lane. An empty
    ``queries`` means no mention-search lane; known walls/channels still come
    from the task's sources, not from this payload.
    """

    version: int = COLLECTION_VERSION
    content_types: tuple[str, ...] = (CONTENT_POSTS,)
    engagement_types: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT
    start_date: date | None = None
    end_date: date | None = None
    queries: tuple[str, ...] = ()
    city: CityIntent | None = None
    discovery: DiscoveryIntent = field(default_factory=DiscoveryIntent)
    comments: CommentLimits = field(default_factory=CommentLimits)
    candidates: CandidateLimits = field(default_factory=CandidateLimits)

    @property
    def includes_comments(self) -> bool:
        return CONTENT_COMMENTS in self.content_types

    @property
    def date_window(self) -> tuple[date | None, date | None]:
        return self.start_date, self.end_date


def parse_collection_intent(payload: Mapping[str, Any] | None) -> CollectionIntent:
    """Build the collection intent from a task payload.

    A missing or empty ``collection`` block is valid and preserves the old
    post-only behaviour. Unknown keys are ignored for forward compatibility;
    known keys with an invalid value raise :class:`CollectionIntentError` so a
    bad task save fails visibly instead of collecting the wrong population.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise CollectionIntentError("collection_payload_invalid")

    raw = payload.get("collection")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise CollectionIntentError("collection_block_invalid")

    version = _int(raw.get("version"), "collection_version", default=COLLECTION_VERSION, minimum=1, maximum=1)
    content_types = _choices(
        raw.get("content_types"),
        CONTENT_TYPES,
        "collection_content_types",
        default=(CONTENT_POSTS,),
    )
    engagement_types = _choices(
        raw.get("engagement_types"),
        ENGAGEMENT_TYPES,
        "collection_engagement_types",
        default=(),
        allow_empty=True,
    )
    hard_limit = max(1, int(settings.SOCIAL_COLLECT_HARD_LIMIT))
    default_limit = min(max(1, int(settings.SOCIAL_COLLECT_LIMIT)), hard_limit)
    limit = _int(raw.get("limit"), "collection_limit", default=default_limit, minimum=1, maximum=hard_limit)

    start_date, end_date = _date_window(raw, payload)
    queries = _phrases(raw.get("queries"), "collection_queries")
    city = _city(raw.get("city"))
    discovery = _discovery(raw.get("discovery"))
    comments = _comment_limits(raw.get("comments"))
    candidates = _candidate_limits(raw.get("candidates"))

    return CollectionIntent(
        version=version,
        content_types=content_types,
        engagement_types=engagement_types,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        queries=queries,
        city=city,
        discovery=discovery,
        comments=comments,
        candidates=candidates,
    )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CollectionIntentError(f"{field_name}_invalid")
    return value


def _int(value: Any, field_name: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        result = default
    elif isinstance(value, bool):
        raise CollectionIntentError(f"{field_name}_invalid")
    elif isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise CollectionIntentError(f"{field_name}_invalid")
    if result < minimum or result > maximum:
        raise CollectionIntentError(f"{field_name}_out_of_range")
    return result


def _bool(value: Any, field_name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CollectionIntentError(f"{field_name}_invalid")
    return value


def _choices(
    value: Any,
    allowed: tuple[str, ...],
    field_name: str,
    *,
    default: tuple[str, ...],
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        entries: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        entries = value
    else:
        raise CollectionIntentError(f"{field_name}_invalid")

    result: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise CollectionIntentError(f"{field_name}_invalid")
        normalised = entry.strip().lower()
        if normalised not in allowed:
            raise CollectionIntentError(f"{field_name}_unsupported")
        if normalised not in result:
            result.append(normalised)
    if not result and not allow_empty:
        raise CollectionIntentError(f"{field_name}_empty")
    return tuple(result)


def _phrases(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        entries: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        entries = value
    else:
        raise CollectionIntentError(f"{field_name}_invalid")

    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            raise CollectionIntentError(f"{field_name}_invalid")
        phrase = " ".join(entry.split())
        key = phrase.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(phrase)
    return tuple(result)


def _date(value: Any, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError as exc:
                raise CollectionIntentError(f"{field_name}_invalid") from exc
    raise CollectionIntentError(f"{field_name}_invalid")


def _date_window(raw: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[date | None, date | None]:
    window = raw.get("date_window")
    if window is None:
        legacy = payload.get("cli_dates")
        if legacy is None:
            return None, None
        window = legacy
    data = _mapping(window, "collection_date_window")
    start = _date(data.get("start", data.get("start_date")), "collection_start_date")
    end = _date(data.get("end", data.get("end_date")), "collection_end_date")
    if start is not None and end is not None and start > end:
        raise CollectionIntentError("collection_date_window_reversed")
    return start, end


def _city(value: Any) -> CityIntent | None:
    if value is None:
        return None
    if isinstance(value, str):
        name = " ".join(value.split())
        if not name:
            return None
        return CityIntent(name=name)
    data = _mapping(value, "collection_city")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CollectionIntentError("collection_city_name_invalid")
    mode = data.get("mode") or CITY_PREFER
    if mode not in CITY_MODES:
        raise CollectionIntentError("collection_city_mode_invalid")
    return CityIntent(name=" ".join(name.split()), mode=mode)


def _discovery(value: Any) -> DiscoveryIntent:
    data = _mapping(value, "collection_discovery")
    return DiscoveryIntent(
        people=_bool(data.get("people"), "collection_discovery_people"),
        communities=_bool(data.get("communities"), "collection_discovery_communities"),
        queries=_phrases(data.get("queries"), "collection_discovery_queries"),
    )


def _comment_limits(value: Any) -> CommentLimits:
    data = _mapping(value, "collection_comments")
    return CommentLimits(
        max_per_post=_int(
            data.get("max_per_post"),
            "collection_comments_max_per_post",
            default=DEFAULT_COMMENT_MAX_PER_POST,
            minimum=1,
            maximum=MAX_COMMENT_PER_POST,
        ),
        total=_int(
            data.get("total"),
            "collection_comments_total",
            default=DEFAULT_COMMENT_TOTAL,
            minimum=1,
            maximum=MAX_COMMENT_TOTAL,
        ),
    )


def _candidate_limits(value: Any) -> CandidateLimits:
    data = _mapping(value, "collection_candidates")
    confirm = data.get("confirm") or CONFIRM_MANUAL
    if confirm not in CONFIRM_MODES:
        raise CollectionIntentError("collection_candidates_confirm_invalid")
    return CandidateLimits(
        limit=_int(
            data.get("limit"),
            "collection_candidates_limit",
            default=DEFAULT_CANDIDATE_LIMIT,
            minimum=1,
            maximum=MAX_CANDIDATE_LIMIT,
        ),
        confirm=confirm,
    )
