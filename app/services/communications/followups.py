"""Follow-up commitments, quiet hours, and evidence-backed reminders service (P12-T04).

Implements:
  1. Tenant/user quiet hours window enforcement (21:00–08:00) with automatic delivery rescheduling.
  2. Imprecise date detection (e.g., "в начале следующей недели") requiring human verification.
  3. Evidence-backed citations linking reminders directly to letters, contracts, or chat messages.
  4. Follow-up lifecycle state machine: pending, requires_confirmation, confirmed, snoozed, dismissed, completed.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

# Follow-up status codes
STATUS_PENDING = "pending"
STATUS_REQUIRES_CONFIRMATION = "requires_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_SNOOZED = "snoozed"
STATUS_DISMISSED = "dismissed"
STATUS_COMPLETED = "completed"

STATUS_LABELS: dict[str, str] = {
    STATUS_PENDING: "Ожидает обработки",
    STATUS_REQUIRES_CONFIRMATION: "Требует подтверждения даты",
    STATUS_CONFIRMED: "Дата подтверждена",
    STATUS_SNOOZED: "Отложено (Snoozed)",
    STATUS_DISMISSED: "Отклонено",
    STATUS_COMPLETED: "Выполнено",
}

DEFAULT_QUIET_START = 21  # 21:00
DEFAULT_QUIET_END = 8  # 08:00

IMPRECISE_PATTERNS = [
    r"в\s+начале\s+следующей\s+недели",
    r"на\s+днях",
    r"после\s+праздников",
    r"до\s+конца\s+месяца",
    r"в\s+середине\s+месяца",
    r"как\s+будет\s+время",
    r"в\s+скором\s+времени",
    r"на\s+следующей\s+неделе",
    r"к\s+концу\s+недели",
]


def detect_imprecise_date(text: str) -> bool:
    """Check if the commitment or due date text contains ambiguous/relative phrasing."""
    lower = text.lower()
    return any(re.search(pat, lower) is not None for pat in IMPRECISE_PATTERNS)


def is_in_quiet_hours(
    dt: datetime,
    start_hour: int = DEFAULT_QUIET_START,
    end_hour: int = DEFAULT_QUIET_END,
    *,
    start_minute: int = 0,
    end_minute: int = 0,
) -> bool:
    """Check local wall time against the one shared quiet-hours window."""
    current = dt.hour * 60 + dt.minute
    start, end = start_hour * 60 + start_minute, end_hour * 60 + end_minute
    if start > end:
        return current >= start or current < end
    return start <= current < end


def adjust_for_quiet_hours(
    dt: datetime,
    start_hour: int = DEFAULT_QUIET_START,
    end_hour: int = DEFAULT_QUIET_END,
    *,
    start_minute: int = 0,
    end_minute: int = 0,
) -> datetime:
    """Defer local wall time to the window's end; old hour-only calls stay valid."""
    if not is_in_quiet_hours(
        dt, start_hour=start_hour, end_hour=end_hour, start_minute=start_minute, end_minute=end_minute
    ):
        return dt

    # If hour >= start_hour (e.g., 22:00), rolls forward to next day at end_hour
    # If hour < end_hour (e.g., 04:00), rolls forward to same day at end_hour
    if (
        start_hour * 60 + start_minute > end_hour * 60 + end_minute
        and dt.hour * 60 + dt.minute >= start_hour * 60 + start_minute
    ):
        next_day = dt + timedelta(days=1)
        return next_day.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    else:
        return dt.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)


def create_followup_item(
    item_id: str,
    counterparty: str,
    commitment_text: str,
    source_citation: str,
    due_at: str,
    escalation_policy: str = "Повтор через 24ч",
    quiet_hours_window: str = "21:00–08:00",
    requires_date_confirmation: bool | None = None,
) -> dict[str, Any]:
    """Create a structured follow-up reminder item."""
    if requires_date_confirmation is None:
        requires_date_confirmation = detect_imprecise_date(due_at) or detect_imprecise_date(commitment_text)

    status = STATUS_REQUIRES_CONFIRMATION if requires_date_confirmation else STATUS_PENDING

    return {
        "id": item_id,
        "counterparty": counterparty,
        "commitment_text": commitment_text,
        "source_citation": source_citation,
        "due_at": due_at,
        "quiet_hours_window": quiet_hours_window,
        "escalation_policy": escalation_policy,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "requires_date_confirmation": requires_date_confirmation,
        "confirmed_by": None,
    }


def confirm_followup_date(
    item: dict[str, Any],
    confirmed_due_at: str,
    actor_id: str,
) -> dict[str, Any]:
    """Human confirmation of an ambiguous due date."""
    updated = dict(item)
    updated["due_at"] = confirmed_due_at
    updated["requires_date_confirmation"] = False
    updated["status"] = STATUS_CONFIRMED
    updated["status_label"] = STATUS_LABELS[STATUS_CONFIRMED]
    updated["confirmed_by"] = actor_id
    return updated


def snooze_followup(
    item: dict[str, Any],
    snooze_until: str,
    actor_id: str,
) -> dict[str, Any]:
    """Snooze follow-up reminder until specified time."""
    updated = dict(item)
    updated["due_at"] = snooze_until
    updated["status"] = STATUS_SNOOZED
    updated["status_label"] = STATUS_LABELS[STATUS_SNOOZED]
    updated["confirmed_by"] = actor_id
    return updated


def dismiss_followup(
    item: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    """Dismiss follow-up reminder."""
    updated = dict(item)
    updated["status"] = STATUS_DISMISSED
    updated["status_label"] = STATUS_LABELS[STATUS_DISMISSED]
    updated["confirmed_by"] = actor_id
    return updated
