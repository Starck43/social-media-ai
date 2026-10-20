"""Task collection context: one parsed intent plus provider capabilities.

The intent says *what* a task wants; the provider says what it can actually do.
A context is built per source because two sources in one task may live on
different platforms. It performs no database, provider or model calls: a
warning is evidence for a lane that cannot run, not a silent empty result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.collection.adapters.telegram import (
    TELEGRAM_CAPABILITIES,
    TelegramCollectionAdapter,
)
from app.services.collection.adapters.vk import VK_CAPABILITIES, VKCollectionAdapter
from app.services.collection.intent import (
    CONTENT_POSTS,
    CollectionIntent,
    parse_collection_intent,
)
from app.services.collection.pages import ProviderCapabilities

_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "vk": VK_CAPABILITIES,
    "telegram": TELEGRAM_CAPABILITIES,
}


@dataclass(frozen=True)
class CollectionContext:
    """The collection answer for one source in one task run.

    ``intent`` is the requested configuration, not a silently narrowed one.
    ``warnings`` names every requested operation the provider cannot perform;
    D2-D4 must skip those operations explicitly and report the warning.
    """

    platform: str
    source_id: int | None
    intent: CollectionIntent
    capabilities: ProviderCapabilities
    warnings: tuple[str, ...] = ()

    @property
    def includes_comments(self) -> bool:
        return self.intent.includes_comments

    @property
    def engagement_types(self) -> tuple[str, ...]:
        return self.intent.engagement_types

    def summary(self) -> dict[str, Any]:
        """A bounded, JSON-safe summary for a job result or dry-run preview."""
        city = self.intent.city
        return {
            "platform": self.platform,
            "content_types": list(self.intent.content_types),
            "engagement_types": list(self.intent.engagement_types),
            "limit": self.intent.limit,
            "comments": {
                "enabled": self.includes_comments,
                "max_per_post": self.intent.comments.max_per_post,
                "total": self.intent.comments.total,
            },
            "candidates": {
                "limit": self.intent.candidates.limit,
                "confirm": self.intent.candidates.confirm,
            },
            "city": {"name": city.name, "mode": city.mode} if city is not None else None,
            "warnings": list(self.warnings),
        }

    def adapter(self, client: Any) -> Any:
        """Build the provider adapter for this context, if one is supported."""
        if self.platform == "vk":
            return VKCollectionAdapter(client)
        if self.platform == "telegram":
            return TelegramCollectionAdapter(client)
        return None


def build_collection_context(
    payload: Mapping[str, Any] | None,
    *,
    platform: str,
    source_id: int | None = None,
) -> CollectionContext:
    """Parse the task payload and attach capabilities for one source.

    Raises :class:`app.services.collection.intent.CollectionIntentError` when a
    known value is malformed. A caller must report that error rather than
    collecting a guessed population.
    """
    name = normalise_platform(platform)
    intent = parse_collection_intent(payload)
    capabilities = _CAPABILITIES.get(name, ProviderCapabilities())
    return CollectionContext(
        platform=name,
        source_id=source_id,
        intent=intent,
        capabilities=capabilities,
        warnings=_warnings(intent, capabilities),
    )


def platform_name(source: Any) -> str:
    """Provider name for a Source, accepting ORM enums and test doubles."""
    platform = getattr(source, "platform", None)
    value = getattr(platform, "platform_type", platform)
    if value is None:
        value = getattr(platform, "name", "")
    if hasattr(value, "db_value"):
        value = value.db_value
    elif hasattr(value, "value"):
        value = value.value
    return normalise_platform(str(value or ""))


def normalise_platform(value: str) -> str:
    return str(value or "").strip().lower()


def _warnings(intent: CollectionIntent, capabilities: ProviderCapabilities) -> tuple[str, ...]:
    warnings: list[str] = []
    seen: set[str] = set()

    def warn(code: str) -> None:
        if code not in seen:
            seen.add(code)
            warnings.append(code)

    if intent.includes_comments:
        if CONTENT_POSTS not in intent.content_types:
            warn("comments_require_posts")
        if not capabilities.comments:
            warn("comments_unsupported")

    for engagement in intent.engagement_types:
        if CONTENT_POSTS not in intent.content_types:
            warn("engagement_require_posts")
        if not capabilities.supports(engagement):
            warn(f"{engagement}_unsupported")

    if intent.city is not None:
        if not capabilities.city_lookup:
            warn("city_lookup_unsupported")
        if not capabilities.profiles:
            warn("profiles_unsupported")

    if intent.discovery.people and not capabilities.people_search:
        warn("people_search_unsupported")
    if intent.discovery.communities and not capabilities.community_search:
        warn("community_search_unsupported")

    return tuple(warnings)
