from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from croniter import croniter

from app.core.permissions import require_permission
from app.types import ActionType

from .base_manager import BaseManager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..agent_task import AgentTask


# Job types that operate on concrete sources. The rest (digest, prune, learn,
# reflect) work at the workspace/memory level and do not require a source.
SOURCE_BASED_JOB_TYPES = frozenset({"collect", "analyze"})


class _RetryAdmissionBusy(Exception):
    """Sentinel that rolls back retry resets before a busy receipt is returned."""

    def __init__(self, job: Any) -> None:
        self.job = job
        super().__init__("Analysis retry admission was busy")


class _RetryAdmissionTransaction:
    """Convert retry-busy only after the database transaction rolls back."""

    def __init__(self, session: Any) -> None:
        self.transaction = session.begin()
        self.busy_job = None

    async def __aenter__(self):
        await self.transaction.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if isinstance(exc, _RetryAdmissionBusy):
            await self.transaction.__aexit__(exc_type, exc, traceback)
            self.busy_job = exc.job
            return True
        return await self.transaction.__aexit__(exc_type, exc, traceback)


def _busy_admission_receipt(task_id: int, job: Any) -> dict[str, Any]:
    """Keep the established task-admission busy receipt shape."""
    return {"status": "busy", "task_id": task_id, "job": job}


