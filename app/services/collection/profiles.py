"""Profile enrichment: a city belongs to a profile, not to a post.

VK post search cannot filter by city and a wall item carries no city at all, so
the only honest source is the author's or community's own profile — self-
declared, stored with `profile` as its `city_source`. This module turns the
provider's `fetch_profiles(ids)` into those attributes for posts and candidates.

It holds no database, client or model: the caller injects the fetch callable, so
batching, identity mapping, the unknown-city outcome and a failing batch are
provable with a double. An identity whose profile is missing or declares no
city is counted as unknown, never guessed from text and never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

CITY_SOURCE_PROFILE = "profile"
PROFILE_CITY_CONFIDENCE = 1.0
DEFAULT_BATCH = 50

ProfileFetch = Callable[[list[str]], Awaitable[list[Mapping[str, Any]]]]


@dataclass
class ProfileEnrichment:
    """What the enrichment lane learned and what it could not.

    Counts are identities, not rows: one author's profile answers every post
    they wrote in the batch, and every candidate carrying that identity.
    """

    requested: int = 0
    resolved: int = 0
    unknown: int = 0
    errors: int = 0
    error_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        """A bounded, JSON-safe report for a job result or dry-run preview."""
        return {
            "city_source": CITY_SOURCE_PROFILE,
            "requested": self.requested,
            "resolved": self.resolved,
            "unknown": self.unknown,
            "errors": self.errors,
            "error_codes": list(self.error_codes),
            "warnings": list(self.warnings),
        }


async def enrich_posts(
    items: Iterable[Any],
    fetch: ProfileFetch,
    *,
    batch_size: int = DEFAULT_BATCH,
) -> ProfileEnrichment:
    """Attach the author-declared city to every post in the batch.

    Comments inherit the thread decision, so only posts are enriched: a
    commenter's city says nothing about whether the thread belongs to the task.
    """
    result = ProfileEnrichment()
    posts = [item for item in items if isinstance(item, dict) and not _is_comment(item)]
    identities = _post_identities(posts)
    profiles = await _load(fetch, identities, result, batch_size)
    for item in posts:
        author = item.get("author")
        if not isinstance(author, dict):
            continue
        identity = _identity(
            author.get("platform") or item.get("platform"), author.get("external_id") or author.get("id")
        )
        profile_city = profiles.get(identity) if identity is not None else None
        if not profile_city:
            continue
        author["city"] = profile_city
        author["city_source"] = CITY_SOURCE_PROFILE
        author["city_confidence"] = PROFILE_CITY_CONFIDENCE
    _finish(result, identities, profiles)
    return result


async def enrich_candidates(
    candidates: Iterable[dict],
    fetch: ProfileFetch,
    *,
    batch_size: int = DEFAULT_BATCH,
) -> ProfileEnrichment:
    """Attach the declared city to discovered people/communities before storage."""
    materialised = [candidate for candidate in candidates if isinstance(candidate, dict)]
    result = ProfileEnrichment()
    identities = _candidate_identities(materialised)
    profiles = await _load(fetch, identities, result, batch_size)
    for candidate in materialised:
        identity = _identity(candidate.get("platform"), candidate.get("external_id"))
        profile_city = profiles.get(identity) if identity is not None else None
        if not profile_city:
            continue
        candidate["city"] = profile_city
        candidate["city_source"] = CITY_SOURCE_PROFILE
        candidate["city_confidence"] = PROFILE_CITY_CONFIDENCE
    _finish(result, identities, profiles)
    return result


def _finish(
    result: ProfileEnrichment,
    identities: Iterable[tuple[str, str]],
    profiles: Mapping[tuple[str, str], str | None],
) -> None:
    unique = list(identities)
    result.error_codes = tuple(dict.fromkeys(result.error_codes))
    result.requested = len(unique)
    result.resolved = sum(1 for identity in unique if profiles.get(identity))
    result.unknown = max(result.requested - result.resolved, 0)


async def _load(
    fetch: ProfileFetch,
    identities: list[tuple[str, str]],
    result: ProfileEnrichment,
    batch_size: int,
) -> dict[tuple[str, str], str | None]:
    """Fetch profiles in bounded batches, keeping one failure local to its batch."""
    profiles: dict[tuple[str, str], str | None] = {}
    if not identities:
        return profiles
    size = max(int(batch_size), 1)
    for start in range(0, len(identities), size):
        batch = identities[start : start + size]
        try:
            rows = await fetch([external for _platform, external in batch])
        except Exception as error:  # noqa: BLE001 — enrichment never fails the batch
            result.errors += 1
            result.error_codes = (*result.error_codes, str(getattr(error, "code", "profiles_fetch_failed")))
            continue
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            identity = _identity(row.get("platform"), row.get("external_id") or row.get("id"))
            if identity is None:
                continue
            profiles[identity] = _profile_city(row)
    return profiles


def _profile_city(row: Mapping[str, Any]) -> str | None:
    value = row.get("city")
    if isinstance(value, Mapping):
        value = value.get("title")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _post_identities(posts: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    identities: dict[tuple[str, str], None] = {}
    for item in posts:
        author = item.get("author")
        if not isinstance(author, Mapping):
            continue
        identity = _identity(
            author.get("platform") or item.get("platform"), author.get("external_id") or author.get("id")
        )
        if identity is not None:
            identities.setdefault(identity, None)
    return list(identities)


def _candidate_identities(candidates: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    identities: dict[tuple[str, str], None] = {}
    for candidate in candidates:
        identity = _identity(candidate.get("platform"), candidate.get("external_id"))
        if identity is not None:
            identities.setdefault(identity, None)
    return list(identities)


def _identity(platform: Any, external_id: Any) -> tuple[str, str] | None:
    name = str(platform or "").strip().lower()
    value = str(external_id or "").strip()
    if not name or not value:
        return None
    return name, value


def _is_comment(item: Mapping[str, Any]) -> bool:
    return str(item.get("content_type") or "").lower() == "comment"


__all__ = [
    "CITY_SOURCE_PROFILE",
    "PROFILE_CITY_CONFIDENCE",
    "ProfileEnrichment",
    "enrich_candidates",
    "enrich_posts",
]
