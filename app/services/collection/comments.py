"""Comment expansion for a comment-enabled source collection.

The planner already knows that a comment-enabled task has one comments lane per
post lane; this module executes that lane against the batch a source just
returned. Two rules come from the collection contract
(``docs/audit/collection-plan-history.md``):

- a post is expanded when it passes the **author** filter, not the text scope.
  A comment may carry the phrase when the post does not, and that is the whole
  reason a task collects comments; applying the text scope here would drop the
  parent before its comments were ever read.
- the per-post and total caps are independent, and stopping at either one is
  reported. A shorter batch with no warning would look like a complete one.

The module holds no database, provider or model access: the caller injects the
``fetch`` callable (a provider adapter's ``fetch_comments``), so every cap and
every failure mode is provable with a double.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from app.services.collection.pages import Page
from app.services.monitoring.selection import SelectionScope, eligible_posts

COMMENT_CONTENT_TYPE = "comment"

LIMIT_PER_POST = "comments_per_post_limit"
LIMIT_TOTAL = "comments_total_limit"
FETCH_FAILED = "comments_fetch_failed"

CommentFetch = Callable[[Any, int, "str | None"], Awaitable[Page[Any]]]


@dataclass(frozen=True)
class CommentBudget:
    """The two independent comment caps of one source run."""

    max_per_post: int
    total: int

    def __post_init__(self) -> None:
        if self.max_per_post < 1:
            raise ValueError("comment_max_per_post_invalid")
        if self.total < 1:
            raise ValueError("comment_total_invalid")


@dataclass
class CommentExpansion:
    """Fetched comments plus the evidence of what the caps actually did."""

    items: list[Any] = field(default_factory=list)
    posts: int = 0
    fetched: int = 0
    limit_reached: bool = False
    warnings: tuple[str, ...] = ()
    errors: int = 0
    error_codes: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        """A bounded, JSON-safe report for a job result or dry-run preview."""
        return {
            "posts": self.posts,
            "fetched": self.fetched,
            "limit_reached": self.limit_reached,
            "warnings": list(self.warnings),
            "errors": self.errors,
            "error_codes": list(self.error_codes),
        }


def comment_parents(posts: Iterable[Any], scope: SelectionScope | None = None) -> list[Any]:
    """Posts whose comments this run is allowed to read.

    Only the author layer is applied; the text scope stays a thread decision
    made later, over the post *and* its comments together.
    """
    return eligible_posts(posts, scope)


async def expand_comments(
    posts: Iterable[Any],
    fetch: CommentFetch,
    *,
    budget: CommentBudget,
    scope: SelectionScope | None = None,
) -> CommentExpansion:
    """Fetch a bounded comment batch for every eligible parent post.

    One post's comments are requested in pages no larger than the remaining
    per-post allowance, and the run stops at the total cap even with parents
    left. A provider failure is counted per post and never raised: comments are
    an addition to the post batch, so losing them must not lose the posts.
    """
    result = CommentExpansion()
    warnings: list[str] = []
    seen_warnings: set[str] = set()
    error_codes: list[str] = []

    def warn(code: str) -> None:
        result.limit_reached = True
        if code not in seen_warnings:
            seen_warnings.add(code)
            warnings.append(code)

    candidates = comment_parents(posts, scope)
    remaining = budget.total

    for post in candidates:
        if remaining <= 0:
            warn(LIMIT_TOTAL)
            break
        result.posts += 1
        per_post = 0
        cursor: str | None = None
        truncated = False
        while per_post < budget.max_per_post and remaining > 0:
            size = min(budget.max_per_post - per_post, remaining)
            try:
                page = await fetch(post, size, cursor)
            except Exception as error:  # noqa: BLE001 — a comment lane never fails the post batch
                result.errors += 1
                error_codes.append(str(getattr(error, "code", FETCH_FAILED)))
                break
            items = list(getattr(page, "items", ()) or ())
            taken = items[:size]
            result.items.extend(taken)
            result.fetched += len(taken)
            per_post += len(taken)
            remaining -= len(taken)
            if len(taken) < size:
                # A short page means the provider has no more for this post.
                break
            cursor = getattr(page, "next_cursor", None)
            truncated = bool(cursor) or bool(getattr(page, "limit_reached", False))
            if cursor is None:
                break
        if truncated:
            warn(LIMIT_TOTAL if remaining <= 0 else LIMIT_PER_POST)

    result.warnings = tuple(warnings)
    result.error_codes = tuple(dict.fromkeys(error_codes))
    return result
