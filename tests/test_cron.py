"""Unit tests for scheduler cron helpers (no DB required)."""
from datetime import datetime, timezone

from app.scheduler.cron import next_run_at, validate_cron


class TestValidateCron:
    def test_valid_expressions(self):
        assert validate_cron("* * * * *")
        assert validate_cron("*/5 * * * *")
        assert validate_cron("0 9 * * 1-5")
        assert validate_cron("30 8 1 * *")

    def test_invalid_expressions(self):
        assert not validate_cron("not a cron")
        assert not validate_cron("")
        assert not validate_cron("99 99 * * *")


class TestNextRunAt:
    def test_daily_at_9am_msk_is_6am_utc(self):
        after = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)  # 15:00 MSK
        nxt = next_run_at("0 9 * * *", "Europe/Moscow", after=after)
        assert nxt.date().isoformat() == "2026-09-23"  # next morning
        assert nxt.hour == 6 and nxt.minute == 0
        assert nxt.tzinfo == timezone.utc

    def test_every_five_minutes(self):
        after = datetime(2026, 9, 22, 10, 3, tzinfo=timezone.utc)
        nxt = next_run_at("*/5 * * * *", "UTC", after=after)
        assert (nxt.hour, nxt.minute) == (10, 5)

    def test_monday_range(self):
        # 2026-09-22 is Tuesday; next Mon-Fri 9:00 UTC = Wednesday
        after = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
        nxt = next_run_at("0 9 * * 1-5", "UTC", after=after)
        assert nxt.weekday() < 5
        assert nxt.day == 23
