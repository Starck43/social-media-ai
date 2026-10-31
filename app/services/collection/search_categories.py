"""VK search-result categories: an explicit, evidence-gated registry (P8-T33).

A *category* is one typed VK search-result family (people, video, ...), not a
task-population lane: discovery lanes in ``planner.py``/``population.py`` feed
``collection_candidates`` and stay untouched. This module only records which
categories exist, which official API method backs each one, and what evidence
gates its availability, then dispatches the executable ones to the existing
adapter operations.

Availability is honest by construction: a category with only mocked contract
verification is ``mocked`` — implemented, but not enabled for users. Live
enablement requires the method/profile evidence owned by Q-5; no category here
may become ``enabled`` from code alone. Deferred or unsupported categories keep
an explicit bounded reason so closing P8-T33 cannot silently drop them.

Every surface (web, API, CLI, admin, agent) must render categories through
:func:`category_catalog` so all five share one contract. ``all`` aggregates
only executable categories and preserves each result kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services.collection.pages import Candidate, CityRef, Page

CATEGORY_PEOPLE = "people"
CATEGORY_VIDEO = "video"
CATEGORY_APPS = "apps"
CATEGORY_MARKET = "market"
CATEGORY_MUSIC = "music"
CATEGORY_CLIPS = "clips"
CATEGORY_CHANNELS = "channels"

STATUS_ENABLED = "enabled"
STATUS_MOCKED = "mocked"
STATUS_DEFERRED = "deferred"
STATUS_UNSUPPORTED = "unsupported"
CATEGORY_STATUSES = (STATUS_ENABLED, STATUS_MOCKED, STATUS_DEFERRED, STATUS_UNSUPPORTED)

# Only these statuses may execute. ``mocked`` is executable so the mocked
# contract can be verified; it is still not a live availability claim.
EXECUTABLE_STATUSES = (STATUS_ENABLED, STATUS_MOCKED)

ALL_CATEGORIES = "all"

# Bounded, operator-safe reason codes. Surfaces render these verbatim; none of
# them implies a live failure or a live capability.
REASON_PROFILE_UNVERIFIED = "vk_method_profile_unverified"
REASON_NOT_SELECTED = "category_not_selected_for_this_slice"
REASON_METHOD_UNVERIFIED = "official_method_unverified"
REASON_PERMISSION_EVIDENCE = "separate_permission_evidence_required"
REASON_NO_PUBLIC_METHOD = "no_promised_public_search_method"
REASON_PARTIAL_VIA_COMMUNITIES = "partially_represented_by_communities"

Q5_EVIDENCE_REF = "docs/tasks/q-5.md"
T33_CARD_REF = "docs/tasks/p8-t33.md"


class SearchCategoryError(ValueError):
    """A bounded category failure; never a raw traceback at a surface."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SearchCategory:
    """One VK search-result category and the evidence that gates it."""

    category_id: str
    platform: str
    label: str
    result_kind: str
    status: str
    reason: str
    official_method: str | None = None
    adapter_operation: str | None = None
    evidence_ref: str | None = None

    @property
    def executable(self) -> bool:
        return self.status in EXECUTABLE_STATUSES and self.adapter_operation is not None

    def as_dict(self) -> dict[str, Any]:
        """The JSON-safe catalog entry shared by every surface."""
        return {
            "id": self.category_id,
            "platform": self.platform,
            "label": self.label,
            "result_kind": self.result_kind,
            "status": self.status,
            "reason": self.reason,
            "official_method": self.official_method,
            "executable": self.executable,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class CategorySelection:
    """The outcome of resolving a requested category set.

    ``selected`` holds only executable categories. ``unavailable`` carries one
    JSON-safe entry per requested category that cannot run, with its bounded
    reason, so a surface can say *why* instead of showing an empty result.
    """

    selected: tuple[SearchCategory, ...]
    unavailable: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected": [category.as_dict() for category in self.selected],
            "unavailable": [dict(entry) for entry in self.unavailable],
        }


# The P8-T33 registry. Only `people` is selected for this slice; it executes
# through the existing adapter `search_people` operation over the official
# `users.search` method and stays `mocked` while Q-5 owns live profile evidence.
# Every other requested category keeps an explicit status and reason.
VK_SEARCH_CATEGORIES: tuple[SearchCategory, ...] = (
    SearchCategory(
        category_id=CATEGORY_PEOPLE,
        platform="vk",
        label="Люди",
        result_kind="person",
        status=STATUS_MOCKED,
        reason=REASON_PROFILE_UNVERIFIED,
        official_method="users.search",
        adapter_operation="search_people",
        evidence_ref=Q5_EVIDENCE_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_VIDEO,
        platform="vk",
        label="Видео",
        result_kind="video",
        status=STATUS_DEFERRED,
        reason=REASON_NOT_SELECTED,
        official_method="video.search",
        evidence_ref=T33_CARD_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_APPS,
        platform="vk",
        label="Приложения и игры",
        result_kind="app",
        status=STATUS_DEFERRED,
        reason=REASON_METHOD_UNVERIFIED,
        evidence_ref=T33_CARD_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_MARKET,
        platform="vk",
        label="Товары сообщества",
        result_kind="market_item",
        status=STATUS_DEFERRED,
        reason=REASON_NOT_SELECTED,
        official_method="market.search",
        evidence_ref=T33_CARD_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_MUSIC,
        platform="vk",
        label="Музыка",
        result_kind="audio",
        status=STATUS_DEFERRED,
        reason=REASON_PERMISSION_EVIDENCE,
        evidence_ref=T33_CARD_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_CLIPS,
        platform="vk",
        label="Клипы",
        result_kind="clip",
        status=STATUS_UNSUPPORTED,
        reason=REASON_NO_PUBLIC_METHOD,
        evidence_ref=T33_CARD_REF,
    ),
    SearchCategory(
        category_id=CATEGORY_CHANNELS,
        platform="vk",
        label="Каналы",
        result_kind="channel",
        status=STATUS_UNSUPPORTED,
        reason=REASON_PARTIAL_VIA_COMMUNITIES,
        evidence_ref=T33_CARD_REF,
    ),
)


