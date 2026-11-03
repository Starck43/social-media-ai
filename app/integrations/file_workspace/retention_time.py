"""Pure proposed retention timing, not authority or durable clock protection.

No clock reads, I/O, DB, provider, model or storage activation. The application
must supply trusted UTC times and atomically preserve its clock high-water mark
on every check, including expiry denial. These proposal constants await review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_CAPTURE_AGE = timedelta(minutes=5)
RETENTION_TTL = timedelta(hours=24)


class RetentionTimeUnavailable(Exception):
    """Sanitized failure, without caller timestamps or chained diagnostics."""

    def __init__(self):
        super().__init__("retention_time_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class RetentionDeadline:
    """Application-owned proposal data, not authenticated provenance or a grant."""

    captured_at: datetime
    expires_at: datetime


def _utc(value: object) -> datetime:
    # Exact stdlib UTC inputs avoid implicit local-time/DST conversion and
    # caller-controlled tzinfo callbacks. Normalize trusted server data upstream.
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError
    return value


def _clock(now: datetime, last_observed_at: datetime) -> datetime:
    now = _utc(now)
    if now < _utc(last_observed_at):
        raise ValueError
    return now


def make_retention_deadline(*, captured_at: datetime, now: datetime, last_observed_at: datetime) -> RetentionDeadline:
    """Admit capture age 0..5 min and fix expiry at capture + 24 h, not now.

    A repeated calculation cannot renew the deadline. This is time validation
    only; it does not check source version, consent, owner, quotas or idempotency.
    Inputs are trusted timezone.utc datetimes; naive/non-UTC values fail closed.
    """
    try:
        now = _clock(now, last_observed_at)
        captured_at = _utc(captured_at)
        if not timedelta(0) <= now - captured_at <= MAX_CAPTURE_AGE:
            raise ValueError
        return RetentionDeadline(captured_at, captured_at + RETENTION_TTL)
    except Exception:
        pass
    raise RetentionTimeUnavailable()


def retention_time_eligible(deadline: RetentionDeadline, *, now: datetime, last_observed_at: datetime) -> bool:
    """Check time only, with exact expiry denied and supplied rollback rejected.

    The high-water mark is mandatory, trusted and external: this pure function
    neither persists it nor proves it is current. A real caller must preserve
    max(previous mark, observed now) even on denial, and serialize that update
    with use. A later clock rollback must not reopen a previously expired row.
    Fixed 24h proposal format only; arbitrary deadline edits, including shorter
    windows, are not a reviewed policy-change mechanism. No authorization grant.
    """
    try:
        if type(deadline) is not RetentionDeadline:
            return False
        captured_at = _utc(deadline.captured_at)
        expires_at = _utc(deadline.expires_at)
        now = _clock(now, last_observed_at)
        if expires_at != captured_at + RETENTION_TTL:
            return False
        return captured_at <= now < expires_at
    except Exception:
        return False
