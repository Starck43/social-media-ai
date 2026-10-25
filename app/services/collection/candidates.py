"""Engagement candidates: likes and reposts produce people, not text.

An engagement lane answers "who reacted", never "what was said": its result is
a candidate identity awaiting confirmation and it must not become a
`collected_items` row. The planner already emits one lane per engagement type;
this module executes those lanes over the eligible post batch with a single
shared candidate budget, and keeps identity stable so two lanes — or two runs —
cannot produce the same person twice.

Like the comments lane it holds no database, provider or model access: the
caller injects paired `(lane, fetch)` callables, so the cap, the cross-lane
dedupe and the per-post failure policy are provable with doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping

from app.services.collection.pages import Candidate, Page
from app.services.monitoring.selection import SelectionScope, eligible_posts

CANDIDATE_KIND_PERSON = "person"

LIMIT_REACHED = "candidates_limit_reached"
FETCH_FAILED = "candidates_fetch_failed"

CandidateFetch = Callable[[Any, int, "str | None"], Awaitable[Page[Candidate]]]


@dataclass(frozen=True)
class CandidateBudget:
    """The task-wide candidate cap, shared by every engagement lane."""

    limit: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("candidate_limit_invalid")


@dataclass
class CandidateExpansion:
    """Discovered identities plus the evidence of what the caps hid."""

    items: list[Mapping[str, Any]] = field(default_factory=list)
    by_lane: dict[str, int] = field(default_factory=dict)
    fetched: int = 0
    duplicates: int = 0
    invalid: int = 0
    limit_reached: bool = False
    warnings: tuple[str, ...] = ()
    errors: int = 0
    error_codes: tuple[str, ...] = ()

    def summary(
        self, *, stored: int | None = None, confirm: str | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """A bounded, JSON-safe report for a job result or dry-run preview."""
        report: dict[str, Any] = {
            "fetched": self.fetched,
            "by_lane": dict(self.by_lane),
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "limit_reached": self.limit_reached,
            "warnings": list(self.warnings),
            "errors": self.errors,
            "error_codes": list(self.error_codes),
        }
        if stored is not None:
            report["stored"] = stored
        if confirm is not None:
            report["confirm"] = confirm
        if dry_run:
            report["dry_run"] = True
        return report


async def expand_candidates(
    posts: Iterable[Any],
    lanes: Iterable[tuple[str, CandidateFetch]],
    *,
    budget: CandidateBudget,
    scope: SelectionScope | None = None,
) -> CandidateExpansion:
    """Read every engagement lane for the eligible posts, capped once.

    The budget is shared by all lanes, so the cap is a property of the task and
    not of an arbitrary lane order. A candidate already seen in this run keeps
    its first evidence; a provider failure is counted per post and never stops
    the remaining lanes.
    """
    result = CandidateExpansion()
    seen: dict[tuple[str, str, str], int] = {}
    remaining = budget.limit
    parents = eligible_posts(posts, scope)
    error_codes: list[str] = []

    for lane, fetch in lanes:
        result.by_lane.setdefault(lane, 0)
        for post in parents:
            if remaining <= 0:
                _warn(result, LIMIT_REACHED)
                break
            cursor: str | None = None
            while remaining > 0:
                try:
                    page = await fetch(post, remaining, cursor)
                except Exception as error:  # noqa: BLE001 — one lane never fails the collection
                    result.errors += 1
                    error_codes.append(str(getattr(error, "code", FETCH_FAILED)))
                    break
                items = [item for item in (getattr(page, "items", ()) or ()) if isinstance(item, Mapping)]
                for item in items:
                    identity = _identity(item)
                    if identity is None:
                        result.invalid += 1
                        continue
                    result.fetched += 1
                    result.by_lane[lane] = result.by_lane.get(lane, 0) + 1
                    if identity in seen:
                        result.duplicates += 1
                        continue
                    if remaining <= 0:
                        _warn(result, LIMIT_REACHED)
                        break
                    seen[identity] = len(result.items)
                    result.items.append(_candidate(item, lane, post))
                    remaining -= 1
                if remaining <= 0 and getattr(page, "next_cursor", None) is not None:
                    _warn(result, LIMIT_REACHED)
                cursor = getattr(page, "next_cursor", None)
                if cursor is None or not items:
                    break
        if remaining <= 0:
            break

    result.error_codes = tuple(dict.fromkeys(error_codes))
    return result


def _warn(result: CandidateExpansion, code: str) -> None:
    result.limit_reached = True
    if code not in result.warnings:
        result.warnings = (*result.warnings, code)


def _identity(item: Mapping[str, Any]) -> tuple[str, str, str] | None:
    platform = str(item.get("platform") or "").strip().lower()
    kind = str(item.get("kind") or CANDIDATE_KIND_PERSON).strip().lower()
    external_id = str(item.get("external_id") or "").strip()
    if not platform or not kind or not external_id:
        return None
    return platform, kind, external_id


def _candidate(item: Mapping[str, Any], lane: str, post: Any) -> Mapping[str, Any]:
    """Keep the provider's fields, add the evidence of why we looked."""
    evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
    post_external_id = post.get("external_id") if isinstance(post, Mapping) else None
    return {
        "platform": str(item.get("platform") or "").strip().lower(),
        "kind": str(item.get("kind") or CANDIDATE_KIND_PERSON).strip().lower(),
        "external_id": str(item.get("external_id") or "").strip(),
        "name": item.get("name"),
        "url": item.get("url"),
        "city": item.get("city"),
        "metrics": item.get("metrics") if isinstance(item.get("metrics"), Mapping) else None,
        "evidence": {
            **dict(evidence),
            "lane": lane,
            "post_external_id": post_external_id,
        },
    }
