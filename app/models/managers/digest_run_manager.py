from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..digest_run import DigestRun


# Error prefix of a scheduled run whose delivery reached some recipients but not all.
# Such a run must not be rebuilt and re-broadcast automatically.
PARTIAL_DELIVERY_REVIEW = "partial_delivery_requires_review"

# Failure code of a scheduled attempt that found its (schedule, period) run row
# already owned by another attempt — in flight, or stopped mid-flight.
DIGEST_RUN_IN_PROGRESS = "digest_run_in_progress_requires_review"


class DigestRunInProgress(RuntimeError):
    """Another attempt owns this schedule+period's run row; do not build or send."""

    def __init__(self, run_id: Optional[int] = None):
        super().__init__(DIGEST_RUN_IN_PROGRESS)
        self.run_id = run_id


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

    async def delivery_requires_review(
        self, agent_task_id: Optional[int], period_start: date, period_end: date
    ) -> bool:
        """True if this schedule+period already delivered to some recipients but not all.

        Manual runs (NULL agent_task_id) keep separate history and are never blocked.
        """
        if agent_task_id is None:
            return False
        existing = await self.get(agent_task_id=agent_task_id, period_start=period_start, period_end=period_end)
        return (
            existing is not None
            and existing.status == "failed"
            and (existing.error or "").startswith(PARTIAL_DELIVERY_REVIEW)
        )

    async def start_run(
        self,
        *,
        agent_task_id: Optional[int],
        period: str,
        period_start: date,
        period_end: date,
        channel: str = "auto",
        force: bool = False,
    ) -> Optional["DigestRun"]:
        """Take the run row for (schedule, period), creating it when missing.

        Reusing the row instead of inserting a new one keeps the unique
        constraint on (agent_task_id, period_start, period_end) satisfiable when
        the same digest is re-attempted after a failure or a skip.
        Manual runs (agent_task_id=None) always get a fresh row — NULLs do not
        collide, and each manual send is separate history.

        A scheduled attempt owns the row only if it moved it to `pending` itself:
        the reset is conditional on the observed status, and a concurrent insert
        loses on the unique constraint. A row that is already `pending` belongs to
        another attempt in flight — or one that stopped mid-flight, whose delivery
        is unknown. Both raise `DigestRunInProgress` before any build or send.
        `force` (the operator's explicit resend) may take over a `pending` row;
        two concurrent forced runs are not fenced against each other.
        """
        if agent_task_id is not None:
            existing = await self.get(
                agent_task_id=agent_task_id,
                period_start=period_start,
                period_end=period_end,
            )
            if existing is not None:
                if existing.status == "pending" and not force:
                    raise DigestRunInProgress(existing.id)
                taken = await self.filter(id=existing.id, status=existing.status).update(
                    status="pending", channel=channel, message_id=None, error=None
                )
                if taken != 1:
                    raise DigestRunInProgress(existing.id)
                return await self.get(id=existing.id)

        from sqlalchemy.exc import IntegrityError

        try:
            return await self.create(
                agent_task_id=agent_task_id,
                period=period,
                period_start=period_start,
                period_end=period_end,
                channel=channel,
                status="pending",
            )
        except IntegrityError:
            if agent_task_id is None:
                raise
            # A concurrent attempt inserted the row for this schedule+period first.
            raise DigestRunInProgress() from None


digest_runs = DigestRunManager()
