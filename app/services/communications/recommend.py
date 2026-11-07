"""Fail-closed, side-effect-free channel ranking for P15-T07.

The caller must derive eligibility from current tenant-scoped consent and
reachability evidence, never from user-supplied flags. Ranking is not delivery
permission, a promise of delivery, or an instruction to send anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CHANNEL_ORDER = ("email", "telegram", "max", "phone", "bitrix24")


@dataclass(frozen=True)
class ChannelSignals:
    channel: str
    consented: bool
    reachable: bool
    inbound_count: int = 0
    outbound_count: int = 0
    last_inbound_at: datetime | None = None


@dataclass(frozen=True)
class Recommendation:
    channel: str | None
    confidence: float
    reasons: tuple[str, ...]


def _recent_inbound(last: datetime | None, now: datetime) -> float:
    if last is None or last.utcoffset() is None or last > now:
        return 0.0
    age = now - last
    if age <= timedelta(days=7):
        return 1.0
    if age <= timedelta(days=30):
        return 0.75
    if age <= timedelta(days=90):
        return 0.4
    return 0.1


def rank_channels(
    candidates: list[ChannelSignals], *, preferred_channel: str | None = None, now: datetime | None = None
) -> Recommendation:
    """Rank already authorized routes; no fallback may bypass eligibility.

    Preference dominates activity; activity dominates volume. Ties are stable.
    Confidence is a bounded heuristic, not a calibrated success probability.
    """
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    seen: set[str] = set()
    eligible = []
    for candidate in candidates:
        if candidate.channel not in CHANNEL_ORDER or candidate.channel in seen:
            raise ValueError("invalid or duplicate channel")
        seen.add(candidate.channel)
        if type(candidate.inbound_count) is not int or candidate.inbound_count < 0:
            raise ValueError("invalid inbound count")
        if type(candidate.outbound_count) is not int or candidate.outbound_count < 0:
            raise ValueError("invalid outbound count")
        if candidate.consented is not True or candidate.reachable is not True:
            continue
        preferred = candidate.channel == preferred_channel
        activity = _recent_inbound(candidate.last_inbound_at, now)
        count = candidate.inbound_count + candidate.outbound_count
        volume = min(1.0, math.log1p(count) / math.log(21))
        # Tuples enforce priority even when frequency differs by orders of magnitude.
        eligible.append((preferred, activity, volume, count, candidate))
    if not eligible:
        return Recommendation(None, 0.0, ("no_consented_reachable_channel",))
    eligible.sort(key=lambda x: (-int(x[0]), -x[1], -x[3], CHANNEL_ORDER.index(x[4].channel)))
    preferred, activity, volume, count, selected = eligible[0]
    if len(eligible) == 1:
        margin = 0.0
    else:
        other = eligible[1]
        margin = 1.0 if preferred != other[0] else max(0.0, activity - other[1], (volume - other[2]) * 0.25)
    confidence = round(min(0.95, 0.2 + 0.35 * min(1.0, count / 20) + 0.3 * margin), 3)
    reasons = ["consented_and_reachable"]
    if preferred:
        reasons.append("manual_preference")
    if activity > 0:
        reasons.append("recent_inbound_activity" if activity >= 0.4 else "older_inbound_activity")
    if count:
        reasons.append("message_frequency")
    return Recommendation(selected.channel, confidence, tuple(reasons))
