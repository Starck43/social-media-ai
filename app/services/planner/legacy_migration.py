"""Legacy task identity resolution and resumable P22-T06 backfill."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.planner import (
    LegacyTaskId,
    PlannerTask,
    Task,
    TaskBackfillRecord,
    TaskBackfillRun,
    TaskLink,
)
from app.models.scheduling import AgentTask

PLANNER_NAMESPACE = "planner"
AGENT_NAMESPACE = "agent"
_NAMESPACES = frozenset({PLANNER_NAMESPACE, AGENT_NAMESPACE})


class LegacyIdentityError(ValueError):
    """A legacy key or mapping cannot be accepted safely."""


class LegacyMappingConflict(LegacyIdentityError):
    """A legacy key is already bound to another canonical identity."""


@dataclass(frozen=True)
class LegacyReference:
    tenant_id: int
    namespace: str
    legacy_id: int


def _validate_reference(tenant_id: int, namespace: str, legacy_id: int) -> LegacyReference:
    if type(tenant_id) is not int or tenant_id <= 0:
        raise LegacyIdentityError("invalid_workspace")
    if type(namespace) is not str or namespace not in _NAMESPACES:
        raise LegacyIdentityError("invalid_legacy_namespace")
    if type(legacy_id) is not int or legacy_id <= 0:
        raise LegacyIdentityError("invalid_legacy_id")
    return LegacyReference(tenant_id, namespace, legacy_id)


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_default)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _planner_fingerprint(row: PlannerTask) -> str:
    return fingerprint(
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "owner_user_id": row.owner_user_id,
            "assignee_id": row.assignee_id,
            "title": row.title_encrypted,
            "description": row.description_encrypted,
            "due_at": row.due_at,
            "due_date": row.due_date,
            "timezone": row.timezone,
            "priority": row.priority,
            "status": row.status,
            "category": row.category,
            "tags": row.tags,
            "contact_id": row.contact_id,
            "parent_id": row.parent_id,
            "series_id": row.series_id,
            "previous_occurrence_id": row.previous_occurrence_id,
            "occurrence_key": row.occurrence_key,
            "occurrence_index": row.occurrence_index,
            "recurrence": row.recurrence,
            "recurrence_source": row.recurrence_source,
            "series_template": row.series_template_encrypted,
            "version": row.version,
            "completed_at": row.completed_at,
        }
    )


def _planner_access_fingerprint(row: PlannerTask) -> str:
    return fingerprint({"owner": row.owner_user_id, "assignee": row.assignee_id, "visibility": "planner"})


def _agent_fingerprint(row: AgentTask) -> str:
    return fingerprint(
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "name": row.name,
            "launch_key": row.launch_key,
            "cron_expr": row.cron_expr,
            "job_type": row.job_type,
            "payload": row.payload,
            "is_active": row.is_active,
            "next_run_at": row.next_run_at,
            "last_run_at": row.last_run_at,
            "last_status": row.last_status,
            "agent_scenario_id": row.agent_scenario_id,
            "trigger_type": row.trigger_type,
            "trigger_config": row.trigger_config,
            "action_type": row.action_type,
            "dry_run": row.dry_run,
            "rate_limit_per_hour": row.rate_limit_per_hour,
            "cooldown_seconds": row.cooldown_seconds,
            "launch_fingerprint": row.launch_fingerprint,
            "launch_job_id": row.launch_job_id,
            "last_error": row.last_error,
        }
    )


def _agent_access_fingerprint(row: AgentTask, owner_user_id: int | None, source_ids: list[int] | None = None) -> str:
    return fingerprint(
        {
            "owner": owner_user_id,
            "workspace_visible": True,
            "source_ids": sorted(source_ids or []),
        }
    )


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


async def _agent_source_ids(session: AsyncSession, row: AgentTask) -> list[int]:
    from app.models.scheduling.agent_task import agent_task_sources

    return list(
        await session.scalars(
            select(agent_task_sources.c.source_id)
            .where(agent_task_sources.c.agent_task_id == row.id)
            .order_by(agent_task_sources.c.source_id)
        )
    )


def _canonical_values_from_planner(row: PlannerTask) -> dict[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "owner_user_id": row.owner_user_id,
        "assignee_id": row.assignee_id,
        "title_encrypted": row.title_encrypted,
        "description_encrypted": row.description_encrypted,
        "due_at": row.due_at,
        "due_date": row.due_date,
        "timezone": row.timezone,
        "priority": row.priority,
        "status": row.status,
        "category": row.category,
        "tags": row.tags,
        "contact_id": row.contact_id,
        "parent_id": None,
        "series_id": None,
        "previous_occurrence_id": None,
        "occurrence_key": row.occurrence_key,
        "occurrence_index": row.occurrence_index,
        "recurrence": row.recurrence,
        "recurrence_source": row.recurrence_source,
        "series_template_encrypted": row.series_template_encrypted,
        "version": row.version,
        "completed_at": row.completed_at,
        "visibility": "shared",
    }


def _canonical_values_from_agent(row: AgentTask, owner_user_id: int) -> dict[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "owner_user_id": owner_user_id,
        "assignee_id": owner_user_id,
        "title_encrypted": row.name,
        "description_encrypted": None,
        "due_at": None,
        "due_date": None,
        "timezone": "UTC",
        "priority": "medium",
        "status": "todo",
        "category": "automation",
        "tags": [],
        "contact_id": None,
        "parent_id": None,
        "series_id": None,
        "previous_occurrence_id": None,
        "occurrence_key": "once",
        "occurrence_index": 0,
        "recurrence": None,
        "recurrence_source": "legacy_agent",
        "series_template_encrypted": json.dumps({"legacy_namespace": AGENT_NAMESPACE, "legacy_id": row.id}),
        "version": 1,
        "completed_at": None,
        "visibility": "shared",
    }


async def resolve_legacy_task(
    session: AsyncSession,
    *,
    tenant_id: int,
    namespace: str,
    legacy_id: int,
    lock: bool = False,
) -> Task | None:
    """Resolve an opaque legacy URL only through its scoped mapping."""

    reference = _validate_reference(tenant_id, namespace, legacy_id)
    query = select(LegacyTaskId).where(
        LegacyTaskId.tenant_id == reference.tenant_id,
        LegacyTaskId.legacy_namespace == reference.namespace,
        LegacyTaskId.legacy_id == reference.legacy_id,
        LegacyTaskId.resolution == "mapped",
    )
    mapping = await session.scalar(query.with_for_update() if lock else query)
    if mapping is None:
        return None
    return await session.scalar(
        select(Task).where(
            Task.tenant_id == reference.tenant_id,
            Task.id == mapping.task_id,
            Task.deleted_at.is_(None),
        )
    )


async def project_legacy_task(tenant_id: int, namespace: str, legacy_id: int):
    """Resolve a legacy reference and apply canonical task visibility."""
    from app.core.database import async_session_maker
    from app.services.planner.canonical_tasks import task_service
    from app.services.planner.tasks import PlannerUnavailable

    async with async_session_maker() as session:
        try:
            task = await resolve_legacy_task(session, tenant_id=tenant_id, namespace=namespace, legacy_id=legacy_id)
        except LegacyIdentityError:
            raise PlannerUnavailable("planner_unavailable") from None
        if task is None:
            raise PlannerUnavailable("planner_unavailable")
        return await task_service.get(task.id, session=session)


class LegacyCompatibilityWriter:
    """Write one canonical mirror and its legacy alias in the caller's transaction."""

    @staticmethod
    def _database_session(session: AsyncSession) -> bool:
        return isinstance(session, AsyncSession)

    @staticmethod
    async def _source_updated_at(session, row):
        if "updated_at" in inspect(row).expired_attributes:
            await session.refresh(row, attribute_names=["updated_at"])
        return row.updated_at

    async def _mapping(self, session, reference: LegacyReference, *, lock: bool = True):
        query = select(LegacyTaskId).where(
            LegacyTaskId.tenant_id == reference.tenant_id,
            LegacyTaskId.legacy_namespace == reference.namespace,
            LegacyTaskId.legacy_id == reference.legacy_id,
        )
        return await session.scalar(query.with_for_update() if lock else query)

    async def _alias(
        self,
        session,
        *,
        reference: LegacyReference,
        task_id: int,
        source_fingerprint: str,
        access_fingerprint: str,
        source_updated_at,
        details: dict | None = None,
        resolution: str = "mapped",
        resolution_reason: str | None = None,
    ) -> LegacyTaskId:
        mapping = await self._mapping(session, reference)
        if mapping is not None and mapping.task_id is not None and mapping.task_id != task_id:
            raise LegacyMappingConflict("legacy_identity_already_mapped")
        if mapping is None:
            mapping = LegacyTaskId(
                tenant_id=reference.tenant_id,
                legacy_namespace=reference.namespace,
                legacy_id=reference.legacy_id,
                task_id=task_id,
            )
            session.add(mapping)
        mapping.task_id = task_id
        mapping.source_fingerprint = source_fingerprint
        mapping.access_fingerprint = access_fingerprint
        mapping.source_updated_at = source_updated_at
        mapping.resolution = resolution
        mapping.resolution_reason = resolution_reason
        mapping.details = details
        await session.flush()
        return mapping

    async def planner(self, session: AsyncSession, row: PlannerTask) -> Task:
        if not self._database_session(session):
            return row  # Lightweight planner unit doubles have no canonical store.
        reference = _validate_reference(row.tenant_id, PLANNER_NAMESPACE, row.id)
        mapping = await self._mapping(session, reference)
        if mapping is not None:
            task = await session.scalar(select(Task).where(Task.tenant_id == row.tenant_id, Task.id == mapping.task_id))
            if task is None:
                raise LegacyMappingConflict("canonical_task_missing")
            self._copy_planner(task, row)
        else:
            task = Task(**_canonical_values_from_planner(row))
            session.add(task)
            await session.flush()
        task.parent_id = await self._canonical_id(session, row.tenant_id, PLANNER_NAMESPACE, row.parent_id)
        task.series_id = await self._canonical_id(session, row.tenant_id, PLANNER_NAMESPACE, row.series_id) or task.id
        task.previous_occurrence_id = await self._canonical_id(
            session, row.tenant_id, PLANNER_NAMESPACE, row.previous_occurrence_id
        )
        await self._alias(
            session,
            reference=reference,
            task_id=task.id,
            source_fingerprint=_planner_fingerprint(row),
            access_fingerprint=_planner_access_fingerprint(row),
            source_updated_at=await self._source_updated_at(session, row),
            details={"source_table": "planner_tasks"},
        )
        await session.flush()
        return task

    @staticmethod
    async def _canonical_id(session, tenant_id: int, namespace: str, legacy_id: int | None) -> int | None:
        if legacy_id is None:
            return None
        mapping = await session.scalar(
            select(LegacyTaskId).where(
                LegacyTaskId.tenant_id == tenant_id,
                LegacyTaskId.legacy_namespace == namespace,
                LegacyTaskId.legacy_id == legacy_id,
                LegacyTaskId.resolution == "mapped",
            )
        )
        return mapping.task_id if mapping is not None else None

    async def agent(
        self,
        session: AsyncSession,
        row: AgentTask,
        *,
        owner_user_id: int | None,
        attach_to_task_id: int | None = None,
        execution_for_task_id: int | None = None,
    ) -> Task | None:
        if not self._database_session(session):
            return None
        reference = _validate_reference(row.tenant_id, AGENT_NAMESPACE, row.id)
        if owner_user_id is None:
            await self._alias_unresolved(
                session,
                row=row,
                reason="owner_unproven",
                details={"source_table": "agent_tasks", "job_type": row.job_type},
            )
            return None
        mapping = await self._mapping(session, reference)
        task = None
        if attach_to_task_id is not None:
            task = await session.scalar(
                select(Task).where(Task.tenant_id == row.tenant_id, Task.id == attach_to_task_id).with_for_update()
            )
            if task is None:
                raise LegacyMappingConflict("linked_canonical_task_missing")
            # The locked planner task serializes assignment creation. An additional
            # schedule owns its own identity and links back to the human work.
            restricted_target = task.id if owner_user_id != task.owner_user_id else None
            occupied = await session.scalar(
                select(LegacyTaskId.id)
                .where(
                    LegacyTaskId.tenant_id == row.tenant_id,
                    LegacyTaskId.legacy_namespace == AGENT_NAMESPACE,
                    LegacyTaskId.legacy_id != row.id,
                    LegacyTaskId.task_id == task.id,
                )
                .limit(1)
            )
            if mapping is None or mapping.task_id != task.id:
                if restricted_target is not None or occupied is not None or (mapping and mapping.task_id is not None):
                    execution_for_task_id = None if restricted_target else task.id
                    attach_to_task_id = None
                    task = None
        else:
            restricted_target = None
        if task is None:
            if mapping is not None and mapping.task_id is not None:
                task = await session.scalar(
                    select(Task).where(Task.tenant_id == row.tenant_id, Task.id == mapping.task_id)
                )
                if task is None:
                    raise LegacyMappingConflict("canonical_task_missing")
            else:
                task = Task(**_canonical_values_from_agent(row, owner_user_id))
                session.add(task)
                await session.flush()
        owner_user_id = task.owner_user_id
        attached = attach_to_task_id is not None or bool(mapping and (mapping.details or {}).get("attached"))
        if not attached and task.title_encrypted != row.name:
            task.title_encrypted = row.name
            task.version += 1
        details = {
            "source_table": "agent_tasks",
            "job_type": row.job_type,
            "cron_expr": row.cron_expr,
            "is_active": row.is_active,
            "next_run_at": _timestamp(row.next_run_at),
            "agent_scenario_id": row.agent_scenario_id,
            "payload_keys": sorted((row.payload or {}).keys()),
            "attached": attached,
        }
        source_ids = await _agent_source_ids(session, row)
        details["source_ids"] = source_ids
        await self._alias(
            session,
            reference=reference,
            task_id=task.id,
            source_fingerprint=_agent_fingerprint(row),
            access_fingerprint=_agent_access_fingerprint(row, owner_user_id, source_ids),
            source_updated_at=await self._source_updated_at(session, row),
            details=details,
        )
        if execution_for_task_id is not None and execution_for_task_id != task.id:
            await self.link_execution(
                session,
                tenant_id=row.tenant_id,
                from_task_id=task.id,
                to_task_id=execution_for_task_id,
                legacy_id=row.id,
                details=details,
            )
        if restricted_target is not None and restricted_target != task.id:
            await self.link(
                session,
                tenant_id=row.tenant_id,
                from_task_id=task.id,
                relation="related",
                to_task_id=restricted_target,
                legacy_id=row.id,
                details={"restricted": True, "reason": "visibility_boundary"},
            )
        return task

    async def refresh_agent(self, session: AsyncSession, row: AgentTask) -> Task | None:
        """Refresh an existing alias after a legacy schedule or receipt write."""

        if not self._database_session(session):
            return None

        reference = _validate_reference(row.tenant_id, AGENT_NAMESPACE, row.id)
        mapping = await self._mapping(session, reference)
        if mapping is None or mapping.task_id is None:
            return None
        task = await session.scalar(select(Task).where(Task.tenant_id == row.tenant_id, Task.id == mapping.task_id))
        if task is None:
            raise LegacyMappingConflict("canonical_task_missing")
        return await self.agent(
            session,
            row,
            owner_user_id=task.owner_user_id,
            attach_to_task_id=task.id if (mapping.details or {}).get("attached") else None,
        )

    async def delete_planner(self, session: AsyncSession, row: PlannerTask) -> None:
        if not self._database_session(session):
            return
        # PostgreSQL cascades through all three planner self-references. Resolve
        # their aliases before the delete while the legacy identities still exist.
        affected = (
            select(PlannerTask.id)
            .where(PlannerTask.tenant_id == row.tenant_id, PlannerTask.id == row.id)
            .cte(recursive=True)
        )
        affected = affected.union(
            select(PlannerTask.id)
            .join(
                affected,
                or_(
                    PlannerTask.parent_id == affected.c.id,
                    PlannerTask.series_id == affected.c.id,
                    PlannerTask.previous_occurrence_id == affected.c.id,
                ),
            )
            .where(PlannerTask.tenant_id == row.tenant_id)
        )
        task_ids = select(LegacyTaskId.task_id).where(
            LegacyTaskId.tenant_id == row.tenant_id,
            LegacyTaskId.legacy_namespace == PLANNER_NAMESPACE,
            LegacyTaskId.legacy_id.in_(select(affected.c.id)),
        )
        await session.execute(
            update(Task)
            .where(Task.tenant_id == row.tenant_id, Task.id.in_(task_ids), Task.deleted_at.is_(None))
            .values(deleted_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )

    async def delete_agent(self, session: AsyncSession, row: AgentTask) -> None:
        """Tombstone an agent mirror before the legacy row is physically deleted."""

        if not self._database_session(session):
            return
        reference = _validate_reference(row.tenant_id, AGENT_NAMESPACE, row.id)
        mapping = await self._mapping(session, reference)
        if mapping is None or mapping.task_id is None:
            return
        details = dict(mapping.details or {})
        details["deleted"] = True
        if details.get("attached"):
            mapping.task_id = None
            mapping.resolution = "unresolved"
        else:
            task = await session.scalar(
                select(Task).where(Task.tenant_id == row.tenant_id, Task.id == mapping.task_id).with_for_update()
            )
            if task is None:
                raise LegacyMappingConflict("canonical_task_missing")
            task.deleted_at = datetime.now(timezone.utc)
        mapping.details = details
        mapping.resolution_reason = "legacy_deleted"
        await session.flush()

    async def _alias_unresolved(self, session, *, row: AgentTask, reason: str, details: dict):
        reference = _validate_reference(row.tenant_id, AGENT_NAMESPACE, row.id)
        mapping = await self._mapping(session, reference)
        if mapping is not None and mapping.resolution == "mapped":
            return mapping
        if mapping is None:
            mapping = LegacyTaskId(
                tenant_id=row.tenant_id,
                legacy_namespace=AGENT_NAMESPACE,
                legacy_id=row.id,
            )
            # An unresolved alias has no canonical task identity. It is retained
            # only as diagnostic evidence and is never returned by the resolver.
            session.add(mapping)
        mapping.resolution = "unresolved"
        mapping.resolution_reason = reason
        mapping.source_fingerprint = _agent_fingerprint(row)
        mapping.access_fingerprint = _agent_access_fingerprint(row, None, await _agent_source_ids(session, row))
        mapping.source_updated_at = await self._source_updated_at(session, row)
        mapping.details = details
        await session.flush()
        return mapping

    @staticmethod
    def _copy_planner(task: Task, row: PlannerTask):
        for name in (
            "owner_user_id",
            "assignee_id",
            "title_encrypted",
            "description_encrypted",
            "due_at",
            "due_date",
            "timezone",
            "priority",
            "status",
            "category",
            "tags",
            "contact_id",
            "occurrence_key",
            "occurrence_index",
            "recurrence",
            "recurrence_source",
            "series_template_encrypted",
            "version",
            "completed_at",
        ):
            setattr(task, name, getattr(row, name))

    async def link_execution(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        from_task_id: int,
        to_task_id: int,
        legacy_id: int | None,
        details: dict | None,
    ) -> TaskLink:
        return await self.link(
            session,
            tenant_id=tenant_id,
            from_task_id=from_task_id,
            relation="execution_for",
            to_task_id=to_task_id,
            legacy_id=legacy_id,
            details=details,
        )

    async def link(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        from_task_id: int,
        relation: str,
        to_task_id: int,
        legacy_id: int | None,
        details: dict | None,
    ) -> TaskLink:
        if relation not in {"execution_for", "related"}:
            raise LegacyIdentityError("task_link_relation_invalid")
        if from_task_id == to_task_id:
            raise LegacyIdentityError("task_link_self_reference")
        existing = await session.scalar(
            select(TaskLink).where(
                TaskLink.tenant_id == tenant_id,
                TaskLink.from_task_id == from_task_id,
                TaskLink.relation == relation,
                TaskLink.to_task_id == to_task_id,
            )
        )
        if existing is not None:
            return existing
        link = TaskLink(
            tenant_id=tenant_id,
            from_task_id=from_task_id,
            relation=relation,
            to_task_id=to_task_id,
            legacy_namespace=AGENT_NAMESPACE,
            legacy_id=legacy_id,
            details=details,
        )
        session.add(link)
        await session.flush()
        return link


compatibility_writer = LegacyCompatibilityWriter()


def _planner_reference(row: AgentTask) -> int | None:
    payload = row.payload if isinstance(row.payload, dict) else {}
    reference = payload.get("planner_reference")
    if not isinstance(reference, dict) or reference.get("tenant_id") != row.tenant_id:
        return None
    value = reference.get("task_id")
    return value if type(value) is int and value > 0 else None


async def _workspace_owner(session: AsyncSession, tenant_id: int, user_id: int | None) -> int | None:
    if type(user_id) is not int or user_id <= 0:
        return None
    from app.models.identity import User
    from app.models.tenancy import TenantUser

    return await session.scalar(
        select(TenantUser.user_id)
        .join(User, User.id == TenantUser.user_id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == user_id,
            TenantUser.is_active.is_(True),
            User.is_active.is_(True),
        )
    )


async def _proven_agent_owner(session: AsyncSession, row: AgentTask) -> int | None:
    # The payer, a job initiator and IDs supplied in an editable payload
    # establish no ownership of a schedule. A verified brief binds its recipient.
    if row.job_type == "social.brief":
        from app.services.social.brief_schedule import schedule_for_task

        try:
            signed_owner = schedule_for_task(row).user_id
        except ValueError:
            return None
        return await _workspace_owner(session, row.tenant_id, signed_owner)
    return None


class LegacyBackfillService:
    """Bounded, idempotent workspace copy with cursors and reconciliation rows."""

    async def start(self, session: AsyncSession, *, tenant_id: int, watermark: datetime | None = None):
        if type(tenant_id) is not int or tenant_id <= 0:
            raise LegacyIdentityError("invalid_workspace")
        from app.models import Tenant

        if await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update()) is None:
            raise LegacyIdentityError("backfill_workspace_unavailable")
        existing = await session.scalar(
            select(TaskBackfillRun)
            .where(TaskBackfillRun.tenant_id == tenant_id, TaskBackfillRun.status.in_(("running", "paused")))
            .order_by(TaskBackfillRun.id.desc())
            .with_for_update()
        )
        if existing is not None:
            return existing
        run = TaskBackfillRun(
            tenant_id=tenant_id,
            watermark=watermark or datetime.now(timezone.utc),
            planner_cursor=0,
            agent_cursor=0,
            status="running",
            counts={"planner": 0, "agent": 0, "mapped": 0, "replayed": 0, "restricted": 0, "unresolved": 0},
        )
        session.add(run)
        await session.flush()
        return run

    async def process(self, session: AsyncSession, run: TaskBackfillRun, *, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise LegacyIdentityError("invalid_backfill_limit")
        if run.status not in {"running", "paused"}:
            raise LegacyIdentityError("backfill_not_resumable")
        run.status = "running"
        processed = 0
        processed += await self._process_planner(session, run, limit)
        if processed < limit:
            processed += await self._process_agents(session, run, limit - processed)
        planner_left = await session.scalar(
            select(func.count(PlannerTask.id)).where(
                PlannerTask.tenant_id == run.tenant_id, PlannerTask.id > run.planner_cursor
            )
        )
        agent_left = await session.scalar(
            select(func.count(AgentTask.id)).where(
                AgentTask.tenant_id == run.tenant_id, AgentTask.id > run.agent_cursor
            )
        )
        if not planner_left and not agent_left and run.replay_until is None:
            run.replay_until = datetime.now(timezone.utc)
        if processed < limit and not planner_left and not agent_left:
            processed += await self._process_planner(session, run, limit - processed, replay=True)
            if processed < limit:
                processed += await self._process_agents(session, run, limit - processed, replay=True)
        replay_planner_left = await session.scalar(
            select(func.count(PlannerTask.id)).where(
                PlannerTask.tenant_id == run.tenant_id,
                PlannerTask.id > run.replay_planner_cursor,
                PlannerTask.updated_at > run.watermark,
                PlannerTask.updated_at <= run.replay_until if run.replay_until is not None else True,
            )
        )
        replay_agent_left = await session.scalar(
            select(func.count(AgentTask.id)).where(
                AgentTask.tenant_id == run.tenant_id,
                AgentTask.id > run.replay_agent_cursor,
                AgentTask.updated_at > run.watermark,
                AgentTask.updated_at <= run.replay_until if run.replay_until is not None else True,
            )
        )
        if not planner_left and not agent_left and not replay_planner_left and not replay_agent_left:
            used, complete = await self._reconcile_planner_relations(session, run, limit - processed)
            processed += used
            run.status = "completed" if complete else "paused"
            if complete:
                run.completed_at = datetime.now(timezone.utc)
                outcomes = await session.execute(
                    select(TaskBackfillRecord.outcome, func.count())
                    .where(TaskBackfillRecord.tenant_id == run.tenant_id, TaskBackfillRecord.run_id == run.id)
                    .group_by(TaskBackfillRecord.outcome)
                )
                run.counts = {
                    **run.counts,
                    **dict.fromkeys(("mapped", "restricted", "unresolved", "replayed"), 0),
                    **dict(outcomes.all()),
                }
        else:
            run.status = "paused"
        await session.flush()
        return {
            "run_id": run.id,
            "status": run.status,
            "processed": processed,
            "planner_cursor": run.planner_cursor,
            "agent_cursor": run.agent_cursor,
            "counts": dict(run.counts or {}),
        }

    async def _reconcile_planner_relations(self, session, run, limit: int) -> tuple[int, bool]:
        from app.models import CalendarEvent

        tenant_id = run.tenant_id
        counts = dict(run.counts)
        rows = list(
            await session.scalars(
                select(PlannerTask)
                .where(PlannerTask.tenant_id == tenant_id, PlannerTask.id > counts.get("planner_relation_cursor", 0))
                .order_by(PlannerTask.id)
                .limit(limit)
                .with_for_update()
            )
        )
        for row in rows:
            await compatibility_writer.planner(session, row)
            counts["planner_relation_cursor"] = row.id
        run.counts = dict(counts)
        remaining = limit - len(rows)
        if remaining == 0:
            return len(rows), False
        events = list(
            await session.scalars(
                select(CalendarEvent)
                .where(
                    CalendarEvent.tenant_id == tenant_id,
                    CalendarEvent.task_id.is_not(None),
                    CalendarEvent.id > counts.get("event_relation_cursor", 0),
                )
                .order_by(CalendarEvent.id)
                .limit(remaining)
                .with_for_update()
            )
        )
        for event in events:
            counts["event_relation_cursor"] = event.id
            mapping = await session.scalar(
                select(LegacyTaskId).where(
                    LegacyTaskId.tenant_id == tenant_id,
                    LegacyTaskId.legacy_namespace == PLANNER_NAMESPACE,
                    LegacyTaskId.legacy_id == event.task_id,
                    LegacyTaskId.resolution == "mapped",
                )
            )
            if mapping is None:
                continue
            await self._source_link(
                session,
                tenant_id=tenant_id,
                task_id=mapping.task_id,
                source_object_id=event.source_object_id,
                relation="calendar_event",
                details={"calendar_event_id": event.id, "origin": event.origin, "state": event.state},
            )

        run.counts = dict(counts)
        return len(rows) + len(events), len(events) < remaining

    @staticmethod
    async def _source_link(session, *, tenant_id, task_id, source_object_id, relation, details):
        from app.models.planner import TaskSourceLink

        existing = await session.scalar(
            select(TaskSourceLink).where(
                TaskSourceLink.tenant_id == tenant_id,
                TaskSourceLink.task_id == task_id,
                TaskSourceLink.source_object_id == source_object_id,
                TaskSourceLink.relation == relation,
            )
        )
        if existing is None:
            session.add(
                TaskSourceLink(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    source_object_id=source_object_id,
                    relation=relation,
                    details=details,
                )
            )
        else:
            existing.details = details
        await session.flush()

    async def _process_planner(self, session, run, limit: int, *, replay: bool = False) -> int:
        cursor_name = "replay_planner_cursor" if replay else "planner_cursor"
        cursor = getattr(run, cursor_name)
        filters = [PlannerTask.tenant_id == run.tenant_id, PlannerTask.id > cursor]
        if replay:
            filters.extend(
                [
                    PlannerTask.updated_at > run.watermark,
                    PlannerTask.updated_at <= run.replay_until,
                ]
            )
        rows = list(
            await session.scalars(
                select(PlannerTask).where(*filters).order_by(PlannerTask.id).limit(limit).with_for_update()
            )
        )
        for row in rows:
            task = await compatibility_writer.planner(session, row)
            await self._record(
                session,
                run,
                namespace=PLANNER_NAMESPACE,
                legacy_id=row.id,
                task_id=task.id,
                outcome="replayed" if replay or row.updated_at > run.watermark else "mapped",
                source_fingerprint=_planner_fingerprint(row),
                access_fingerprint=_planner_access_fingerprint(row),
                details={"source_updated_at": _timestamp(row.updated_at)},
            )
            setattr(run, cursor_name, row.id)
            if replay:
                self._count(run, "replayed")
            else:
                self._count(run, "planner")
                self._count(run, "replayed" if row.updated_at > run.watermark else "mapped")
        return len(rows)

    async def _process_agents(self, session, run, limit: int, *, replay: bool = False) -> int:
        cursor_name = "replay_agent_cursor" if replay else "agent_cursor"
        cursor = getattr(run, cursor_name)
        filters = [AgentTask.tenant_id == run.tenant_id, AgentTask.id > cursor]
        if replay:
            filters.extend(
                [
                    AgentTask.updated_at > run.watermark,
                    AgentTask.updated_at <= run.replay_until,
                ]
            )
        rows = list(
            await session.scalars(
                select(AgentTask).where(*filters).order_by(AgentTask.id).limit(limit).with_for_update()
            )
        )
        for row in rows:
            planner_id = _planner_reference(row)
            planner_task = None
            attach_to = None
            execution_for = None
            reason = None
            reference = _validate_reference(row.tenant_id, AGENT_NAMESPACE, row.id)
            mapping = await compatibility_writer._mapping(session, reference)
            mapped = await resolve_legacy_task(
                session, tenant_id=row.tenant_id, namespace=AGENT_NAMESPACE, legacy_id=row.id
            )
            owner = mapped.owner_user_id if mapped is not None else await _proven_agent_owner(session, row)
            restricted = False
            planner_canonical = None
            if mapping is not None and mapping.task_id is not None and mapped is None:
                reason = "canonical_task_unavailable"
            elif planner_id is not None:
                planner_task = await session.scalar(
                    select(PlannerTask).where(PlannerTask.tenant_id == row.tenant_id, PlannerTask.id == planner_id)
                )
                if planner_task is None:
                    reason = "planner_reference_unavailable"
                else:
                    planner_canonical = await resolve_legacy_task(
                        session, tenant_id=row.tenant_id, namespace=PLANNER_NAMESPACE, legacy_id=planner_task.id
                    )
                    if planner_canonical is None:
                        reason = "planner_reference_unavailable"
                    else:
                        related_count = await session.scalar(
                            select(func.count(AgentTask.id)).where(
                                AgentTask.tenant_id == row.tenant_id,
                                AgentTask.payload["planner_reference"]["task_id"].as_integer() == planner_id,
                            )
                        )
                        if owner is None:
                            # A linked planner task supplies the canonical owner.
                            owner = planner_task.owner_user_id
                        if owner not in {planner_task.owner_user_id, planner_task.assignee_id}:
                            restricted = True
                        elif related_count == 1:
                            attach_to = planner_canonical.id
                            owner = planner_task.owner_user_id
                        else:
                            execution_for = planner_canonical.id
                            owner = owner or planner_task.owner_user_id
            elif owner is None:
                reason = "owner_unproven"
            source_ids = await _agent_source_ids(session, row)
            task = None
            if reason is None:
                task = await compatibility_writer.agent(
                    session,
                    row,
                    owner_user_id=owner,
                    attach_to_task_id=attach_to,
                    execution_for_task_id=execution_for,
                )
                if (
                    restricted
                    and task is not None
                    and planner_canonical is not None
                    and task.id != planner_canonical.id
                ):
                    await compatibility_writer.link(
                        session,
                        tenant_id=row.tenant_id,
                        from_task_id=task.id,
                        relation="related",
                        to_task_id=planner_canonical.id,
                        legacy_id=row.id,
                        details={"restricted": True, "reason": "visibility_boundary"},
                    )
            else:
                await compatibility_writer._alias_unresolved(
                    session,
                    row=row,
                    reason=reason,
                    details={"source_table": "agent_tasks", "planner_id": planner_id},
                )
            record_owner = task.owner_user_id if task is not None else owner
            outcome = (
                "unresolved" if task is None else "restricted" if restricted else "replayed" if replay else "mapped"
            )
            if not replay and task is not None and row.updated_at > run.watermark and not restricted:
                outcome = "replayed"
            await self._record(
                session,
                run,
                namespace=AGENT_NAMESPACE,
                legacy_id=row.id,
                task_id=None if task is None else task.id,
                outcome=outcome,
                source_fingerprint=_agent_fingerprint(row),
                access_fingerprint=_agent_access_fingerprint(row, record_owner, source_ids),
                reason=reason,
                details={
                    "planner_id": planner_id,
                    "attached": task is not None and planner_canonical is not None and task.id == planner_canonical.id,
                    "execution_for": (
                        execution_for
                        if task is not None and planner_canonical is not None and task.id != planner_canonical.id
                        else None
                    ),
                    "restricted": restricted,
                    "source_ids": source_ids,
                },
            )
            setattr(run, cursor_name, row.id)
            if not replay:
                self._count(run, "agent")
            self._count(run, outcome)
        return len(rows)

    async def _record(self, session, run, **values):
        namespace = values.pop("namespace")
        existing = await session.scalar(
            select(TaskBackfillRecord).where(
                TaskBackfillRecord.tenant_id == run.tenant_id,
                TaskBackfillRecord.run_id == run.id,
                TaskBackfillRecord.legacy_namespace == namespace,
                TaskBackfillRecord.legacy_id == values["legacy_id"],
            )
        )
        if existing is None:
            session.add(
                TaskBackfillRecord(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    legacy_namespace=namespace,
                    legacy_id=values.pop("legacy_id"),
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        await session.flush()

    @staticmethod
    def _count(run, key: str):
        counts = dict(run.counts or {})
        counts[key] = int(counts.get(key, 0)) + 1
        run.counts = counts


legacy_backfill_service = LegacyBackfillService()
