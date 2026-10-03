"""Cron helpers built on croniter (5-field expressions, tz-aware)."""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from croniter import croniter

from app.core.config import settings

DOW_RU = ["", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def resolve_tz(tenant: object | None = None) -> str:
    """Cron timezone for a workspace: its own zone wins, the setting is the fallback.

    The single entry point for "which zone is this workspace's cron in". A task
    is scheduled when `next_run_at` is computed AND re-advanced after every
    fire, so all writers must agree: mixing a per-workspace zone at creation
    with the global setting in the runner silently moves the schedule by the
    offset between them after the first run.

    `SCHEDULER_TIMEZONE` stays meaningful as the fallback for a workspace with
    no zone of its own, and for code that has no tenant at hand (a workspace
    that was deleted mid-tick, a bootstrap before the row is readable).
    """
    tz = getattr(tenant, "timezone", None)
    return tz or settings.SCHEDULER_TIMEZONE


def _field(value: str) -> str:
    """Normalize a numeric cron field for display, falling back to raw value."""
    return f"{int(value):02d}" if value.isdigit() else value


def cron_to_human(cron_expr: str | None) -> str:
    """Convert a cron expression to a human-readable Russian string."""
    if not cron_expr:
        return "—"
    if cron_expr == "@once":
        return "Разово"
    parts = cron_expr.split()
    if len(parts) != 5:
        return cron_expr
    minute, hour, day_of_month, month, dow = parts
    if minute == "0" and hour.startswith("*/"):
        n = hour[2:]
        return f"Каждые {n} ч"
    if day_of_month == "*" and month == "*" and dow == "*":
        return f"Ежедневно в {_field(hour)}:{_field(minute)}"
    if day_of_month == "*" and month == "*" and dow != "*":
        try:
            days = "-".join(DOW_RU[int(d)] for d in dow.split(",") if d.isdigit())
        except (ValueError, IndexError):
            days = dow
        return f"Еженедельно ({days}) в {_field(hour)}:{_field(minute)}"
    if day_of_month != "*" and month == "*" and dow == "*":
        return f"Ежемесячно {day_of_month}-го в {_field(hour)}:{_field(minute)}"
    return cron_expr


def validate_cron(cron_expr: str) -> bool:
    """Return True if cron expression is valid (5-field) or @once."""
    if cron_expr == "@once":
        return True
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
