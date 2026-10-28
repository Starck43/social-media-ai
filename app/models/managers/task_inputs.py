"""Task input validation/projection helpers, independent of persistence ownership."""

from datetime import datetime, timezone
from typing import Any, Optional

from croniter import croniter

# Workspace-level jobs do not need concrete source links or content date bounds.
SOURCE_BASED_JOB_TYPES = frozenset({"collect", "analyze"})


class TaskInputsMixin:
    """Stable AgentTaskManager helper API used by create and update surfaces."""

    @staticmethod
    def _validate_job_retention_schedule(job_type: str | None, payload: Any, cron_expr: str | None) -> None:
        if (
            job_type == "prune"
            and isinstance(payload, dict)
            and payload.get("mode") == "job_history"
            and cron_expr == "@once"
        ):
            raise ValueError("Настройка хранения истории должна быть повторяемой, например ежедневно.")


    @staticmethod
    def _validate_task_plan_binding(job_type: str | None, payload: Any, cron_expr: str | None, scenario_id: int | None):
        """Keep typed plan, persisted scenario and scheduler trigger aligned."""
        from app.services.task_plans import TaskPlanError, apply_parsed_task_plan, read_saved_task_plan

        try:
            plan = read_saved_task_plan(payload)
        except TaskPlanError as error:
            raise ValueError(str(error)) from error
        if plan is None:
            return
        if job_type not in {"collect", "analyze"}:
            raise ValueError("A social task plan requires a collect or analyze task")
        if plan.trigger.type == "schedule" and cron_expr != plan.trigger.cron:
            raise ValueError("Task schedule and task plan schedule must match")
        if plan.trigger.type == "once" and cron_expr != "@once":
            raise ValueError("A one-off task plan requires an @once task")
        if scenario_id != plan.scenario_id:
            raise ValueError("Task plan scenario must match the persisted task scenario")
        canonical = apply_parsed_task_plan({}, plan, job_type=job_type)
        for key in ("collection", "cli_dates"):
            if payload.get(key) != canonical.get(key):
                raise ValueError(f"Task plan and {key} must be stored together in canonical form")


    @staticmethod
    def requires_sources(job_type: str) -> bool:
        """Whether a task of this job type needs at least one active source."""
        return job_type in SOURCE_BASED_JOB_TYPES


    @staticmethod
    def wants_deactivate_after_success(payload: Any) -> bool:
        """Whether this task is a trial: stop after its first successful run.

        The owner decides this when the task is created. A recurring schedule
        that was never confirmed as one-off is a normal task and keeps running;
        only an explicit `deactivate_after_success` in the payload ends it.
        """
        return isinstance(payload, dict) and payload.get("deactivate_after_success") is True


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
        """Merge a task's selection into its payload (`docs/COLLECTION.md#task-selection-contract`).

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

