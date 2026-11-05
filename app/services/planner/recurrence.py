"""Bounded RFC 5545 subset. Local wall-clock arithmetic, never expanded storage.

DAILY/WEEKLY/MONTHLY, INTERVAL and one of COUNT/UNTIL only. Invalid month days
and nonexistent DST local times are skipped, not clamped/shifted. Ambiguous
future local times use the first occurrence (fold=0). DTSTART counts as one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


@dataclass(frozen=True)
class Rule:
    frequency: str
    interval: int = 1
    count: int | None = None
    until: date | datetime | None = None

    @property
    def text(self):
        parts = [f"FREQ={self.frequency}", f"INTERVAL={self.interval}"]
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        if self.until is not None:
            end = self.until
            stamp = f"{end.year:04d}{end.month:02d}{end.day:02d}"
            if isinstance(end, datetime):
                stamp += f"T{end.hour:02d}{end.minute:02d}{end.second:02d}Z"
            parts.append("UNTIL=" + stamp)
        return ";".join(parts)


def parse_rule(text: str, *, timed: bool) -> Rule:
    if not isinstance(text, str) or not 1 <= len(text) <= 150:
        raise ValueError("invalid_recurrence")
    parts = text.split(";")
    pairs = [part.split("=") for part in parts]
    if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
        raise ValueError("invalid_recurrence")
    values = dict(pairs)
    if set(values) - {"FREQ", "INTERVAL", "COUNT", "UNTIL"} or values.get("FREQ") not in {"DAILY", "WEEKLY", "MONTHLY"}:
        raise ValueError("unsupported_recurrence")
    for key, maximum in (("INTERVAL", 366), ("COUNT", 10000)):
        if key in values and (not re.fullmatch(r"[1-9][0-9]{0,4}", values[key]) or int(values[key]) > maximum):
            raise ValueError("invalid_recurrence_bound")
    if "COUNT" in values and "UNTIL" in values:
        raise ValueError("ambiguous_recurrence_end")
    until = None
    if "UNTIL" in values:
        pattern, fmt = (r"[0-9]{8}T[0-9]{6}Z", "%Y%m%dT%H%M%SZ") if timed else (r"[0-9]{8}", "%Y%m%d")
        if not re.fullmatch(pattern, values["UNTIL"]):
            raise ValueError("invalid_recurrence_until")
        parsed = datetime.strptime(values["UNTIL"], fmt)
        until = parsed.replace(tzinfo=UTC) if timed else parsed.date()
    return Rule(
        values["FREQ"], int(values.get("INTERVAL", "1")), int(values["COUNT"]) if "COUNT" in values else None, until
    )


def occurrence_key(rule: str | None, anchor: date | datetime, occurrence: date | datetime) -> str:
    def stamp(value):
        return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value.isoformat()

    envelope = f"{rule or 'ONCE'}|{stamp(anchor)}|{stamp(occurrence)}"
    return hashlib.sha256(envelope.encode()).hexdigest()


def next_occurrence(
    anchor: date | datetime, rule_text: str, *, after: date | datetime, tz: str
) -> tuple[date | datetime, int] | None:
    timed = isinstance(anchor, datetime)
    if timed != isinstance(after, datetime) or (timed and (anchor.utcoffset() is None or after.utcoffset() is None)):
        raise ValueError("incompatible_recurrence_dates")
    zone = ZoneInfo(tz)
    rule = parse_rule(rule_text, timed=timed)
    local = anchor.astimezone(zone).replace(tzinfo=None) if timed else datetime.combine(anchor, time())
    valid = 0
    # No unbounded history expansion for a far-future threshold or huge interval.
    for index in range(10001):
        try:
            if rule.frequency == "MONTHLY":
                month = local.year * 12 + local.month - 1 + index * rule.interval
                year, month = divmod(month, 12)
                try:
                    candidate = local.replace(year=year, month=month + 1)
                except ValueError:
                    if year > 9999:
                        return None
                    continue
            else:
                days = index * rule.interval * (7 if rule.frequency == "WEEKLY" else 1)
                candidate = local + timedelta(days=days)
        except (OverflowError, ValueError):
            return None
        if timed:
            candidate = anchor if index == 0 else candidate.replace(tzinfo=zone, fold=0)
            if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None):
                continue
            value = candidate.astimezone(UTC)
        else:
            value = candidate.date()
        valid += 1
        if rule.count is not None and valid > rule.count:
            return None
        if rule.until is not None and value > rule.until:
            return None
        if value > after:
            return value, valid - 1
    raise ValueError("recurrence_search_limit")
