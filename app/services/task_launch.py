"""Authenticated, idempotent admission of one-off social research tasks.

A request key identifies an admission, not a promise of exactly-once external
execution. Its durable receipt belongs to the task and survives job pruning.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import ValidationError

from app.core.permissions import get_current_user, has_permission, permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass
from app.services.task_contracts import (
    TASK_REQUEST_ID_PATTERN,
    TaskLaunchArguments,
    TaskLaunchCommand,
    TaskLaunchError,
    TaskLaunchResult,
    present_task_launch_result,
)
from app.services.task_plans import apply_task_plan, parse_task_plan, task_plan_block
from app.types import ActionType

_REQUEST_KEY = re.compile(TASK_REQUEST_ID_PATTERN.removeprefix("^").removesuffix("$"))


def launch_identity(tenant_id: int, actor_id: int, request_id: str, plan: Mapping, job_type: str) -> tuple[str, str]:
    """Scope a stable request key to the actor/workspace and bind its arguments."""
    if not isinstance(request_id, str) or not _REQUEST_KEY.fullmatch(request_id):
        raise ValueError("request_id must contain 8–128 ASCII letters, digits, '.', '_', ':' or '-'")
    key = hashlib.sha256(f"{tenant_id}:{actor_id}:{request_id}".encode()).hexdigest()
    serialized = json.dumps({"plan": plan, "job_type": job_type}, sort_keys=True, separators=(",", ":"))
    return key, hashlib.sha256(serialized.encode()).hexdigest()


async def _fresh_actor():
    """Reload web membership rights; a caller's payload never supplies identity."""
    from app.models import Tenant, User
    from app.models.managers.tenant_manager import tenant_users

    actor = get_current_user()
    actor_id = getattr(actor, "id", None)
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or type(actor_id) is not int or actor_id <= 0:
        raise PermissionError("An authenticated workspace member is required")
    user = await User.objects.get(id=actor_id, is_active=True)
    tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
    members = await tenant_users.web_memberships(actor_id) if user is not None and tenant is not None else []
    member = next((item for item in members if item.tenant_id == tenant_id), None)
    if member is None:
        raise PermissionError("An active workspace membership is required")
    grants = await tenant_users.member_grants(member)
    required = {
        ("agenttask", ActionType.CREATE),
        ("agenttask", ActionType.UPDATE),
        ("source", ActionType.ANALYZE),
    }
    if not required.issubset(grants):
        raise PermissionError("Task creation, execution and source analysis permissions are required")
    return user, tenant, grants


