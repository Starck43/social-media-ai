from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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

# A forced resend may take over a `pending` row only after it has not been
# written for this long (mirrors the job queue's stale-lease reaping window);
# a younger `pending` row is treated as an attempt still in flight.
FORCED_TAKEOVER_AFTER = timedelta(minutes=30)


def pending_is_stale(updated_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """True if a `pending` run row has not been written for `FORCED_TAKEOVER_AFTER`.

    A missing timestamp is not evidence of staleness. Naive values are UTC.
    """
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - updated_at >= FORCED_TAKEOVER_AFTER


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
        `force` (the operator's explicit resend) may take over a `pending` row
        only once it is stale (`FORCED_TAKEOVER_AFTER` without a write), so a
        resend never races an attempt still in flight.
        Every reuse is conditional on the observed status *and* `updated_at`
        version: of two simultaneous claims of the same row only one matches.
        """
        if agent_task_id is not None:
            existing = await self.get(
                agent_task_id=agent_task_id,
                period_start=period_start,
                period_end=period_end,
            )
            if existing is not None:
                if existing.status == "pending" and not (force and pending_is_stale(existing.updated_at)):
                    raise DigestRunInProgress(existing.id)
                taken = await self.filter(
                    id=existing.id, status=existing.status, updated_at=existing.updated_at
                ).update(status="pending", channel=channel, message_id=None, error=None)
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
