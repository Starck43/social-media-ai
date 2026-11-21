"""Execute a canonical plan using the queue's acquired generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.pii import decrypt_pii, encrypt_pii
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.catalog.task_capabilities import Direction
from app.integrations.task_resources import ScopedResource, resolve_task_resource
from app.jobs.claim_outcomes import JobClaimLostError
from app.jobs.progress import current_claim
from app.models import Job, User
from app.models.managers.job_manager import JobManager
from app.models.planner.task_run import TaskRun
from app.services.execution_plan_contracts import ExecutionPlan, ExecutionPlanError, parse_execution_plan
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerUnavailable
from app.types import ActionType

JOB_TYPE = "task.execute"
MAX_CHECKPOINT_BYTES = 512 * 1024


class TaskStepRetry(RuntimeError):
    """A known rolled-back database transaction can retry on the same Run."""


class TaskStepUnavailable(RuntimeError):
    """A supported operation could not produce its bounded output."""


class TaskRunStopped(RuntimeError):
    """An authorization or validation check stopped execution."""


@dataclass(frozen=True)
class StepContext:
    run_id: int
    tenant_id: int
    principal_id: int
    payer_user_id: int | None
    effective_at: datetime
    methodology: dict = field(repr=False)
    plan: ExecutionPlan = field(repr=False)


def validate_runtime_plan(plan: ExecutionPlan) -> None:
    from app.services.task_run_operations import OPERATIONS

    if any(step.operation not in OPERATIONS for step in plan.steps):
        raise ExecutionPlanError("unsupported_execution_operation")
    if plan.legacy_effects is not None:
        raise ExecutionPlanError("unsupported_execution_effects")


async def lock_run(session, tenant_id, run_id, job_id) -> TaskRun | None:
    if run_id is None:
        return None
    return await session.scalar(
        select(TaskRun)
        .where(TaskRun.tenant_id == tenant_id, TaskRun.id == run_id, TaskRun.job_id == job_id)
        .with_for_update()
    )


async def project_job_status(session, tenant_id, run_id, job_id, status, *, code=None):
    """Mirror a Job transition while the caller still owns its row lock."""
    run = await lock_run(session, tenant_id, run_id, job_id)
    if run is None:
        return
    run.status = status
    if code is not None and not (run.result or {}).get("code"):
        run.result = {**(run.result or {}), "code": code}
    if status in {"done", "failed", "cancelled", "unknown"}:
        run.checkpoint_encrypted = None


async def _locked_run(session, claim):
    if claim is None or claim.job_type != JOB_TYPE or current_tenant_id() != claim.tenant_id or is_bypass():
        raise PermissionError("task_claim_required")
    job = await session.scalar(
        select(Job).where(Job.status == "running", *JobManager._claim_predicates(Job, claim)).with_for_update()
    )
    if job is None:
        raise JobClaimLostError(claim)
    run = await lock_run(session, job.tenant_id, job.task_run_id, job.id)
    if run is None or run.status != "running":
        raise TaskRunStopped("task_run_unavailable")
    return run


async def _resolve_bindings(session, run, plan, step):
    names = set(step.bindings)
    resources = []
    for binding in plan.bindings:
        if binding.name not in names:
            continue
        resolved = await resolve_task_resource(
            session,
            tenant_id=run.tenant_id,
            actor_id=run.principal_id,
            capability_id=binding.capability,
            direction=Direction.READ,
            selection=binding.selection(),
            canonical_vk=True,
        )
        if not isinstance(resolved, ScopedResource):
            raise TaskRunStopped(resolved.value)
        resources.append(resolved)
    return tuple(resources)


async def _perform_step(session, run, plan, methodology, step, outputs):
    from app.services.task_run_admission import authorize_task_run
    from app.services.task_run_operations import OPERATIONS

    user = await session.scalar(select(User).where(User.id == run.principal_id, User.is_active.is_(True)))
    if user is None or run.payer_user_id is not None:
        raise TaskRunStopped("task_authorization_lost")
    try:
        with permission_scope(user):
            identity = await task_service._actor(session, ActionType.VIEW, member_channel="web")
        with permission_scope(user, member_grants=workspace_grants(identity[2])):
            task = await authorize_task_run(session, run.task_id)
            if task.status in {"done", "cancelled"}:
                raise TaskRunStopped("task_authorization_lost")
            resources = await _resolve_bindings(session, run, plan, step)
            context = StepContext(
                run.id, run.tenant_id, run.principal_id, run.payer_user_id, run.effective_at, methodology, plan
            )
            if plan.dry_run:
                return None
            inputs = {
                port: tuple(outputs[name] for name in names if name in outputs) for port, names in step.inputs.items()
            }
            if any(not values for values in inputs.values()):
                raise TaskStepUnavailable("upstream_unavailable")
            return await OPERATIONS[step.operation](session, context, step, inputs, resources)
    except (PermissionError, PlannerUnavailable):
        raise TaskRunStopped("task_authorization_lost") from None


def _snapshot(run):
    data = json.loads(decrypt_pii(run.admitted_inputs_encrypted))
    plan = parse_execution_plan(json.dumps(data["plan"]))
    validate_runtime_plan(plan)
    return plan, data["methodology"]


async def _execute_step(claim, plan, methodology, step):
    async with async_session_maker() as session, session.begin():
        run = await _locked_run(session, claim)
        result = run.result or {}
        steps = dict(result.get("steps", {}))
        if step.name in steps:
            return
        outputs = json.loads(decrypt_pii(run.checkpoint_encrypted)) if run.checkpoint_encrypted else {}
        try:
            async with session.begin_nested():
                output = await _perform_step(session, run, plan, methodology, step, outputs)
                if output is not None:
                    outputs[step.name] = output.output
                    encoded = json.dumps(outputs, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    if len(encoded.encode()) > MAX_CHECKPOINT_BYTES:
                        raise TaskRunStopped("task_checkpoint_limit")
                    run.checkpoint_encrypted = encrypt_pii(encoded)
        except TaskStepUnavailable:
            if step.on_failure != "continue_partial":
                raise TaskRunStopped("task_step_unavailable") from None
            steps[step.name] = {"status": "unavailable"}
        else:
            steps[step.name] = {"status": "previewed"} if output is None else {"status": "done", "count": output.count}
        run.result = {"steps": steps}


async def handle_task_run(_payload):
    """Use only the acquired Job and encrypted admitted inputs as authority."""
    claim = current_claim()
    try:
        async with async_session_maker() as session, session.begin():
            run = await _locked_run(session, claim)
            plan, methodology = _snapshot(run)
        for step in plan.steps:
            await _execute_step(claim, plan, methodology, step)
        async with async_session_maker() as session, session.begin():
            run = await _locked_run(session, claim)
            steps = run.result["steps"]
            if all(step["status"] == "unavailable" for step in steps.values()):
                raise TaskRunStopped("task_step_unavailable")
            coverage = "partial" if any(step["status"] == "unavailable" for step in steps.values()) else "complete"
            run.result = {**run.result, "coverage": coverage}
            return {"status": "previewed" if plan.dry_run else "done", "coverage": coverage}
    except (ExecutionPlanError, TaskRunStopped) as error:
        async with async_session_maker() as session, session.begin():
            run = await _locked_run(session, claim)
            run.result = {**(run.result or {}), "code": str(error)}
        return {"status": "failed", "error": "task_execution_refused"}
    except DBAPIError as error:
        # PostgreSQL confirms these transactions could not have committed. The
        # bounded read adapters and local transaction need no effect replay.
        if getattr(error.orig, "sqlstate", None) in {"40001", "40P01"}:
            raise TaskStepRetry from None
        raise
