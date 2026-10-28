"""Immutable reporting windows and canonical presentation buckets (no ORM reads)."""

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Optional

from app.types import PeriodType

REMOVED_GROUPING_AXES = frozenset({"sentiment", "content_type"})


def sentiment_bucket(score: Any) -> str | None:
    """Canonical buckets: >0.6 positive, <0.4 negative, boundaries neutral.

    Missing/non-numeric/non-finite values have no bucket, never fake neutrality.
    """
    if score is None or isinstance(score, bool):
        return None
    try:
        score = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return "positive" if score > 0.6 else "negative" if score < 0.4 else "neutral"


@dataclass(frozen=True)
class ReportScope:
    """One immutable scope shared by every digest report section (AUD-07).

    Before it, the digest's top-level aggregates read a ``today - days`` window
    with no upper bound, no base-row-kind filter and no source/scenario
    selection, while the brief applied its own filters — so the rendered
    sections could disagree, a day report leaked yesterday/future rows and
    saved WEEK/MONTH rollups re-entered later sums.
    """

    start: date  # inclusive
    end: date  # inclusive
    source_ids: Optional[frozenset] = None  # None = the whole workspace
    scenario_id: Optional[int] = None
    tenant_id: Optional[int] = None  # narrow-only, same rule as _analytics_query
    base_period_type: PeriodType = PeriodType.DAY

    @property
    def days(self) -> int:
        """Window length in days; matches period_bounds for digest periods."""
        return (self.end - self.start).days + 1

    def previous(self) -> "ReportScope":
        """The same-shaped window immediately before this one (never overlapping)."""
        prev_end = self.start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=self.days - 1)
        return replace(self, start=prev_start, end=prev_end)


def normalize_digest_axis(group_by: str | None) -> str:
    """Compatibility for saved tasks; public grouping APIs reject old axes."""
    return "themes" if not group_by or group_by in REMOVED_GROUPING_AXES else group_by

