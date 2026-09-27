"""Unit tests for task cron helpers (no DB required)."""

from datetime import datetime, timezone

from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks.cron import cron_to_human, next_run_at, validate_cron


class TestValidateCron:
    def test_valid_expressions(self):
        assert validate_cron("* * * * *")
        assert validate_cron("*/5 * * * *")
        assert validate_cron("0 9 * * 1-5")
        assert validate_cron("30 8 1 * *")

    def test_once_accepted(self):
        assert validate_cron("@once")

    def test_invalid_expressions(self):
        assert not validate_cron("not a cron")
        assert not validate_cron("")
        assert not validate_cron("99 99 * * *")

    def test_manager_validate_once(self):
        assert AgentTaskManager.validate_cron("@once")


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


class TestCronToHuman:
    def test_empty_and_none(self):
        assert cron_to_human(None) == "—"
        assert cron_to_human("") == "—"

    def test_once(self):
        assert cron_to_human("@once") == "Разово"

    def test_daily(self):
        assert cron_to_human("0 9 * * *") == "Ежедневно в 09:00"

    def test_every_n_hours(self):
        assert cron_to_human("0 */6 * * *") == "Каждые 6 ч"

    def test_weekly(self):
        assert cron_to_human("0 5 * * 1") == "Еженедельно (Пн) в 05:00"

    def test_monthly(self):
        assert cron_to_human("0 4 1 * *") == "Ежемесячно 1-го в 04:00"

    def test_wildcard_fields_do_not_crash(self):
        # Regression: previously raised ValueError on int('*')
        assert cron_to_human("* * * * *") == "Ежедневно в *:*"

    def test_malformed_returns_raw(self):
        assert cron_to_human("bad") == "bad"
        assert cron_to_human("0 9 * *") == "0 9 * *"
