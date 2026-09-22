from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..digest_run import DigestRun


class DigestRunManager(BaseManager):
    """Manager for digest runs: idempotency per (schedule, period)."""

    def __init__(self):
        from ..digest_run import DigestRun

        super().__init__(DigestRun)

    async def already_sent(self, schedule_id: Optional[int], period_start: date, period_end: date) -> bool:
        """True if a digest for this schedule+period was already sent successfully.

        Note: NULL schedule_id never collides in Postgres unique constraints,
        so manual `send-now` runs are never blocked.
        """
        existing = await self.get(
            schedule_id=schedule_id,
            period_start=period_start,
            period_end=period_end,
            status="sent",
        )
        return existing is not None