def vk_search_categories() -> tuple[SearchCategory, ...]:
    """The declared VK categories, in registry order."""
    return VK_SEARCH_CATEGORIES


def get_category(category_id: str, *, platform: str = "vk") -> SearchCategory:
    """One declared category; an unknown id fails closed with a bounded code."""
    key = str(category_id or "").strip().lower()
    for category in VK_SEARCH_CATEGORIES:
        if category.platform == platform and category.category_id == key:
            return category
    raise SearchCategoryError("search_category_unknown", f"unknown {platform} search category: {category_id!r}")


def category_catalog(*, platform: str = "vk") -> list[dict[str, Any]]:
    """The shared read model for web, API, CLI, admin and agent surfaces."""
    return [category.as_dict() for category in VK_SEARCH_CATEGORIES if category.platform == platform]


def resolve_categories(requested: Sequence[str] | str | None, *, platform: str = "vk") -> CategorySelection:
    """Resolve a requested category set into executable and unavailable parts.

    ``None`` or ``"all"`` aggregates only executable categories; a deferred or
    unsupported category is never silently included. An explicit list preserves
    order, deduplicates, reports each non-executable category with its reason,
    and fails closed on an unknown id.
    """
    if requested is None or requested == ALL_CATEGORIES:
        selected = tuple(
            category for category in VK_SEARCH_CATEGORIES if category.platform == platform and category.executable
        )
        return CategorySelection(selected=selected, unavailable=())
    if isinstance(requested, str):
        entries: Sequence[str] = [requested]
    else:
        entries = requested

    selected: list[SearchCategory] = []
    unavailable: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        category = get_category(entry, platform=platform)
        if category.category_id in seen:
            continue
        seen.add(category.category_id)
        if category.executable:
            selected.append(category)
        else:
            unavailable.append(
                {
                    "id": category.category_id,
                    "status": category.status,
                    "reason": category.reason,
                    "evidence_ref": category.evidence_ref,
                }
            )
    return CategorySelection(selected=tuple(selected), unavailable=tuple(unavailable))


async def search_category(
    provider: Any,
    category: SearchCategory | str,
    query: str,
    *,
    limit: int,
    cursor: str | None = None,
    city: CityRef | None = None,
) -> Page[Candidate]:
    """Execute one category through the provider's existing typed operation.

    The provider (today :class:`VKCollectionAdapter`) owns token rules,
    normalisation, paging bounds and error translation; this wrapper only
    enforces the category gate and stamps provenance. A non-executable
    category raises a bounded error instead of returning an empty page, and a
    provider without the mapped operation fails visibly.
    """
    resolved = category if isinstance(category, SearchCategory) else get_category(category)
    if not resolved.executable:
        raise SearchCategoryError(
            "search_category_unavailable",
            f"{resolved.platform} search category {resolved.category_id!r} is {resolved.status}: {resolved.reason}",
        )
    operation = getattr(provider, resolved.adapter_operation or "", None)
    if operation is None:
        raise SearchCategoryError(
            "search_category_operation_missing",
            f"provider {type(provider).__name__} has no {resolved.adapter_operation} operation",
        )
    if resolved.category_id == CATEGORY_PEOPLE:
        page = await operation(query, city, limit, cursor)
    else:  # pragma: no cover - no second executable category exists yet
        raise SearchCategoryError(
            "search_category_unavailable",
            f"{resolved.platform} search category {resolved.category_id!r} has no dispatch binding",
        )
    return _with_provenance(page, resolved)


def _with_provenance(page: Page[Candidate], category: SearchCategory) -> Page[Candidate]:
    """Stamp each result with its category and official method, preserving kinds."""
    items: list[Candidate] = []
    for item in page.items:
        if not isinstance(item, Mapping):
            items.append(item)
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        items.append(
            {
                **dict(item),
                "kind": item.get("kind") or category.result_kind,
                "evidence": {
                    **dict(evidence),
                    "search_category": category.category_id,
                    "official_method": category.official_method,
                },
            }
        )
    return Page(
        items=tuple(items),
        next_cursor=page.next_cursor,
        limit_reached=page.limit_reached,
        total_available=page.total_available,
        warnings=page.warnings,
        unsupported_reason=page.unsupported_reason,
    )
