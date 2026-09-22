"""Cron helpers built on croniter (5-field expressions, tz-aware)."""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from croniter import croniter


def validate_cron(cron_expr: str) -> bool:
    """Return True if cron expression is valid."""
    try:
        croniter(cron_expr, datetime.now(timezone.utc))
        return True
    except (ValueError, KeyError):
        return False


def next_run_at(cron_expr: str, tz_name: str, after: Optional[datetime] = None) -> datetime:
    """Next fire time for cron_expr in tz_name; returns tz-aware UTC datetime."""
    base = after or datetime.now(timezone.utc)
    tz = ZoneInfo(tz_name)
    itr = croniter(cron_expr, base.astimezone(tz))
    return itr.get_next(datetime).astimezone(timezone.utc)
