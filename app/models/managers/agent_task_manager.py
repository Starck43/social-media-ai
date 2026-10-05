from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from croniter import croniter

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_task import AgentTask


# Job types that operate on concrete sources. The rest (digest, prune, learn,
# reflect) work at the workspace/memory level and do not require a source.
SOURCE_BASED_JOB_TYPES = frozenset({"collect", "analyze"})


class AgentTaskManager(BaseManager):
    """Manager for AgentTask model: cron validation and next-run computation."""

    def __init__(self):
        from ..agent_task import AgentTask

        super().__init__(AgentTask)

    @staticmethod
    def requires_sources(job_type: str) -> bool:
        """Whether a task of this job type needs at least one active source."""
        return job_type in SOURCE_BASED_JOB_TYPES

    @staticmethod
    def requires_content_dates(job_type: str) -> bool:
        """Whether a task of this job type needs a content start date.

        Same set as `requires_sources`: collect/analyze pull content from the
        platforms, and without a `cli_dates.start_date` the first run falls
        back to "no date filters" and drains the whole history from the first
        post. digest/prune/learn/reflect work on what is already stored.
        """
        return job_type in SOURCE_BASED_JOB_TYPES

    @staticmethod
    def parse_date(value: str | None):
        """Parse a form/CLI date to a `date`, or None when absent/invalid.

        Accepts the same formats the CLI's `--start-date` does (DD-MM-YYYY,
        DD.MM.YYYY, ISO). Stored as `YYYY-MM-DD` so the JSON payload stays
        serialisable and every reader (`universal_date_parser`) can parse it.
        """
        if not value:
            return None
        from app.utils.date_parsing import universal_date_parser

        parsed = universal_date_parser(value)
        return parsed.date() if parsed else None

    @staticmethod
    def build_dates_payload(start_date, end_date=None, force_refresh: bool = True) -> dict:
        """The payload keys a collect/analyze run needs for its date window.

        Mirrors the CLI: `force_refresh` plus `cli_dates` with the explicit
        bounds, so a fresh source is not drained from the first post. The CLI
        always refreshes (its whole point is a one-off full window); the web
        passes the operator's checkbox so it can run incrementally instead.
        """
        cli_dates: dict[str, str] = {}
        if start_date:
            cli_dates["start_date"] = str(start_date)
        if end_date:
            cli_dates["end_date"] = str(end_date)
        payload: dict[str, Any] = {"force_refresh": bool(force_refresh)}
        if cli_dates:
            payload["cli_dates"] = cli_dates
        return payload

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

        return await self._replace_secondary(agent_task_sources, "agent_task_id", task_id, "source_id", source_ids)

    async def add_sources(self, task_id: int, source_ids: list[int]) -> int:
        """Link sources to the task, keeping the links that already exist."""
        from ..agent_task import agent_task_sources

        return await self._add_secondary(agent_task_sources, "agent_task_id", task_id, "source_id", source_ids)

    @staticmethod
    def _task_effectively_active(task, workspace_active_source_ids) -> bool:
        """Whether a task is effectively active — i.e. should be scheduled/shown as active.

        A task is effectively active only when all of:
          - its own `is_active` flag is set;
          - its scenario (if one is chosen) is active — a deactivated scenario
            suspends every task bound to it;
          - if its job type operates on sources (collect/analyze) it has at
            least one active source: an explicit linked source that is active,
            or (with no linked sources, which works over "all active sources")
            any active source in the workspace. Workspace/memory-level types
            (digest/prune/learn/reflect) never need a source.

        `workspace_active_source_ids` is the set of active source ids in the task's
        workspace, used only for the "no linked sources" case.
        """
        if not getattr(task, "is_active", False):
            return False
        scenario = getattr(task, "agent_scenario", None)
        if scenario is not None and not scenario.is_active:
            return False
        if not AgentTaskManager.requires_sources(getattr(task, "job_type", "") or ""):
            return True
        linked = task.sources or []
        if linked:
            # Explicit links: active iff at least one is active — no fallback.
            return any(getattr(s, "is_active", True) for s in linked)
        # No linked sources -> operates over "all active sources".
        return bool(workspace_active_source_ids)

    async def get_workspace_active_source_ids(self) -> set[int]:
        """Active source ids in the current (tenant-scoped) workspace."""
        from ..source import Source

        rows = await Source.objects.filter(is_active=True).values(Source.id).rows()
        return {r.id for r in rows}

    async def effective_active_map(
        self, tasks: list["AgentTask"], workspace_active_source_ids: Optional[set[int]] = None
    ) -> dict[int, bool]:
        """`{task_id: is_effectively_active}` for a batch of tasks.

        A task bound to a deactivated scenario, or a source-based task (collect/
        analyze) with no active source to operate on, is not effectively active,
        regardless of its `is_active` flag. Pass `workspace_active_source_ids` to
        avoid a second query when the caller already holds the workspace's active
        source ids.
        """
        active = (
            workspace_active_source_ids
            if workspace_active_source_ids is not None
            else await self.get_workspace_active_source_ids()
        )
        return {t.id: self._task_effectively_active(t, active) for t in tasks}

    async def get_due(self, now: Optional[datetime] = None) -> list["AgentTask"]:
        """AgentTasks that are active and due for enqueueing (next_run_at stored in UTC).

        Only *effectively* active tasks are returned: one bound to a deactivated
        scenario, or a source-based task (collect/analyze) with no active source
        to operate on, is skipped, so the queue is not formed for it.
        """
        now = now or datetime.now(timezone.utc)
        due = await self.filter(is_active=True, next_run_at__lte=now).prefetch_related("sources", "agent_scenario")
        if not due:
            return []
        active = await self.get_workspace_active_source_ids()
        return [t for t in due if self._task_effectively_active(t, active)]

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
