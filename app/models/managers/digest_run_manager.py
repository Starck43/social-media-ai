from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..digest_run import DigestRun


class DigestRunManager(BaseManager["DigestRun"]):
    """Manager for digest runs: idempotency per (schedule, period)."""

    def __init__(self):
        from ..digest_run import DigestRun

        super().__init__(DigestRun)

    async def cost_today(self, now: Optional[datetime] = None) -> float:
        """Total USD spent on digest LLM summaries today (UTC day) — the daily cap check."""
        from datetime import timezone

        now = now or datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self.filter(created_at__gte=day_start)
        return sum(float(row.llm_cost or 0.0) for row in rows)

    async def already_sent(self, agent_task_id: Optional[int], period_start: date, period_end: date) -> bool:
        """True if a digest for this schedule+period was already sent successfully.

        Note: NULL agent_task_id never collides in Postgres unique constraints,
        so manual `send-now` runs are never blocked.
        """
        existing = await self.get(
            agent_task_id=agent_task_id,
            period_start=period_start,
            period_end=period_end,
            status="sent",
        )
        return existing is not None

    async def start_run(
        self,
        *,
        agent_task_id: Optional[int],
        period: str,
        period_start: date,
        period_end: date,
        channel: str = "auto",
    ) -> Optional["DigestRun"]:
        """Return the run row for (schedule, period), creating it when missing.

        Reusing the row instead of inserting a new one keeps the unique
        constraint on (agent_task_id, period_start, period_end) satisfiable when
        the same digest is re-attempted after a failure or a skip.
        Manual runs (agent_task_id=None) always get a fresh row — NULLs do not
        collide, and each manual send is separate history.
        """
        if agent_task_id is not None:
            existing = await self.get(
                agent_task_id=agent_task_id,
                period_start=period_start,
                period_end=period_end,
            )
            if existing is not None:
                return await self.update_by_id(
                    existing.id, status="pending", channel=channel, message_id=None, error=None
                )

        return await self.create(
            agent_task_id=agent_task_id,
            period=period,
            period_start=period_start,
            period_end=period_end,
            channel=channel,
            status="pending",
        )


digest_runs = DigestRunManager()
