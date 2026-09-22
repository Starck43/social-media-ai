from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from croniter import croniter

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..schedule import Schedule


class ScheduleManager(BaseManager):
    """Manager for Schedule model: cron validation and next-run computation."""

    def __init__(self):
        from ..schedule import Schedule

        super().__init__(Schedule)

    @staticmethod
    def validate_cron(cron_expr: str) -> bool:
        """Return True if cron expression is valid (5-field)."""
        try:
            croniter(cron_expr, datetime.now(timezone.utc))
            return True
        except (ValueError, KeyError):
            return False

    @staticmethod
    def next_run_at(cron_expr: str, timezone_name: str, after: Optional[datetime] = None) -> datetime:
        """Compute next run time for cron expression in the given timezone (UTC returned)."""
        from zoneinfo import ZoneInfo

        base = after or datetime.now(timezone.utc)
        tz = ZoneInfo(timezone_name)
        local_base = base.astimezone(tz)
        itr = croniter(cron_expr, local_base)
        nxt = itr.get_next(datetime)
        return nxt.astimezone(timezone.utc)

    async def get_active(self) -> list["Schedule"]:
        """All active schedules."""
        return await self.filter(is_active=True)

    async def get_due(self, now: Optional[datetime] = None) -> list["Schedule"]:
        """Schedules that are active and due for enqueueing (next_run_at stored in UTC)."""
        now = now or datetime.now(timezone.utc)
        return await self.filter(is_active=True, next_run_at__lte=now)

    async def mark_triggered(
        self,
        schedule_id: int,
        next_run_at: datetime,
        status: str = "ok",
        error: Optional[str] = None,
    ) -> None:
        """Record a trigger: set last_run_at/last_status and advance next_run_at."""
        await self.update_by_id(
            schedule_id,
            last_run_at=datetime.now(timezone.utc),
            last_status=status,
            last_error=error,
            next_run_at=next_run_at,
        )