async def execute_task_launch(command: TaskLaunchCommand) -> TaskLaunchResult:
    """Save and queue one typed bounded task command, or return its receipt.

    No action/payload/recipient overrides are accepted. Recurrence and sends
    stay on their existing confirmed paths. An inactive draft is retained when
    personal payer setup fails so the user can inspect and retry the same key.
    """
    try:
        arguments = TaskLaunchArguments(request_id=command.request_id, plan=dict(command.plan))
    except (TypeError, ValidationError) as error:
        raise TaskLaunchError("request_invalid", cause=error) from None
    if command.job_type not in {"collect", "analyze"}:
        raise TaskLaunchError("request_invalid")

    raw_plan = arguments.plan
    request_id = arguments.request_id
    job_type = command.job_type
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.jobs.source_activity import SourceJobBusy
    from app.models import AgentScenario, AgentTask, Platform, Source, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.job_manager import JobManager
    from app.services.billing.payer import consent_current_user_as_task_payer

    plan = parse_task_plan(raw_plan)
    if plan.trigger.type != "once":
        raise TaskLaunchError("schedule_confirmation_required")
    if job_type == "analyze" and plan.scenario_id is None:
        raise TaskLaunchError("scenario_required")
    canonical = task_plan_block(plan)
    actor, tenant, grants = await _fresh_actor()
    key, fingerprint = launch_identity(tenant.id, actor.id, request_id, canonical, job_type)
    payload = apply_task_plan({}, canonical, job_type=job_type)
    if job_type == "analyze":
        payload["collect_before_analyze"] = True

    with permission_scope(actor, member_grants=grants):
        # Preserve the manager's own permission checks even when called directly.
        if not has_permission(actor, "agenttask", ActionType.CREATE):
            raise PermissionError("Task creation is not permitted")
        async with async_session_maker() as session:
            async with session.begin():
                # The gate serializes concurrent identical creations with source
                # admission. The unique launch key is the database backstop.
                await JobManager.lock_source_admission(session, tenant.id)
                task = (
                    await session.execute(
                        select(AgentTask).where(AgentTask.tenant_id == tenant.id, AgentTask.launch_key == key)
                    )
                ).scalar_one_or_none()
                if task is not None:
                    if task.launch_fingerprint != fingerprint:
                        raise TaskLaunchError("request_id_conflict")
                    task_id = task.id
                    already_launched = task.launch_job_id is not None
                else:
                    workspace = (
                        await session.execute(select(Tenant).where(Tenant.id == tenant.id, Tenant.is_active.is_(True)))
                    ).scalar_one_or_none()
                    if workspace is None:
                        raise PermissionError("Workspace is inactive")
                    ceiling = workspace.effective_limits().get("max_tasks")
                    if ceiling is not None:
                        count = (
                            await session.execute(
                                select(func.count(AgentTask.id)).where(AgentTask.tenant_id == tenant.id)
                            )
                        ).scalar_one()
                        if count >= ceiling:
                            raise TaskLaunchError("workspace_task_limit")
                    if plan.scenario_id is not None:
                        scenario = (
                            await session.execute(
                                select(AgentScenario.id).where(
                                    AgentScenario.id == plan.scenario_id,
                                    AgentScenario.tenant_id == tenant.id,
                                    AgentScenario.is_active.is_(True),
                                )
                            )
                        ).scalar_one_or_none()
                        if scenario is None:
                            raise TaskLaunchError("scenario_unavailable")
                    if plan.source_ids:
                        selected = (
                            (
                                await session.execute(
                                    select(Source.id)
                                    .join(Platform, Platform.id == Source.platform_id)
                                    .where(
                                        Source.id.in_(plan.source_ids),
                                        Source.tenant_id == tenant.id,
                                        Source.is_active.is_(True),
                                        Platform.platform_type.in_(plan.platforms),
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if set(selected) != set(plan.source_ids):
                            raise TaskLaunchError("source_unavailable")
                    task = await AgentTask.objects.create(
                        name=f"{(plan.goal or 'Social research')[:65]} [{key[:12]}]",
                        cron_expr="@once",
                        job_type=job_type,
                        payload=payload,
                        is_active=False,
                        next_run_at=None,
                        agent_scenario_id=plan.scenario_id,
                        session=session,
                    )
                    task.launch_key = key
                    task.launch_fingerprint = fingerprint
                    await AgentTask.objects.set_sources(task.id, list(plan.source_ids), session=session)
                    await session.flush()
                    task_id = task.id
                    already_launched = False

        if not already_launched:
            # This is the existing personal-account consent path. It rechecks
            # active membership and task rights and never guesses another payer.
            await consent_current_user_as_task_payer(task_id)
        receipt = await AgentTaskManager()._admit_task_run(
            task_id, now=datetime.now(timezone.utc), initiated_by_user_id=actor.id, launch_request_key=key
        )
        if receipt is None:
            raise TaskLaunchError("launch_unconfirmed")
        if receipt.get("status") == "busy":
            raise SourceJobBusy(receipt["job"])
        if receipt.get("status") not in {"enqueued", "replayed"}:
            raise TaskLaunchError("launch_unconfirmed")
        job = receipt.get("job")
        job_id = getattr(job, "id", None) or receipt.get("job_id")
        raw_job_status = getattr(job, "status", None) if job is not None else None
        if raw_job_status is None:
            normalized_job_status = "unavailable"
        elif raw_job_status in {"pending", "running", "done", "failed"}:
            normalized_job_status = raw_job_status
        else:
            normalized_job_status = "unknown"
        return TaskLaunchResult(
            task_id=task_id,
            job_id=job_id,
            status="replayed" if receipt["status"] == "replayed" else "queued",
            job_status=normalized_job_status,
        )


async def launch_social_task(
    raw_plan: Mapping[str, Any], *, request_id: str, job_type: str = "analyze"
) -> dict[str, Any]:
    """Compatibility adapter returning the existing task-launch receipt shape."""
    try:
        arguments = TaskLaunchArguments(request_id=request_id, plan=dict(raw_plan))
        command = arguments.to_command(job_type=job_type)
    except (TypeError, ValidationError, ValueError) as error:
        if isinstance(error, TaskLaunchError):
            raise
        raise TaskLaunchError("request_invalid", cause=error) from None
    return present_task_launch_result(await execute_task_launch(command))
