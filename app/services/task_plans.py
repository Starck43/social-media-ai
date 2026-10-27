"""Typed, side-effect-free normalization for social task requests.

Surfaces may collect inputs differently, but they store the same plan. Resource
relationships remain on AgentTask and are replaced in that manager's existing
transaction; this module never reads or writes the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.collection.intent import (
    COLLECTION_ERROR_TEXT,
    CollectionIntent,
    collection_block,
    parse_collection_intent,
)
from app.services.social.vk_access import VKSelection, VKSelectionError, parse_vk_selection

TaskSourceScope = Literal["selected", "discovery", "all"]
TaskWindowType = Literal["fixed", "rolling"]
TaskTriggerType = Literal["once", "schedule"]


_TASK_PLAN_ERROR_CODES = frozenset(
    {
        "task_plan_collection_invalid",
        "task_plan_discovery_queries_required",
        "task_plan_fixed_start_required",
        "task_plan_goal_invalid",
        "task_plan_invalid",
        "task_plan_override_changes_persisted_scope",
        "task_plan_override_requires_persisted_plan",
        "task_plan_payload_invalid",
        "task_plan_platform_unsupported",
        "task_plan_platforms_invalid",
        "task_plan_query_limit_exceeded",
        "task_plan_requires_collect_or_analyze",
        "task_plan_rolling_days_invalid",
        "task_plan_run_time_invalid",
        "task_plan_scenario_invalid",
        "task_plan_schedule_cron_invalid",
        "task_plan_schedule_requires_recurring_cron",
        "task_plan_selected_sources_required",
        "task_plan_source_ids_invalid",
        "task_plan_source_ids_scope_conflict",
        "task_plan_source_ids_too_many",
        "task_plan_source_scope_invalid",
        "task_plan_timezone_invalid",
        "task_plan_trigger_invalid",
        "task_plan_version_unsupported",
        "task_plan_vk_access_clear_requires_explicit_selection",
        "task_plan_vk_access_invalid",
        "task_plan_vk_access_requires_vk",
        "task_plan_vk_access_retention_required",
        "task_plan_window_collection_mismatch",
        "task_plan_window_invalid",
        "task_plan_window_reversed",
        "task_plan_window_type_invalid",
    }
)


class TaskPlanError(ValueError):
    """A task request cannot be represented safely as a canonical plan."""

    def __init__(self, message: str) -> None:
        self.code = (
            message if message in _TASK_PLAN_ERROR_CODES or message in COLLECTION_ERROR_TEXT else "task_plan_invalid"
        )
        super().__init__(message)


@dataclass(frozen=True)
class TaskWindow:
    type: TaskWindowType
    start: date | None = None
    end: date | None = None
    days: int | None = None


@dataclass(frozen=True)
class TaskTrigger:
    type: TaskTriggerType
    cron: str | None = None
    timezone: str | None = None


@dataclass(frozen=True)
class TaskPlan:
    """A normalized user-authored plan, before source/permission resolution."""

    goal: str | None
    platforms: tuple[str, ...]
    source_scope: TaskSourceScope
    source_ids: tuple[int, ...]
    collection: CollectionIntent
    window: TaskWindow | None
    scenario_id: int | None
    trigger: TaskTrigger
    vk_access: VKSelection | None = None


_PLATFORMS = frozenset({"vk", "telegram"})
_MAX_PLAN_PLATFORMS = 2
_MAX_PLAN_QUERIES = 5
_WINDOW_DAYS_MAX = 365
_COLLECTING_JOB_TYPES = frozenset({"collect", "analyze"})


def parse_task_plan(raw: Mapping[str, Any], *, legacy: bool = False) -> TaskPlan:
    """Validate one plan using stable, bounded fields and the collection parser.

    Stored legacy tasks have no plan block and should bypass this parser. The
    flag is retained for call-site compatibility but never weakens validation;
    callers must not fabricate a plan to adapt historical semantics.
    """
    if not isinstance(raw, Mapping):
        raise TaskPlanError("task_plan_invalid")
    version = raw.get("version", 1)
    if type(version) is not int or version != 1:
        raise TaskPlanError("task_plan_version_unsupported")

    goal = raw.get("goal")
    if goal is not None:
        if not isinstance(goal, str):
            raise TaskPlanError("task_plan_goal_invalid")
        goal = goal.strip()
        if not goal or len(goal) > 2_000:
            raise TaskPlanError("task_plan_goal_invalid")

    platforms_raw = raw.get("platforms", ["vk"])
    if not isinstance(platforms_raw, (list, tuple)) or not platforms_raw or len(platforms_raw) > _MAX_PLAN_PLATFORMS:
        raise TaskPlanError("task_plan_platforms_invalid")
    platforms: list[str] = []
    for platform in platforms_raw:
        if not isinstance(platform, str) or platform.strip().lower() not in _PLATFORMS:
            raise TaskPlanError("task_plan_platform_unsupported")
        normalized = platform.strip().lower()
        if normalized not in platforms:
            platforms.append(normalized)

    source_scope = raw.get("source_scope")
    if not isinstance(source_scope, str) or source_scope not in {"selected", "discovery", "all"}:
        raise TaskPlanError("task_plan_source_scope_invalid")
    source_ids_raw = raw.get("source_ids", [])
    if not isinstance(source_ids_raw, (list, tuple)):
        raise TaskPlanError("task_plan_source_ids_invalid")
    source_ids: list[int] = []
    for source_id in source_ids_raw:
        if type(source_id) is not int or source_id <= 0:
            raise TaskPlanError("task_plan_source_ids_invalid")
        if source_id not in source_ids:
            source_ids.append(source_id)
        if len(source_ids) > 500:
            raise TaskPlanError("task_plan_source_ids_too_many")
    if source_scope == "selected" and not source_ids:
        raise TaskPlanError("task_plan_selected_sources_required")
    if source_scope != "selected" and source_ids:
        raise TaskPlanError("task_plan_source_ids_scope_conflict")

    collection_raw = raw.get("collection", {})
    if not isinstance(collection_raw, Mapping):
        raise TaskPlanError("task_plan_collection_invalid")
    collection_input = dict(collection_raw)
    window_input = raw.get("window")
    if isinstance(window_input, Mapping) and window_input.get("type") == "fixed":
        # The top-level window is authoritative; keep the legacy collection
        # date fields synchronized for existing collectors.
        existing_window = collection_input.get("date_window")
        expected_window = {"start": window_input.get("start"), "end": window_input.get("end")}
        if existing_window is None:
            collection_input["date_window"] = expected_window
        elif existing_window != expected_window:
            raise TaskPlanError("task_plan_window_collection_mismatch")
    try:
        collection = parse_collection_intent({"collection": collection_input})
    except (TypeError, ValueError) as error:
        raise TaskPlanError(str(error)) from error
    if len(collection.queries) > _MAX_PLAN_QUERIES or len(collection.discovery.queries) > _MAX_PLAN_QUERIES:
        raise TaskPlanError("task_plan_query_limit_exceeded")

    window = _parse_window(raw.get("window"), collection)
    if source_scope == "discovery" and not (collection.discovery.queries or collection.queries):
        raise TaskPlanError("task_plan_discovery_queries_required")

    scenario_id = raw.get("scenario_id")
    if scenario_id is not None and (type(scenario_id) is not int or scenario_id <= 0):
        raise TaskPlanError("task_plan_scenario_invalid")

    trigger = _parse_trigger(raw.get("trigger"))
    if trigger.type == "schedule" and trigger.cron == "@once":
        raise TaskPlanError("task_plan_schedule_requires_recurring_cron")
    vk_access_raw = raw.get("vk_access")
    if vk_access_raw is not None and "vk" not in platforms:
        raise TaskPlanError("task_plan_vk_access_requires_vk")
    try:
        vk_access = parse_vk_selection(vk_access_raw) if vk_access_raw is not None else None
    except (VKSelectionError, TypeError) as error:
        # Policy errors use fixed codes; never expose caller-supplied token or
        # metadata text through task-plan errors.
        raise TaskPlanError("task_plan_vk_access_invalid") from error
    return TaskPlan(
        goal=goal,
        platforms=tuple(platforms),
        source_scope=source_scope,
        source_ids=tuple(source_ids),
        collection=collection,
        window=window,
        scenario_id=scenario_id,
        trigger=trigger,
        vk_access=vk_access,
    )


def read_saved_task_plan(payload: Any) -> TaskPlan | None:
    """Read a persisted versioned plan without fabricating one for legacy rows."""
    if not isinstance(payload, Mapping):
        return None
    if "task_plan" not in payload:
        return None
    return parse_task_plan(payload["task_plan"])


def task_plan_block(plan: TaskPlan) -> dict[str, Any]:
    """Serialize the canonical plan without DB identities for discovered rows."""
    block: dict[str, Any] = {
        "version": 1,
        "platforms": list(plan.platforms),
        "source_scope": plan.source_scope,
        "source_ids": list(plan.source_ids),
        "collection": collection_block(plan.collection),
        "trigger": {"type": plan.trigger.type},
    }
    if plan.goal is not None:
        block["goal"] = plan.goal
    if plan.window is not None:
        window: dict[str, Any] = {"type": plan.window.type}
        if plan.window.start is not None:
            window["start"] = plan.window.start.isoformat()
        if plan.window.end is not None:
            window["end"] = plan.window.end.isoformat()
        if plan.window.days is not None:
            window["days"] = plan.window.days
        block["window"] = window
    if plan.scenario_id is not None:
        block["scenario_id"] = plan.scenario_id
    if plan.vk_access is not None:
        block["vk_access"] = plan.vk_access.to_dict()
    if plan.trigger.type == "schedule":
        block["trigger"].update(cron=plan.trigger.cron, timezone=plan.trigger.timezone)
    return block


def apply_task_plan(
    payload: Mapping[str, Any] | None,
    raw: Mapping[str, Any] | None,
    *,
    job_type: str,
) -> dict[str, Any]:
    """Return payload with plan, collection and absolute dates synchronized.

    This function doesn't alter AgentTask source/scenario relationships. Callers
    must replace those in the same existing manager transaction. ``None`` is an
    explicit clear operation for a plan created by this contract.
    """
    if raw is None:
        result = dict(payload or {})
        for key in ("task_plan", "collection", "cli_dates", "scenario_id"):
            result.pop(key, None)
        return result
    if not isinstance(raw, Mapping):
        raise TaskPlanError("task_plan_invalid")
    existing_raw = (payload or {}).get("task_plan")
    if isinstance(existing_raw, Mapping) and existing_raw.get("vk_access") is not None:
        if "vk_access" not in raw:
            raw = {**raw, "vk_access": existing_raw["vk_access"]}
        elif raw["vk_access"] is None:
            raise TaskPlanError("task_plan_vk_access_clear_requires_explicit_selection")
    return apply_parsed_task_plan(payload, parse_task_plan(raw), job_type=job_type)


def validate_vk_access_retained(
    existing_payload: Mapping[str, Any] | None,
    candidate_payload: Mapping[str, Any] | None,
    *,
    job_type: str,
) -> None:
    """Reject ordinary collecting-task writes that silently drop a VK pin.

    A caller may intentionally select a different valid connection in the
    candidate plan. Non-collecting transitions may retire collection plans.
    """
    if job_type not in _COLLECTING_JOB_TYPES:
        return
    existing_plan = (existing_payload or {}).get("task_plan")
    if not isinstance(existing_plan, Mapping) or existing_plan.get("vk_access") is None:
        return
    candidate_plan = (candidate_payload or {}).get("task_plan")
    if not isinstance(candidate_plan, Mapping) or candidate_plan.get("vk_access") is None:
        raise TaskPlanError("task_plan_vk_access_retention_required")


def apply_parsed_task_plan(payload: Mapping[str, Any] | None, plan: TaskPlan, *, job_type: str) -> dict[str, Any]:
    """Project an already parsed plan; persistence/execution still validate it."""
    if job_type not in _COLLECTING_JOB_TYPES:
        raise TaskPlanError("task_plan_requires_collect_or_analyze")
    result = dict(payload or {})
    result["task_plan"] = task_plan_block(plan)
    result["collection"] = collection_block(plan.collection)
    if plan.window is not None and plan.window.type == "fixed":
        result["cli_dates"] = {
            "start_date": plan.window.start.isoformat() if plan.window.start else None,
            "end_date": plan.window.end.isoformat() if plan.window.end else None,
        }
    else:
        result.pop("cli_dates", None)
    if plan.scenario_id is not None:
        result["scenario_id"] = plan.scenario_id
    else:
        result.pop("scenario_id", None)
    return result


@dataclass(frozen=True)
class TaskPlanUpdate:
    """Pure write policy result; adapters retain their transactions and guards."""

    changes: dict[str, Any]
    plan: TaskPlan | None
    cleared: bool = False


def prepare_task_plan_update(
    existing_payload: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
    *,
    job_type: str,
) -> TaskPlanUpdate:
    """One API/CLI/chat policy for replacement, preservation and type transitions.

    Inputs use persisted field names. ``task_plan`` is the explicit envelope;
    collection/start_date/end_date are input-only conflict markers. No DB,
    relationship mutation, actor, payer, permission or schedule execution occurs.
    """
    changes = dict(updates)
    supplied = "task_plan" in changes
    raw = changes.pop("task_plan", None)
    existing = dict(existing_payload or {})
    stored = existing.get("task_plan")
    candidate_raw = changes.get("payload") if "payload" in changes else existing
    if candidate_raw is not None and not isinstance(candidate_raw, Mapping):
        raise TaskPlanError("task_plan_payload_invalid")
    candidate = dict(candidate_raw or {})
    markers = {key: changes.pop(key) for key in ("collection", "start_date", "end_date") if key in changes}
    if not supplied and stored is None:
        if "task_plan" in candidate:
            raise TaskPlanError("Use the task_plan field to introduce a planned task")
        return TaskPlanUpdate(changes, None)
    if not supplied and job_type not in _COLLECTING_JOB_TYPES:
        if job_type not in {"digest", "prune", "learn", "reflect"}:
            raise TaskPlanError("task_plan_requires_collect_or_analyze")
        # Preserve unrelated digest/settings payload; retire only plan-owned
        # projections and the stale FK (an explicit new scenario remains explicit).
        changes["payload"] = apply_task_plan(candidate, None, job_type=job_type)
        changes.setdefault("agent_scenario_id", None)
        return TaskPlanUpdate(changes, None, cleared=True)
    if supplied and raw is None:
        raise TaskPlanError("task_plan cannot be null; omit it to preserve the plan")
    if supplied and isinstance(raw, Mapping) and isinstance(stored, Mapping) and stored.get("vk_access") is not None:
        if "vk_access" not in raw:
            # An ordinary task edit must not drop a previously pinned connection.
            raw = {**raw, "vk_access": stored["vk_access"]}
        elif raw["vk_access"] is None:
            raise TaskPlanError("task_plan_vk_access_clear_requires_explicit_selection")
    plan = parse_task_plan(raw if supplied else stored)
    canonical = apply_parsed_task_plan({}, plan, job_type=job_type)
    expected = {
        "source_ids": list(plan.source_ids),
        "agent_scenario_id": plan.scenario_id,
        "cron_expr": "@once" if plan.trigger.type == "once" else plan.trigger.cron,
    }
    if "source_ids" in changes and (
        not isinstance(changes["source_ids"], list)
        or any(type(value) is not int or value <= 0 for value in changes["source_ids"])
    ):
        raise TaskPlanError("task_plan_source_ids_invalid")
    for key, value in expected.items():
        if key in changes and changes[key] != value:
            raise TaskPlanError(f"{key} conflicts with task_plan; provide matching values or a complete task_plan")
    if markers:
        raise TaskPlanError("Collection and dates belong to task_plan; omit conflicting legacy fields")
    if not supplied:
        for key in ("task_plan", "collection", "cli_dates", "scenario_id"):
            if key in candidate and candidate[key] != canonical.get(key):
                raise TaskPlanError(f"Use the task_plan field to change a planned task's {key}")
    changes["payload"] = apply_parsed_task_plan(candidate, plan, job_type=job_type)
    if supplied:
        changes.update(expected)
    return TaskPlanUpdate(changes, plan)


def _parse_window(raw: Any, collection: CollectionIntent) -> TaskWindow | None:
    if raw is None:
        # Existing date-window input remains available through the canonical
        # collection block. New unqualified research defaults to a recent
        # one-day window instead of draining unbounded history.
        if collection.start_date is None and collection.end_date is None:
            return TaskWindow("rolling", days=1)
        if collection.start_date is None:
            raise TaskPlanError("task_plan_fixed_start_required")
        return TaskWindow("fixed", collection.start_date, collection.end_date)
    if not isinstance(raw, Mapping):
        raise TaskPlanError("task_plan_window_invalid")
    window_type = raw.get("type")
    if window_type == "fixed":
        start = _parse_iso_date(raw.get("start"), "start")
        end = _parse_iso_date(raw.get("end"), "end")
        if start is None:
            raise TaskPlanError("task_plan_fixed_start_required")
        if start is not None and end is not None and start > end:
            raise TaskPlanError("task_plan_window_reversed")
        if collection.start_date != start or collection.end_date != end:
            raise TaskPlanError("task_plan_window_collection_mismatch")
        return TaskWindow("fixed", start, end)
    if window_type == "rolling":
        days = raw.get("days")
        if type(days) is not int or not 1 <= days <= _WINDOW_DAYS_MAX:
            raise TaskPlanError("task_plan_rolling_days_invalid")
        if collection.start_date is not None or collection.end_date is not None:
            raise TaskPlanError("task_plan_window_collection_mismatch")
        return TaskWindow("rolling", days=days)
    raise TaskPlanError("task_plan_window_type_invalid")


def _parse_iso_date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskPlanError(f"task_plan_window_{field}_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise TaskPlanError(f"task_plan_window_{field}_invalid") from error


def _parse_trigger(raw: Any) -> TaskTrigger:
    if raw is None:
        return TaskTrigger("once")
    if not isinstance(raw, Mapping):
        raise TaskPlanError("task_plan_trigger_invalid")
    kind = raw.get("type")
    if kind == "once":
        if raw.get("cron") not in (None, "@once"):
            raise TaskPlanError("task_plan_trigger_invalid")
        return TaskTrigger("once")
    if kind != "schedule":
        raise TaskPlanError("task_plan_trigger_invalid")
    cron = raw.get("cron")
    if isinstance(cron, str) and cron.strip() == "@once":
        raise TaskPlanError("task_plan_schedule_requires_recurring_cron")
    if not isinstance(cron, str) or not cron.strip():
        raise TaskPlanError("task_plan_schedule_cron_invalid")
    timezone = raw.get("timezone")
    if timezone is not None and (not isinstance(timezone, str) or not timezone.strip()):
        raise TaskPlanError("task_plan_timezone_invalid")
    if timezone is not None:
        timezone = timezone.strip()
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise TaskPlanError("task_plan_timezone_invalid") from error
    return TaskTrigger("schedule", cron.strip(), timezone)


def task_plan_timezone(raw: Mapping[str, Any] | None, fallback: str) -> str:
    """Return a scheduled plan's explicit IANA zone, or preserve legacy fallback."""
    if raw is None:
        return fallback
    return parse_task_plan(raw).trigger.timezone or fallback


