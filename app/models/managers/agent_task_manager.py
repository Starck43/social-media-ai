from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from croniter import croniter

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_task import AgentTask


class AgentTaskManager(BaseManager):
    """Manager for AgentTask model: cron validation and next-run computation."""

    def __init__(self):
        from ..agent_task import AgentTask

        super().__init__(AgentTask)

    @staticmethod
    def validate_cron(cron_expr: str) -> bool:
        """Return True if cron expression is valid (5-field) or @once."""
        if cron_expr == "@once":
            return True
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

    async def get_active(self) -> list["AgentTask"]:
        """All active tasks."""
        return await self.filter(is_active=True)

    async def get_sources(self, task_id: int) -> list[int]:
        """Source ids linked to the task through the m2m table."""
        return (await self._linked_source_ids([task_id])).get(task_id, [])

    async def get_sources_map(self, task_ids: list[int]) -> dict[int, list[int]]:
        """`{task_id: [source_id, ...]}` for a batch of tasks (one query)."""
        return await self._linked_source_ids(task_ids)

    async def _linked_source_ids(self, task_ids: list[int]) -> dict[int, list[int]]:
        if not task_ids:
            return {}
        from sqlalchemy import select

        from ..agent_task import agent_task_sources

        rows = await self.aggregate_rows(
            select(agent_task_sources.c.agent_task_id, agent_task_sources.c.source_id).where(
                agent_task_sources.c.agent_task_id.in_(task_ids)
            )
        )
        grouped: dict[int, list[int]] = {}
        for task_id, source_id in rows:
            grouped.setdefault(int(task_id), []).append(int(source_id))
        return {tid: sorted(grouped.get(tid, [])) for tid in task_ids}

    async def set_sources(self, task_id: int, source_ids: list[int]) -> int:
        """Replace the task's source links with exactly `source_ids`.

        The single write path for the m2m table — the web form, the CLI and the
        agent tool all call it instead of hand-rolling delete+insert.
        """
        from ..agent_task import agent_task_sources

        return await self._replace_secondary(
            agent_task_sources, "agent_task_id", task_id, "source_id", source_ids
        )

    async def add_sources(self, task_id: int, source_ids: list[int]) -> int:
        """Link sources to the task, keeping the links that already exist."""
        from ..agent_task import agent_task_sources

        return await self._add_secondary(
            agent_task_sources, "agent_task_id", task_id, "source_id", source_ids
        )

    async def get_due(self, now: Optional[datetime] = None) -> list["AgentTask"]:
        """AgentTasks that are active and due for enqueueing (next_run_at stored in UTC)."""
        now = now or datetime.now(timezone.utc)
        return await self.filter(is_active=True, next_run_at__lte=now)

    async def mark_triggered(
        self,
        task_id: int,
        next_run_at: datetime,
        status: str = "ok",
        error: Optional[str] = None,
    ) -> None:
        """Record a trigger: set last_run_at/last_status and advance next_run_at.

        Used by the scheduler to advance the schedule (dedup) and to flag a
        bad cron expression. The actual outcome of a job is recorded later by
        `record_result` when the worker finishes, so `last_status` here is a
        provisional value and may be overwritten.
        """
        await self.update_by_id(
            task_id,
            last_run_at=datetime.now(timezone.utc),
            last_status=status,
            last_error=error,
            next_run_at=next_run_at,
        )

    async def record_result(self, task_id: int, status: str, error: Optional[str] = None) -> None:
        """Record the real outcome of a job run on its AgentTask.

        Called by the job dispatcher on completion/failure so `last_status` /
        `last_error` / `last_run_at` reflect what actually happened, not just
        that the job was enqueued. Must run inside the task's tenant scope.
        """
        await self.update_by_id(
            task_id,
            last_run_at=datetime.now(timezone.utc),
            last_status=status,
            last_error=error,
        )
