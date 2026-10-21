"""Turn an existing agent task into task-scoped collection lanes.

A task that collects today points at workspace-level sources: one search
source per phrase, every task of the workspace can read them. The lane
migration moves such a task onto its own lanes — a `Source` row per phrase
that only this task may read — and stores the collection intent in the task
payload through the shared writer.

The module is split in two halves on purpose:

* `plan_lane_migration` is pure: it reads the task and its linked sources and
  returns the plan (lanes, canonical collection block, links, retirements);
* `apply_lane_migration` is the only writer: it creates the missing lane rows,
  relinks the task, writes the payload and retires the legacy sources.

Retiring a legacy source never deletes the row: it keeps the trail of what it
collected, it is only deactivated and unlinked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.services.collection.intent import (
    COLLECTION_JOB_TYPES,
    CollectionIntentError,
    apply_collection,
    collection_error_message,
)
from app.services.collection.planner import QuerySourceSpec, SourceRef, query_source_spec

# The `sources.source_type` value of a workspace-level search source — the only
# kind of source a lane may replace.
SEARCH_SOURCE_TYPE = "search"

# The lane column arrives with migration 0108; without it a lane cannot be
# stored at all, so the migration refuses instead of writing half a schema.
SCHEMA_HINT = "run alembic upgrade head (0108) first"


class LaneMigrationError(RuntimeError):
    """The task cannot be migrated into task-scoped lanes."""


@dataclass(frozen=True)
class LinkedSource:
    """A source already linked to the task, normalised like the planner does."""

    source_id: int
    platform: str
    source_type: str
    external_id: str
    owner_task_id: int | None = None

    @classmethod
    def from_source(cls, source: Any) -> "LinkedSource":
        ref = SourceRef.from_source(source)
        owner = getattr(source, "owner_task_id", None)
        return cls(
            source_id=ref.source_id,
            platform=ref.platform,
            source_type=ref.source_type,
            external_id=ref.external_id,
            owner_task_id=int(owner) if owner is not None else None,
        )


@dataclass(frozen=True)
class LaneMigrationPlan:
    """What the migration would do. Nothing is written while a plan exists."""

    task_id: int
    task_name: str
    tenant_id: int
    platform: str
    lane_specs: list[QuerySourceSpec] = field(default_factory=list)
    collection_block: dict[str, Any] = field(default_factory=dict)
    keep_source_ids: list[int] = field(default_factory=list)
    retire_source_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LaneMigrationResult:
    """What the migration actually changed."""

    task_id: int
    created_source_ids: list[int] = field(default_factory=list)
    existing_source_ids: list[int] = field(default_factory=list)
    linked_source_ids: list[int] = field(default_factory=list)
    retired_source_ids: list[int] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def plan_lane_migration(
    task: Any,
    *,
    queries: list[str],
    content_types: list[str],
    limit: int,
    city: str | None,
    city_mode: str,
    date_from: str | None,
    date_to: str | None,
    retire_source_ids: list[int],
    platform: str | None,
) -> LaneMigrationPlan:
    """Plan the lanes of an existing task. Pure: no database, no writes.

    `task` is the task row with its linked sources (and their platforms)
    loaded — the CLI prefetches them. Refusals are explicit: a task that never
    collects, an empty query list, an unresolvable platform and a retirement
    that would touch another task's lane all raise :class:`LaneMigrationError`.
    """
    task_id = int(task.id)
    job_type = str(getattr(task, "job_type", "") or "")
    if job_type not in COLLECTION_JOB_TYPES:
        raise LaneMigrationError(
            f"task job type '{job_type}' never collects; expected one of {', '.join(COLLECTION_JOB_TYPES)}"
        )

    phrases = _dedupe_phrases(queries)
    if not phrases:
        raise LaneMigrationError("no queries given: a lane needs at least one search phrase")

    linked = [LinkedSource.from_source(source) for source in (getattr(task, "sources", None) or [])]
    resolved_platform, inferred = _resolve_platform(platform, linked)

    warnings: list[str] = []
    if inferred:
        warnings.append(f"platform inferred from the task's linked sources: {resolved_platform}")

    retire_ids = _validate_retirements(task_id, linked=linked, requested=retire_source_ids)

    keep: list[int] = []
    retiring = set(retire_ids)
    for source in linked:
        if source.source_id in retiring:
            continue
        if source.owner_task_id is not None and source.owner_task_id != task_id:
            # Another task's lane is never linked here: it would be collected twice.
            warnings.append(f"source {source.source_id} is the lane of task {source.owner_task_id} and stays unlinked")
            continue
        if source.owner_task_id is None and source.source_type == SEARCH_SOURCE_TYPE:
            warnings.append(
                f"legacy search source {source.source_id} stays linked; "
                f"retire it with --retire-source {source.source_id}"
            )
        keep.append(source.source_id)

    block = _collection_block(
        task,
        phrases=phrases,
        content_types=content_types,
        limit=limit,
        city=city,
        city_mode=city_mode,
        date_from=date_from,
        date_to=date_to,
    )
    lane_specs = [query_source_spec(task_id=task_id, platform=resolved_platform, query=phrase) for phrase in phrases]

    return LaneMigrationPlan(
        task_id=task_id,
        task_name=str(getattr(task, "name", "") or ""),
        tenant_id=int(task.tenant_id),
        platform=resolved_platform,
        lane_specs=lane_specs,
        collection_block=block,
        keep_source_ids=keep,
        retire_source_ids=retire_ids,
        warnings=warnings,
    )


async def apply_lane_migration(
    plan: LaneMigrationPlan,
    *,
    source_ids_by_external_id: Mapping[str, int] | None = None,
) -> LaneMigrationResult:
    """Create the lane rows, relink the task and retire the legacy sources.

    The only writer of the migration. It is idempotent: a lane row is looked up
    by (tenant, platform, external_id) and reused, never duplicated. A retired
    source is deactivated and unlinked, never deleted — the row keeps the
    trail of the items it collected.

    `source_ids_by_external_id` is the lane resolution the caller made before
    planning; it is cross-checked against the database and a disagreement is
    refused (somebody else changed the lane under the plan).
    """
    from app.models import AgentTask, Platform, Source
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.types import SourceType

    try:
        task = await AgentTask.objects.get(id=plan.task_id)
        if task is None:
            raise LaneMigrationError(f"task {plan.task_id} not found in this workspace")
        if task.tenant_id != plan.tenant_id:
            raise LaneMigrationError(f"task {plan.task_id} is not in workspace {plan.tenant_id}")

        platform = await Platform.objects.filter(platform_type=plan.platform).first()
        if platform is None:
            raise LaneMigrationError(f"platform '{plan.platform}' is not configured")

        external_ids = [spec.external_id for spec in plan.lane_specs]
        existing_rows = await Source.objects.filter(
            tenant_id=plan.tenant_id,
            platform_id=platform.id,
            external_id__in=external_ids,
        )
        by_external = {row.external_id: row for row in existing_rows}
        hints = dict(source_ids_by_external_id or {})

        # First pass: resolve every lane. Nothing is written before every lane
        # is known — a refused migration must leave no half-migrated task.
        resolved: list[Any] = []
        for spec in plan.lane_specs:
            row = by_external.get(spec.external_id)
            hinted = hints.get(spec.external_id)
            if row is None and hinted is not None:
                row = await Source.objects.get(id=int(hinted))
                if (
                    row is None
                    or row.external_id != spec.external_id
                    or row.tenant_id != plan.tenant_id
                    or row.platform_id != platform.id
                ):
                    raise LaneMigrationError(f"source {hinted} does not match the lane '{spec.external_id}'")
            if row is not None:
                if hinted is not None and int(hinted) != row.id:
                    raise LaneMigrationError(f"lane '{spec.external_id}' changed under the plan; re-run the plan")
                if row.owner_task_id is not None and row.owner_task_id != plan.task_id:
                    raise LaneMigrationError(f"source {row.id} is the lane of another task")
            resolved.append(row)

        retired = list(plan.retire_source_ids)
        clash = sorted({row.id for row in resolved if row is not None} & set(retired))
        if clash:
            raise LaneMigrationError(f"source(s) {', '.join(map(str, clash))} are retired and used as a lane")

        created: list[int] = []
        reused: list[int] = []
        lane_source_ids: list[int] = []
        for spec, row in zip(plan.lane_specs, resolved):
            if row is not None:
                reused.append(row.id)
                lane_source_ids.append(row.id)
                continue
            source = await Source.objects.create(
                platform_id=platform.id,
                name=spec.name,
                source_type=SourceType.SEARCH,
                external_id=spec.external_id,
                params=dict(spec.params),
                owner_task_id=plan.task_id,
                is_active=True,
            )
            created.append(source.id)
            lane_source_ids.append(source.id)

        link_ids = sorted(set(plan.keep_source_ids) | set(lane_source_ids))
        await AgentTaskManager().set_sources(plan.task_id, link_ids)

        payload = apply_collection(dict(task.payload or {}), plan.collection_block, job_type=task.job_type)
        await AgentTaskManager().update_by_id(plan.task_id, payload=payload)

        for source_id in retired:
            row = await Source.objects.get(id=source_id)
            if row is None or row.tenant_id != plan.tenant_id:
                raise LaneMigrationError(f"source {source_id} is not in this workspace")
            # No hard delete: the row keeps the trail of the items it collected.
            await Source.objects.update_by_id(source_id, is_active=False)

        return LaneMigrationResult(
            task_id=plan.task_id,
            created_source_ids=created,
            existing_source_ids=reused,
            linked_source_ids=link_ids,
            retired_source_ids=retired,
            payload=payload,
        )
    except LaneMigrationError:
        raise
    except (ProgrammingError, DBAPIError) as error:
        guarded = _schema_error(error)
        if guarded is not None:
            raise guarded from error
        raise


def _schema_error(error: Exception) -> LaneMigrationError | None:
    """Translate a missing `sources.owner_task_id` column into an operator hint."""
    if "owner_task_id" in str(error):
        return LaneMigrationError(SCHEMA_HINT)
    return None


def _dedupe_phrases(queries: Sequence[str]) -> list[str]:
    """Phrases as the parser normalises them: one space, case-insensitive key."""
    result: list[str] = []
    seen: set[str] = set()
    for query in queries or []:
        phrase = " ".join(str(query or "").split())
        key = phrase.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(phrase)
    return result


def _resolve_platform(platform: str | None, linked: Sequence[LinkedSource]) -> tuple[str, bool]:
    """The platform of the lanes: the explicit one, else the linked sources'.

    Returns the platform and whether it was inferred. Sources that span several
    platforms do not agree on one: the caller has to say which one the lanes
    belong to.
    """
    explicit = " ".join(str(platform or "").split()).lower()
    if explicit:
        return explicit, False
    found = {source.platform for source in linked if source.platform}
    if len(found) == 1:
        return found.pop(), True
    if not found:
        raise LaneMigrationError("cannot infer the platform: pass --platform")
    raise LaneMigrationError(f"the task's sources span several platforms ({', '.join(sorted(found))}): pass --platform")


def _validate_retirements(task_id: int, *, linked: Sequence[LinkedSource], requested: Sequence[int]) -> list[int]:
    """Only a legacy workspace-level search source of this task may be retired."""
    by_id = {source.source_id: source for source in linked}
    result: list[int] = []
    for raw_id in requested or []:
        source_id = int(raw_id)
        source = by_id.get(source_id)
        if source is None:
            raise LaneMigrationError(f"source {source_id} is not linked to task {task_id}")
        if source.owner_task_id is not None:
            if source.owner_task_id != task_id:
                raise LaneMigrationError(f"source {source_id} is the lane of another task and cannot be retired")
            raise LaneMigrationError(f"source {source_id} is a lane of task {task_id} and cannot be retired")
        if source.source_type != SEARCH_SOURCE_TYPE:
            raise LaneMigrationError(f"source {source_id} is not a search source and cannot be retired")
        if source_id not in result:
            result.append(source_id)
    return result


def _collection_block(
    task: Any,
    *,
    phrases: list[str],
    content_types: list[str],
    limit: int,
    city: str | None,
    city_mode: str,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    """The canonical collection block, composed through the shared writer.

    The task's own block is the base, so settings this tool does not manage
    (discovery, comment limits, engagement types) survive the migration. The
    managed keys are overwritten with the given values.
    """
    existing = (getattr(task, "payload", None) or {}).get("collection")
    if existing is not None and not isinstance(existing, Mapping):
        raise LaneMigrationError("the task's collection block is not an object")
    raw: dict[str, Any] = dict(existing or {})
    raw["content_types"] = list(content_types)
    raw["limit"] = int(limit)
    raw["queries"] = list(phrases)
    if city:
        raw["city"] = {"name": city, "mode": city_mode}
    if date_from or date_to:
        raw["date_window"] = {"start": date_from, "end": date_to}
    try:
        payload = apply_collection({}, raw, job_type=str(task.job_type))
    except CollectionIntentError as error:
        raise LaneMigrationError(collection_error_message(str(error))) from error
    return dict(payload["collection"])