def apply_task_plan_override(payload: Mapping[str, Any], raw: Mapping[str, Any], *, job_type: str) -> dict[str, Any]:
    """Normalize a per-run plan while keeping its persisted execution identity.

    A run may refine its collection filters/window, but cannot change platforms,
    source authority, selected IDs, scenario, or schedule. Source relationship
    changes belong to the task save transaction, not an invocation payload.
    """
    existing_raw = payload.get("task_plan")
    if not isinstance(existing_raw, Mapping):
        raise TaskPlanError("task_plan_override_requires_persisted_plan")
    existing = parse_task_plan(existing_raw)
    if not isinstance(raw, Mapping):
        raise TaskPlanError("task_plan_invalid")
    if "vk_access" not in raw and existing.vk_access is not None:
        # A per-run override may refine collection details, but omission is not
        # a request to drop or replace the task's saved connection.
        raw = {**raw, "vk_access": existing.vk_access.to_dict()}
    proposed = parse_task_plan(raw)
    fixed_fields = ("platforms", "source_scope", "source_ids", "scenario_id", "trigger", "vk_access")
    if any(getattr(existing, field) != getattr(proposed, field) for field in fixed_fields):
        raise TaskPlanError("task_plan_override_changes_persisted_scope")
    return apply_task_plan(payload, raw, job_type=job_type)


