"""Transactional admission for a published canonical Task revision."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from itertools import islice

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user, has_permission, permission_scope
from app.core.pii import encrypt_pii
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import Job, User
from app.models.planner import Task, TaskRun, TaskTrigger
from app.services.execution_plan_contracts import OPERATIONS
from app.services.planner.canonical_tasks import task_service
from app.services.task_execution_plans import execution_plan_service
from app.types import ActionType

CANONICAL_JOB_TYPE = "task.execute"
ACTIVE_RUN_STATUSES = ("pending", "running")
MAX_RUN_ATTEMPTS = 3


class TaskRunAdmissionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaskRunReceipt:
    run_id: int
    task_id: int
    revision: int
    job_id: int | None
    status: str
    result: dict | None
    replayed: bool


_STEP_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESULT_CODES = frozenset(
    {
        "task_authorization_lost",
        "task_cancelled",
        "task_checkpoint_limit",
        "task_execution_failed",
        "task_execution_refused",
        "task_outcome_unknown",
        "task_step_unavailable",
        "unsupported_execution_operation",
        "unsupported_execution_effects",
        "invalid_execution_plan",
        "unsupported_capability",
        "read_only",
        "select_resource",
        "resource_unavailable",
        "reauth_required",
        "installation_required",
    }
)


def _permitted_result(value):
    """Expose bounded progress only, never provider data or checkpoint contents."""
    if not isinstance(value, dict):
        return None
    result = {}
    if value.get("reason") == "active_run":
        result["reason"] = "active_run"
    coverage = value.get("coverage")
    if isinstance(coverage, str) and coverage in {"complete", "partial"}:
        result["coverage"] = coverage
    code = value.get("code")
    if isinstance(code, str):
        result["code"] = code if code in _RESULT_CODES else "task_execution_refused"
    raw_steps = value.get("steps")
    if isinstance(raw_steps, dict):
        steps = {}
        for name, item in islice(raw_steps.items(), 12):
            if not isinstance(name, str) or not _STEP_NAME.fullmatch(name) or not isinstance(item, dict):
                continue
            status = item.get("status")
            if not isinstance(status, str) or status not in {"done", "unavailable", "previewed"}:
                continue
            step = {"status": status}
            count = item.get("count")
            if type(count) is int and 0 <= count <= 1_000_000_000:
                step["count"] = count
            steps[name] = step
        if steps:
            result["steps"] = steps
    return result or None


def _receipt(run: TaskRun, *, replayed: bool = False) -> TaskRunReceipt:
    return TaskRunReceipt(
        run.id, run.task_id, run.revision, run.job_id, run.status, _permitted_result(run.result), replayed
    )


def _digest(*values) -> str:
    return sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


async def authorize_task_run(session, task_id: int, *, lock: bool = False, member_channel: str | None = None) -> Task:
    action = ActionType.UPDATE if lock else ActionType.VIEW
    identity = await task_service._actor(session, action, member_channel=member_channel)
    if ("plannertask", ActionType.UPDATE) not in workspace_grants(identity[2]) or not has_permission(
        get_current_user(), "plannertask", ActionType.UPDATE
    ):
        raise PermissionError("task_run_denied")
    task = await task_service._task(session, task_id, identity, lock=lock)
    if not lock and not await task_service._can_edit(session, task, identity):
        raise PermissionError("task_run_denied")
    return task


def validate_admissible_plan(plan):
    from app.services.task_run_worker import validate_runtime_plan

    if any(OPERATIONS[step.operation].llm for step in plan.steps):
        raise TaskRunAdmissionError("payer_unavailable")
    validate_runtime_plan(plan)


async def _insert_run(session, *, task, admitted, principal_id, effective_at, fingerprint, **origin):
    if task.status in {"done", "cancelled"}:
        # Closure is the only way to end scheduled work; a closed task must not
        # admit another run, whether the request is manual or due.
        raise TaskRunAdmissionError("task_closed")
    plan = admitted.plan
    validate_admissible_plan(plan)
    snapshot = {
        "plan": plan.model_dump(mode="json"),
        "methodology": json.loads(admitted.methodology_json),
        "resources": [asdict(resource) for resource in admitted.resources],
        "limits": {"max_attempts": MAX_RUN_ATTEMPTS},
    }
    encrypted = encrypt_pii(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    active_run_id = await session.scalar(
        select(TaskRun.id).where(
            TaskRun.tenant_id == task.tenant_id,
            TaskRun.task_id == task.id,
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    run = TaskRun(
        tenant_id=task.tenant_id,
        task_id=task.id,
        revision=admitted.reference.revision,
        principal_id=principal_id,
        status="skipped" if active_run_id is not None else "pending",
        result={"reason": "active_run"} if active_run_id is not None else None,
        admitted_inputs_encrypted=encrypted,
        effective_at=effective_at,
        fingerprint=fingerprint,
        **origin,
    )
    session.add(run)
    await session.flush()
    if active_run_id is None:
        job = Job(
            tenant_id=task.tenant_id,
            task_run_id=run.id,
            job_type=CANONICAL_JOB_TYPE,
            run_at=effective_at,
            payload={},
            max_attempts=MAX_RUN_ATTEMPTS,
        )
        session.add(job)
        await session.flush()
        run.job_id = job.id
        await session.flush()
    return _receipt(run)


class TaskRunService:
    @with_db_session
    async def admit_manual(
        self, task_id: int, *, expected_revision: int, request_key: str, session=None
    ) -> TaskRunReceipt:
        if (
            not isinstance(request_key, str)
            or not 1 <= len(request_key) <= 128
            or not request_key.strip()
            or not request_key.isprintable()
        ):
            raise TaskRunAdmissionError("invalid_request_key")
        if type(expected_revision) is not int or expected_revision <= 0:
            raise TaskRunAdmissionError("invalid_revision")

        task = await authorize_task_run(session, task_id, lock=True)
        if task.status in {"done", "cancelled"}:
            raise TaskRunAdmissionError("task_closed")
        actor_id = get_current_user().id
        # The planner UPDATE identity check locks this workspace before locking
        # the task. This also serializes manual keys across different tasks.
        key = _digest(request_key)
        fingerprint = _digest(task_id, expected_revision)
        existing = await session.scalar(
            select(TaskRun).where(
                TaskRun.tenant_id == task.tenant_id,
                TaskRun.manual_actor_id == actor_id,
                TaskRun.request_key == key,
            )
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise TaskRunAdmissionError("request_key_conflict")
            return _receipt(existing, replayed=True)
        admitted = await execution_plan_service.load_for_admission(task.id, expected_revision, session=session)
        return await _insert_run(
            session,
            task=task,
            admitted=admitted,
            principal_id=actor_id,
            effective_at=datetime.now(timezone.utc),
            fingerprint=fingerprint,
            manual_actor_id=actor_id,
            request_key=key,
        )

    async def admit_scheduled(
        self, trigger: TaskTrigger, *, occurrence_at: datetime, now: datetime, session
    ) -> TaskRunReceipt:
        """Internal scheduler boundary. The scheduler also owns cursor updates in this transaction."""
        tenant_id = current_tenant_id()
        if get_current_user() is not None or is_bypass() or tenant_id != trigger.tenant_id:
            raise PermissionError("task_run_denied")
        principal = await session.scalar(
            select(User)
            .where(User.id == trigger.principal_id, User.is_active.is_(True))
            .execution_options(populate_existing=True)
        )
        if principal is None:
            raise PermissionError("scheduled_principal_unavailable")
        with permission_scope(principal):
            identity = await task_service._actor(session, ActionType.UPDATE, member_channel="web")
        with permission_scope(principal, member_grants=workspace_grants(identity[2])):
            task = await authorize_task_run(session, trigger.task_id, lock=True, member_channel="web")
        stored = await session.scalar(
            select(TaskTrigger)
            .where(TaskTrigger.tenant_id == tenant_id, TaskTrigger.id == trigger.id, TaskTrigger.task_id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if stored is None or stored.principal_id != principal.id:
            raise PermissionError("scheduled_principal_unavailable")
        existing = await session.scalar(
            select(TaskRun).where(
                TaskRun.tenant_id == tenant_id,
                TaskRun.trigger_id == stored.id,
                TaskRun.occurrence_at == occurrence_at,
            )
        )
        fingerprint = _digest(
            task.id, stored.revision, principal.id, occurrence_at.astimezone(timezone.utc).isoformat()
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise TaskRunAdmissionError("occurrence_conflict")
            return _receipt(existing, replayed=True)
        if not stored.enabled or stored.next_due_at != occurrence_at or occurrence_at > now:
            raise TaskRunAdmissionError("occurrence_unavailable")
        admitted = await execution_plan_service.load_for_scheduled_admission(
            task.id, stored.revision, principal_id=principal.id, session=session
        )
        return await _insert_run(
            session,
            task=task,
            admitted=admitted,
            principal_id=principal.id,
            effective_at=occurrence_at,
            fingerprint=fingerprint,
            trigger_id=stored.id,
            occurrence_at=occurrence_at,
        )

    @with_db_session
    async def list_receipts(self, task_id: int, *, limit: int = 50, before_id: int | None = None, session=None):
        """Newest-first receipts for one visible task; same projection as a single read."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise TaskRunAdmissionError("invalid_page")
        identity = await task_service._actor(session, ActionType.VIEW)
        if not has_permission(get_current_user(), "plannertask", ActionType.VIEW):
            raise PermissionError("task_view_denied")
        task = await task_service._task(session, task_id, identity)
        query = select(TaskRun).where(TaskRun.tenant_id == task.tenant_id, TaskRun.task_id == task.id)
        if before_id is not None:
            query = query.where(TaskRun.id < before_id)
        rows = list(
            (
                await session.scalars(
                    query.order_by(TaskRun.id.desc()).limit(limit + 1).execution_options(populate_existing=True)
                )
            ).all()
        )
        return {
            "items": [_receipt(row) for row in rows[:limit]],
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    @with_db_session
    async def get_receipt(self, run_id: int, *, session=None) -> TaskRunReceipt:
        if type(run_id) is not int or run_id <= 0:
            raise TaskRunAdmissionError("run_unavailable")
        identity = await task_service._actor(session, ActionType.VIEW)
        if not has_permission(get_current_user(), "plannertask", ActionType.VIEW):
            raise PermissionError("task_view_denied")
        run = await session.scalar(select(TaskRun).where(TaskRun.id == run_id, TaskRun.tenant_id == identity[0]))
        if run is None:
            raise TaskRunAdmissionError("run_unavailable")
        await task_service._task(session, run.task_id, identity)
        return _receipt(run)


task_run_service = TaskRunService()