class AgentTaskManager(BaseManager):
    """Manager for AgentTask model: cron validation and next-run computation."""

    def __init__(self):
        from ..agent_task import AgentTask

        super().__init__(AgentTask)

    @staticmethod
    def _require_job_retention_permission(job_type: str | None) -> None:
        if job_type != "prune":
            return
        from app.core.permissions import PermissionDeniedError, get_current_user, has_permission
        from app.types import ActionType

        user = get_current_user()
        if not has_permission(user, "job", ActionType.UPDATE):
            raise PermissionDeniedError("Missing permission: job.update")

    @staticmethod
    def _validate_job_retention_schedule(job_type: str | None, payload: Any, cron_expr: str | None) -> None:
        if (
            job_type == "prune"
            and isinstance(payload, dict)
            and payload.get("mode") == "job_history"
            and cron_expr == "@once"
        ):
            raise ValueError("Настройка хранения истории должна быть повторяемой, например ежедневно.")

    # --- permission-gated writes (user surfaces: web, agent tools, CLI) ----

    @require_permission("agenttask", ActionType.CREATE)
    async def create(self, **kwargs: Any) -> "AgentTask":
        self._require_job_retention_permission(kwargs.get("job_type"))
        self._validate_job_retention_schedule(
            kwargs.get("job_type"), kwargs.get("payload"), kwargs.get("cron_expr")
        )
        return await super().create(**kwargs)

    @require_permission("agenttask", ActionType.UPDATE)
    async def update_by_id(self, instance_id: int, **kwargs: Any) -> Optional["AgentTask"]:
        current = await self.get(id=instance_id)
        self._require_job_retention_permission(getattr(current, "job_type", None))
        self._require_job_retention_permission(kwargs.get("job_type"))
        if current is not None:
            self._validate_job_retention_schedule(
                kwargs.get("job_type", current.job_type),
                kwargs.get("payload", current.payload),
                kwargs.get("cron_expr", current.cron_expr),
            )
        return await super().update_by_id(instance_id, **kwargs)

    @require_permission("agenttask", ActionType.UPDATE)
    async def update(self, **values: Any) -> int:
        for task in await self.filter():
            if task.job_type == "prune":
                self._require_job_retention_permission(task.job_type)
            self._require_job_retention_permission(values.get("job_type"))
            self._validate_job_retention_schedule(
                values.get("job_type", task.job_type),
                values.get("payload", task.payload),
                values.get("cron_expr", task.cron_expr),
            )
        return await super().update(**values)

    @require_permission("agenttask", ActionType.DELETE)
    async def delete_by_id(self, instance_id: int) -> bool:
        current = await self.get(id=instance_id)
        self._require_job_retention_permission(getattr(current, "job_type", None))
        return await super().delete_by_id(instance_id)

    @require_permission("agenttask", ActionType.DELETE)
    async def delete(self, session: AsyncSession | None = None, **filters: Any) -> Callable[[], int] | int:
        """Gate filtered deletion just like delete_by_id; preserve base semantics."""
        matching = await self.filter(**filters)
        for task in matching:
            self._require_job_retention_permission(task.job_type)
        return await super().delete(session=session, **filters)

    @require_permission("agenttask", ActionType.UPDATE)
    async def set_sources(self, task_id: int, source_ids: list[int]) -> int:
        """Replace the task's source links with exactly `source_ids`.

        The single write path for the m2m table — the web form, the CLI and the
        agent tool all call it instead of hand-rolling delete+insert.
        """
        from ..agent_task import agent_task_sources

        return await self._replace_secondary(agent_task_sources, "agent_task_id", task_id, "source_id", source_ids)

    @require_permission("agenttask", ActionType.UPDATE)
    async def add_sources(self, task_id: int, source_ids: list[int]) -> int:
        """Link sources to the task, keeping the links that already exist."""
        from ..agent_task import agent_task_sources

        return await self._add_secondary(agent_task_sources, "agent_task_id", task_id, "source_id", source_ids)

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

    @classmethod
    def resolve_cli_date_window(cls, primary: Any, fallback: Any = None) -> tuple[Any, Any]:
        """Resolve optional date bounds and reject malformed or reversed windows."""
        dates = primary if primary not in (None, "", {}) else fallback
        if dates in (None, "", {}):
            dates = {}
        if not isinstance(dates, dict):
            raise ValueError("Invalid content date window")
        start_raw = dates.get("start_date")
        end_raw = dates.get("end_date")
        if any(value not in (None, "") and not isinstance(value, str) for value in (start_raw, end_raw)):
            raise ValueError("Invalid content date window")
        start = cls.parse_date(start_raw)
        end = cls.parse_date(end_raw)
        if (start_raw and start is None) or (end_raw and end is None):
            raise ValueError("Invalid content date window")
        if start is not None and end is not None and start > end:
            raise ValueError("Invalid content date window")
        return start, end

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
    def build_selection_payload(
        *,
        tracked_users: Any = None,
        keywords: Any = None,
        topics: Any = None,
        keywords_match: str | None = None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge a task's selection into its payload (`docs/design/task-selection.md`).

        The one writer for every surface — web form, CLI, chat tool and API —
        so the same settings cannot be stored three different ways. `None` means
        "not supplied, keep what is there"; an empty list/string clears the key.

        Malformed entries raise `SelectionError` so a save fails visibly instead
        of storing a selection that silently selects nothing.
        """
        import app.services.monitoring.selection as selection

        payload = dict(existing or {})
        if tracked_users is not None:
            # Validate through the same parser the runtime uses.
            payload["tracked_users"] = [user.key() for user in selection.parse_tracked_users(tracked_users)]
            if not payload["tracked_users"]:
                payload.pop("tracked_users")
        for raw, key in ((keywords, "keywords_list"), (topics, "topics_list")):
            if raw is None:
                continue
            values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            phrases = [" ".join(str(value).split()) for value in values if value and str(value).strip()]
            if phrases:
                payload[key] = phrases
            else:
                payload.pop(key, None)
        if keywords_match is not None:
            if keywords_match not in selection.MATCH_MODES:
                raise selection.SelectionError("keywords_match_invalid")
            if keywords_match == selection.MATCH_ANY:
                # "any" is the default the reader applies: not storing it keeps
                # the payload (and its analysis fingerprint) stable.
                payload.pop("keywords_match", None)
            else:
                payload["keywords_match"] = keywords_match
        selection.build_scope(payload)
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
        """Compute next run time for cron expression in the given timezone (UTC returned).

        Delegates to the shared helper so `@once` and 5-field cron behave the
        same in the web form, the API, the agent tools and the runner.
        """
        from app.tasks.cron import next_run_at as compute_next_run

        return compute_next_run(cron_expr, timezone_name, after=after)

    async def get_active(self) -> list["AgentTask"]:
        """All active tasks."""
        return await self.filter(is_active=True)

    async def upcoming_effective(
        self, *, now: datetime, include_source_based: bool, limit: int = 3
    ) -> list["AgentTask"]:
        """Return the earliest schedules that the scheduler could actually run.

        Eligibility is evaluated in SQL before limiting, so inactive scenarios,
        paused tasks and source based tasks without an active source cannot crowd
        permitted schedules out of the home preview. Callers without source.view
        must pass ``include_source_based=False``; that path issues no source query.
        """
        from sqlalchemy import and_, exists, literal, not_, or_, select

        from ..agent_scenario import AgentScenario
        from ..agent_task import AgentTask, agent_task_sources
        from ..source import Source

        active_scenario = exists(
            select(literal(1)).where(
                AgentScenario.id == AgentTask.agent_scenario_id,
                AgentScenario.tenant_id == AgentTask.tenant_id,
                AgentScenario.is_active.is_(True),
            )
        )
        eligibility = [
            AgentTask.is_active.is_(True),
            AgentTask.next_run_at.is_not(None),
            or_(AgentTask.agent_scenario_id.is_(None), active_scenario),
        ]

        source_types = tuple(sorted(SOURCE_BASED_JOB_TYPES))
        if include_source_based:
            linked_source = exists(
                select(literal(1))
                .select_from(agent_task_sources.join(Source, Source.id == agent_task_sources.c.source_id))
                .where(
                    agent_task_sources.c.agent_task_id == AgentTask.id,
                    Source.tenant_id == AgentTask.tenant_id,
                    Source.is_active.is_(True),
                )
            )
            any_link = exists(select(literal(1)).where(agent_task_sources.c.agent_task_id == AgentTask.id))
            workspace_source = exists(
                select(literal(1)).where(
                    Source.tenant_id == AgentTask.tenant_id,
                    Source.is_active.is_(True),
                )
            )
            source_task_eligible = or_(linked_source, and_(not_(any_link), workspace_source))
            eligibility.append(or_(AgentTask.job_type.not_in(source_types), source_task_eligible))
        else:
            eligibility.append(AgentTask.job_type.not_in(source_types))

        return await self.filter(*eligibility).order_by(AgentTask.next_run_at.asc(), AgentTask.id.asc()).limit(limit)

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

    async def get_source_scenario(self, source_id: int) -> Optional["AgentScenario"]:
        """The analysis scenario a taskless analyze run should use for `source_id`.

        A staging-triggered analyze has no task of its own, so it used to fall
        straight to the tenant default scenario. When a task explicitly links
        the source and pins a scenario, that task owns the source's analysis and
        its scenario (model choice) must win over the workspace default. Where
        several tasks link the same source with different scenarios, the choice
        is ambiguous — keep the tenant-default fallback.

        Returns the pinned scenario, or None when no task with an explicit
        scenario links the source (caller falls back to the tenant default).
        """
        from app.models import AgentScenario

        if type(source_id) is not int or source_id <= 0:
            return None
        from sqlalchemy import select

        from ..agent_task import agent_task_sources

        rows = await self.aggregate_rows(
            select(agent_task_sources.c.agent_task_id).where(agent_task_sources.c.source_id == source_id)
        )
        if not rows:
            return None
        task_ids = {int(row[0]) for row in rows}
        tasks = await self.filter(id__in=list(task_ids)).prefetch_related("agent_scenario")
        scenarios = {
            task.agent_scenario
            for task in tasks
            if task.agent_scenario is not None and task.agent_scenario.is_active
        }
        if len(scenarios) == 1:
            return next(iter(scenarios))
        return None

    async def for_source(
        self,
        source_id: int,
        tenant_id: int,
        *,
        source_is_active: bool = True,
        include_all_active: bool = True,
    ) -> list["AgentTask"]:
        """Tasks that schedule analysis for one source in an explicit tenant.

        Explicitly linked tasks are included even when paused. Tasks with no
        source links act on all active sources, so they apply only while this
        source is active. Callers may disable that fallback when resolving the
        historic explicit-link-first source window.
        """
        from sqlalchemy import exists, literal, not_, or_, select

        from ..agent_task import AgentTask, agent_task_sources

        linked = exists(
            select(literal(1)).where(
                agent_task_sources.c.agent_task_id == AgentTask.id,
                agent_task_sources.c.source_id == source_id,
            )
        )
        criteria = [
            AgentTask.tenant_id == tenant_id,
            AgentTask.job_type.in_(tuple(SOURCE_BASED_JOB_TYPES)),
        ]
        if include_all_active and source_is_active:
            any_link = exists(select(literal(1)).where(agent_task_sources.c.agent_task_id == AgentTask.id))
            criteria.append(or_(linked, not_(any_link)))
        else:
            criteria.append(linked)
        return await (
            self.filter(*criteria)
            .prefetch_related("agent_scenario", "sources")
            .order_by(AgentTask.created_at.desc(), AgentTask.id.desc())
        )

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

    async def _admit_task_run(
        self,
        task_id: int,
        *,
        now: datetime,
        expected_next_run_at: Optional[datetime] = None,
        scheduled: bool = False,
        initiated_by_user_id: Optional[int] = None,
        timezone_name: Optional[str] = None,
        extra_payload: Optional[dict[str, Any]] = None,
        retry_exhausted: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Internal trigger boundary: insert and schedule advancement commit together.

        Scheduled contenders match their observed due timestamp and skip locked
        tasks. Manual runs share the row lock so a stale snapshot cannot roll
        the schedule backwards. Source-work conflicts return a busy receipt
        without recording a trigger or consuming the scheduled occurrence.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.core.config import settings
        from app.core.database import async_session_maker
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
        from app.tasks.cron import next_run_at

        from ..agent_task import AgentTask
        from ..source import Source
        from .job_manager import JobManager

        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0 or is_bypass():
            raise TenantContextError("Task admission requires a concrete normal tenant scope")
        from app.core.permissions import get_current_user

        trusted_actor_id = getattr(get_current_user(), "id", None)
        if scheduled:
            initiated_by_user_id = None
        elif initiated_by_user_id is not None and initiated_by_user_id != trusted_actor_id:
            raise ValueError("Manual task initiator must match the authenticated actor")
        elif type(trusted_actor_id) is int and trusted_actor_id > 0:
            initiated_by_user_id = trusted_actor_id
        if type(task_id) is not int or task_id <= 0:
            raise ValueError("Task admission requires a persisted task ID")
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("Task admission requires an aware timestamp")
        if scheduled and (not isinstance(expected_next_run_at, datetime) or expected_next_run_at.utcoffset() is None):
            raise ValueError("Scheduled admission requires an aware observed due timestamp")
        async with async_session_maker() as session:
            async with _RetryAdmissionTransaction(session) as retry_transaction:
                # One lock order with direct source admission and staging:
                # workspace admission gate, then task/staged rows.
                await JobManager.lock_source_admission(session, tenant_id)
                query = select(AgentTask).where(AgentTask.id == task_id, AgentTask.tenant_id == tenant_id)
                if scheduled:
                    initiated_by_user_id = None
                    query = query.where(
                        AgentTask.is_active.is_(True),
                        AgentTask.next_run_at == expected_next_run_at,
                        AgentTask.next_run_at <= now,
                    )
                query = query.options(selectinload(AgentTask.sources), selectinload(AgentTask.agent_scenario))
                row = (await session.execute(query.with_for_update(skip_locked=scheduled))).scalar_one_or_none()
                if row is None:
                    return None
                if not scheduled:
                    self._require_job_retention_permission(row.job_type)
                self._validate_job_retention_schedule(row.job_type, row.payload, row.cron_expr)
                if scheduled:
                    active_source_ids = set()
                    if self.requires_sources(row.job_type) and not row.sources:
                        active_source_ids = set(
                            (
                                await session.execute(
                                    select(Source.id).where(Source.tenant_id == tenant_id, Source.is_active.is_(True))
                                )
                            )
                            .scalars()
                            .all()
                        )
                    if not self._task_effectively_active(row, active_source_ids):
                        return None
                once = row.cron_expr == "@once"
                schedule_error = None
                if scheduled and not once:
                    try:
                        next_fire = next_run_at(row.cron_expr, timezone_name or settings.SCHEDULER_TIMEZONE, after=now)
                    except (ValueError, KeyError) as error:
                        schedule_error = str(error)
                        next_fire = now
                else:
                    next_fire = None if once else row.next_run_at
                if schedule_error is not None:
                    receipt = {"status": "invalid_schedule", "task_id": row.id}
                    row.last_status = "failed"
                    row.last_error = schedule_error
                else:
                    payload = dict(row.payload or {})
                    if extra_payload:
                        payload.update({key: value for key, value in extra_payload.items() if value is not None})
                    retry_reset_count = 0
                    if retry_exhausted:
                        if row.job_type != "analyze":
                            raise ValueError("Exhausted staged content can only be retried by an analysis task")
                        from ..collected_item import CollectedItem
                        from ..source import Source
                        from .collected_item_manager import CollectedItemManager

                        source_rows = list(row.sources or [])
                        if source_rows:
                            source_rows = [source for source in source_rows if source.tenant_id == tenant_id]
                        else:
                            source_rows = list(
                                (
                                    await session.execute(
                                        select(Source).where(
                                            Source.tenant_id == tenant_id,
                                            Source.is_active.is_(True),
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                        source_ids = list(dict.fromkeys(source.id for source in source_rows))
                        payload["source_ids"] = source_ids
                        source_windows = []
                        task_dates = payload.get("cli_dates")
                        for source in source_rows:
                            source_params = source.params if isinstance(source.params, dict) else {}
                            start_date, end_date = self.resolve_cli_date_window(
                                task_dates, source_params.get("cli_dates")
                            )
                            source_windows.append((source, start_date, end_date))
                        staged_exists = False
                        for source, start_date, end_date in source_windows:
                            conditions = [CollectedItem.tenant_id == tenant_id, CollectedItem.source_id == source.id]
                            if start_date is not None:
                                conditions.append(CollectedItem.published_at >= start_date)
                            if end_date is not None:
                                conditions.append(CollectedItem.published_at <= end_date)
                            exists = await session.execute(
                                select(CollectedItem.id).where(*conditions).limit(1).with_for_update()
                            )
                            if exists.scalar_one_or_none() is None:
                                continue
                            staged_exists = True
                            retry_reset_count += await CollectedItemManager().reset_attempts(
                                session, source.id, start_date=start_date, end_date=end_date
                            )
                        if not staged_exists:
                            return {"status": "no_staged_content", "task_id": row.id, "reset_count": 0}
                    from app.jobs.source_activity import SOURCE_JOB_TYPES, SourceJobBusy

                    if row.job_type in SOURCE_JOB_TYPES:
                        # A future replay must use this admission-time scope,
                        # even if the reusable task is edited or deleted later.
                        linked_sources = list(row.sources or [])
                        if any(source.tenant_id != tenant_id for source in linked_sources):
                            raise TenantContextError("Task source snapshot contains a foreign workspace source")
                        source_rows = linked_sources
                        if not source_rows:
                            source_rows = list(
                                (
                                    await session.execute(
                                        select(Source).where(
                                            Source.tenant_id == tenant_id,
                                            Source.is_active.is_(True),
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                        source_ids = list(dict.fromkeys(source.id for source in source_rows))
                        if not source_ids:
                            raise ValueError("Source task has no effective sources to snapshot")
                        payload["source_ids"] = source_ids
                        trigger_type = getattr(row.trigger_type, "name", None)
                        action_type = getattr(row.action_type, "name", None)
                        payload["_effective_snapshot"] = {
                            "version": 1,
                            "task_payload": dict(payload),
                            "scenario_id": row.agent_scenario_id or payload.get("scenario_id"),
                            "trigger_type": trigger_type,
                            "trigger_config": dict(row.trigger_config or {}) if row.trigger_config else None,
                            "action_type": action_type,
                            "blacklist": list(row.blacklist or []),
                            "whitelist": list(row.whitelist or []),
                            "rate_limit_per_hour": row.rate_limit_per_hour,
                            "cooldown_seconds": row.cooldown_seconds,
                        }

                    jobs = JobManager()
                    admit = jobs.admit_source_job if row.job_type in SOURCE_JOB_TYPES else jobs.enqueue
                    try:
                        job = await admit(
                            job_type=row.job_type,
                            payload=payload,
                            agent_task_id=row.id,
                            tenant_id=tenant_id,
                            initiated_by_user_id=initiated_by_user_id,
                            run_at=now,
                            session=session,
                        )
                    except SourceJobBusy as busy:
                        # No trigger/schedule/once-state writes: the existing
                        # job may belong to a different task or methodology.
                        # A retry may already have reset staged attempt counts;
                        # let the transaction roll back before returning a receipt.
                        if retry_exhausted:
                            raise _RetryAdmissionBusy(busy.job) from busy
                        return _busy_admission_receipt(row.id, busy.job)
                    row.last_status = "queued"
                    row.last_error = None
                    if once:
                        row.is_active = False
                    receipt = {
                        "status": "enqueued",
                        "task_id": row.id,
                        "job": job,
                        "once": once,
                        "retry_reset_count": retry_reset_count,
                    }
                row.last_run_at = now
                row.next_run_at = next_fire
                await session.flush()
            if retry_transaction.busy_job is not None:
                return _busy_admission_receipt(task_id, retry_transaction.busy_job)
            # Return only after the owning transaction's commit acknowledgement.
        return receipt

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
        from .base_manager import BaseManager

        await BaseManager.update_by_id(
            self,
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
        from .base_manager import BaseManager

        await BaseManager.update_by_id(
            self,
            task_id,
            last_run_at=datetime.now(timezone.utc),
            last_status=status,
            last_error=error,
        )