def materialize_task_plan_window(payload: Mapping[str, Any], now: Any) -> dict[str, Any]:
    """Resolve a rolling window once for this run, preserving replay bounds."""
    from datetime import datetime, timedelta, timezone

    result = dict(payload)
    raw = result.get("task_plan")
    if not isinstance(raw, Mapping):
        return result
    window = raw.get("window")
    if not isinstance(window, Mapping) or window.get("type") != "rolling":
        return result
    days = window.get("days")
    if type(days) is not int or not 1 <= days <= _WINDOW_DAYS_MAX:
        raise TaskPlanError("task_plan_rolling_days_invalid")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TaskPlanError("task_plan_run_time_invalid")
    end = now.astimezone(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    plan = dict(raw)
    plan["window"] = {"type": "fixed", "start": start.isoformat(), "end": end.isoformat()}
    collection = plan.get("collection")
    if not isinstance(collection, Mapping):
        raise TaskPlanError("task_plan_collection_invalid")
    collection_copy = dict(collection)
    collection_copy["date_window"] = {"start": start.isoformat(), "end": end.isoformat()}
    plan["collection"] = collection_copy
    result["task_plan"] = plan
    intent = parse_collection_intent({"collection": collection_copy})
    result["collection"] = collection_block(intent)
    result["cli_dates"] = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    return result
