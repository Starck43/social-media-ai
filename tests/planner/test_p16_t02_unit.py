"""No-DB tests for reminder time presets and request invariants."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.services.planner.reminder_contracts import ReminderCreate
from app.services.planner.reminders import _month_before, reminder_time

UTC = timezone.utc


def test_custom_requires_aware_explicit_instant():
    with pytest.raises(ValidationError):
        ReminderCreate(preset="custom")
    with pytest.raises(ValidationError):
        ReminderCreate(preset="custom", remind_at="2027-01-01T09:00:00")
    request = ReminderCreate(preset="custom", remind_at="2027-01-01T09:00:00+03:00")
    assert request.remind_at == datetime(2027, 1, 1, 9, tzinfo=ZoneInfo("Europe/Moscow"))


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        ("day", datetime(2027, 3, 28, 9, tzinfo=ZoneInfo("Europe/Berlin"))),
        ("week", datetime(2027, 3, 22, 9, tzinfo=ZoneInfo("Europe/Berlin"))),
    ],
)
def test_timed_presets_use_task_local_wall_time_across_dst(preset, expected):
    task = SimpleNamespace(
        due_at=datetime(2027, 3, 29, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        due_date=None,
        timezone="Europe/Berlin",
    )
    result = reminder_time(ReminderCreate(preset=preset), task, now=datetime(2027, 1, 1, tzinfo=UTC))
    assert result == expected.astimezone(UTC)
    assert result.tzinfo is UTC


def test_month_preset_clamps_shorter_month_and_date_deadline_uses_nine_local():
    march = SimpleNamespace(
        due_at=None,
        due_date=date(2027, 3, 31),
        timezone="Europe/Berlin",
    )
    result = reminder_time(ReminderCreate(preset="month"), march, now=datetime(2027, 1, 1, tzinfo=UTC))
    assert result == datetime(2027, 2, 28, 9, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(UTC)
    assert _month_before(datetime(2028, 3, 31, 9, tzinfo=UTC)) == datetime(2028, 2, 29, 9, tzinfo=UTC)


def test_preset_without_task_deadline_is_rejected():
    task = SimpleNamespace(due_at=None, due_date=None, timezone="UTC")
    with pytest.raises(ValueError, match="deadline_required"):
        reminder_time(ReminderCreate(preset="day"), task, now=datetime.now(UTC))


def test_only_named_channels_and_presets_are_accepted():
    with pytest.raises(ValidationError):
        ReminderCreate(preset="month", channel="sms")
    with pytest.raises(ValidationError):
        ReminderCreate(preset="tomorrow")
