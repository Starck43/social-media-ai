"""Task CRUD + run API endpoints."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_model_perm
from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask
from app.schemas.task import (
    TaskCreate,
    TaskPlanPreviewRequest,
    TaskPlanPreviewResponse,
    TaskPlanRunRequest,
    TaskRunRequest,
    TaskResponse,
    TaskUpdate,
)
from app.types import ActionType


def _web_api_payload(payload: dict | None) -> dict:
    """Remove repeat-bypass flags from API-authored task configuration."""
    safe_payload = dict(payload or {})
    safe_payload.pop("force_refresh", None)
    safe_payload.pop("force_reanalyze", None)
    return safe_payload


def _selection_payload(request, existing: dict | None) -> dict:
    """Merge the request's selection fields through the shared writer.

    `None` means "not supplied, keep what the task has"; an empty list clears.
    A malformed identity is a 400, never a stored filter that selects nothing.
    """
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.monitoring.selection import SelectionError

    try:
        return AgentTaskManager.build_selection_payload(
            tracked_users=getattr(request, "tracked_users", None),
            keywords=getattr(request, "keywords_list", None),
            keywords_match=getattr(request, "keywords_match", None),
            existing=existing,
        )
    except SelectionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _collection_payload(payload: dict | None, raw: dict | None, *, job_type: str) -> dict:
    """Validate and canonicalise `payload["collection"]` through the shared writer.

    `raw=None` clears the block. A malformed block is a 400 with the same
    bounded message the web form and CLI show, never a stored half-valid value.
    """
    from app.services.collection.intent import CollectionIntentError, apply_collection, collection_error_message

    try:
        return apply_collection(payload, raw, job_type=job_type)
    except CollectionIntentError as error:
        raise HTTPException(status_code=400, detail=collection_error_message(str(error))) from None


def _request_collection(request, payload: dict | None) -> dict | None:
    """The block the request explicitly carries, falling back to its payload."""
    if "collection" in getattr(request, "model_fields_set", set()):
        return getattr(request, "collection", None)
    return payload.get("collection") if isinstance(payload, dict) and "collection" in payload else None


router = APIRouter(tags=["tasks"])


@router.get("/entitlements", response_model=dict)
async def get_task_entitlements(
    _user = Depends(require_model_perm("agenttask", ActionType.VIEW)),
):
    """Inspect current workspace product grants without consuming usage."""
    from app.models.managers.entitlement_manager import EntitlementManager

    return await EntitlementManager().inspect()


async def _preview_entitlements(plan):
    from app.services.collection.entitlement_policy import preview_entitlements

    return await preview_entitlements({"task_plan": plan, "collection": plan.get("collection", {})})


@router.post("/plan", response_model=TaskPlanPreviewResponse)
async def preview_task_plan(
    request: TaskPlanPreviewRequest,
    _user = Depends(require_model_perm("agenttask", ActionType.CREATE)),
):
    """Normalize a social-task request without writing rows or calling a platform."""
    from app.services.task_plans import TaskPlanError, parse_task_plan, task_plan_block

    try:
        plan = parse_task_plan(request.task_plan)
    except (TaskPlanError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    if request.job_type not in {"collect", "analyze"}:
        raise HTTPException(status_code=400, detail="Unsupported job type for a social task plan")
    normalized = task_plan_block(plan)
    return TaskPlanPreviewResponse(
        task_plan=normalized,
        collection=normalized.get("collection", {}),
        warnings=["VK search availability depends on the configured application profile."],
        entitlements=await _preview_entitlements(normalized),
    )


@router.post("/run/plan", response_model=dict)
async def run_task_plan(
    request: TaskPlanRunRequest,
    _user = Depends(require_model_perm("agenttask", ActionType.CREATE)),
):
    """Launch one explicit social plan through the common idempotent runner."""
    from app.services.task_launch import launch_social_task
    from app.core.permissions import has_permission
    from app.jobs.source_activity import SourceJobBusy
    from app.models.managers.personal_billing_manager import PersonalBillingUnavailable

    # One-off plans create a task receipt, so callers need both rights even
    # though the legacy task-run route only requires update.
    if not has_permission(_user, "agenttask", ActionType.UPDATE):
        raise HTTPException(status_code=403, detail="Missing permission: agenttask.update")

    try:
        return await launch_social_task(
            request.task_plan,
            request_id=request.request_id,
            job_type=request.job_type,
        )
    except ValueError as error:
        from app.services.tenancy.entitlements import EntitlementDenied

        if isinstance(error, EntitlementDenied):
            raise HTTPException(status_code=403, detail=error.decision.to_dict()) from None
        raise HTTPException(status_code=400, detail=str(error)) from None
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from None
    except SourceJobBusy:
        raise HTTPException(status_code=409, detail="A source in this plan already has an active job") from None
    except PersonalBillingUnavailable:
        raise HTTPException(
            status_code=409,
            detail="Task was saved inactive; configure personal billing before retrying this request.",
        ) from None


def _task_response(t: AgentTask) -> TaskResponse:
    return TaskResponse(
        id=t.id,
        name=t.name,
        cron_expr=t.cron_expr,
        job_type=t.job_type,
        payload=t.payload or {},
        is_active=t.is_active,
        next_run_at=t.next_run_at,
        last_run_at=t.last_run_at,
        last_status=t.last_status,
        last_error=t.last_error,
        agent_scenario_id=t.agent_scenario_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    _user = Depends(require_model_perm("agenttask", ActionType.VIEW)),
):
    """List all tasks in the current workspace."""
    tasks = await AgentTask.objects.filter().order_by(AgentTask.id)
    return [_task_response(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    _user = Depends(require_model_perm("agenttask", ActionType.VIEW)),
):
    """Get a specific task by ID."""
    task = await AgentTask.objects.get(id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_response(task)


@router.get("/{task_id}/result", response_model=None)
async def get_task_result(
    task_id: int,
    job_id: Optional[int] = None,
    limit: int = Query(default=5, ge=1, le=10),
    _user = Depends(require_model_perm("agenttask", ActionType.VIEW)),
):
    """Return bounded task progress and permission-filtered result summaries."""
    from app.services.task_results import task_result

    return await task_result(task_id, job_id=job_id, limit=limit)


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    request: TaskCreate,
    _user = Depends(require_model_perm("agenttask", ActionType.CREATE)),
):
    """Create a new scheduled task."""
    from app.tasks.cron import next_run_at, resolve_tz
    from app.models.managers.tenant_manager import tenants

    # Check for duplicate name in workspace
    existing = await AgentTask.objects.filter(name=request.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Task '{request.name}' already exists")

    # A canonical plan owns its trigger and source scope. Legacy callers retain
    # the existing cron/source fields and semantics.
    payload = _collection_payload(
        _selection_payload(request, _web_api_payload(request.payload)),
        _request_collection(request, request.payload),
        job_type=request.job_type,
    )
    source_ids = list(request.source_ids)
    scenario_id = request.agent_scenario_id
    cron_expr = request.cron_expr
    if request.task_plan is not None:
        from app.services.task_plans import TaskPlanError, apply_task_plan, parse_task_plan

        try:
            plan = parse_task_plan(request.task_plan)
            planned_sources = list(plan.source_ids)
            planned_scenario = plan.scenario_id
            planned_cron = "@once" if plan.trigger.type == "once" else plan.trigger.cron
            if "source_ids" in request.model_fields_set:
                if not isinstance(request.source_ids, list) or source_ids != planned_sources:
                    raise TaskPlanError("source_ids must match task_plan.source_scope and source_ids")
            if scenario_id is not None and scenario_id != planned_scenario:
                raise TaskPlanError("agent_scenario_id must match task_plan.scenario_id")
            if cron_expr is not None and cron_expr != planned_cron:
                raise TaskPlanError("cron_expr must match task_plan.trigger")
            source_ids = planned_sources
            scenario_id = planned_scenario
            cron_expr = planned_cron
            payload = apply_task_plan(payload, request.task_plan, job_type=request.job_type)
        except (TaskPlanError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    if not cron_expr:
        raise HTTPException(status_code=400, detail="cron_expr or task_plan.trigger is required")
    if not AgentTask.objects.validate_cron(cron_expr):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {cron_expr}")

    # Get tenant timezone for next_run_at
    from app.core.tenant_context import current_tenant_id
    from app.services.task_plans import task_plan_timezone

    tid = current_tenant_id()
    tenant = await tenants.get(id=tid) if tid else None
    tz = task_plan_timezone(request.task_plan, resolve_tz(tenant))

    # Calculate next run
    if cron_expr.strip() == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, tz)

    if request.task_plan is None:
        # Keep the legacy relationship semantics while ensuring the task row
        # and any explicit source links commit together.
        from app.core.database import new_session
        from app.models.managers.agent_task_manager import AgentTaskManager

        session = new_session()
        try:
            async with session.begin():
                task = await AgentTask.objects.create(
                    session=session,
                    name=request.name.strip()[:100],
                    cron_expr=cron_expr,
                    job_type=request.job_type,
                    payload=payload,
                    agent_scenario_id=scenario_id,
                    is_active=request.is_active,
                    next_run_at=next_run,
                )
                if source_ids:
                    with tenant_scope(task.tenant_id), service_permission_scope("agenttask", "update"):
                        await AgentTaskManager().set_sources(task.id, source_ids, session=session)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        finally:
            await session.close()
    else:
        # New plan, task, scenario and source scope form one transaction. A
        # failed relationship write cannot leave an empty-link legacy task,
        # which would otherwise mean every active workspace source.
        from app.core.database import new_session

        from app.models.managers.agent_task_manager import AgentTaskManager

        session = new_session()
        try:
            async with session.begin():
                task = await AgentTask.objects.create(
                    session=session,
                    name=request.name.strip()[:100],
                    cron_expr=cron_expr,
                    job_type=request.job_type,
                    payload=payload,
                    agent_scenario_id=scenario_id,
                    is_active=False,
                    next_run_at=next_run,
                )
                with tenant_scope(task.tenant_id), service_permission_scope("agenttask", "update"):
                    await AgentTaskManager().set_sources(task.id, source_ids, session=session)
        except (TaskPlanError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        finally:
            await session.close()

        # Never activate a newly authored paid plan until its creator explicitly
        # accepts the existing task-payer contract. On unavailable billing the
        # saved task remains a recoverable inactive draft.
        if request.is_active:
            from app.services.billing import consent_current_user_as_task_payer
            from app.models.managers.personal_billing_manager import PersonalBillingError, PersonalBillingUnavailable

            try:
                await consent_current_user_as_task_payer(task.id)
            except (PersonalBillingError, PersonalBillingUnavailable) as error:
                raise HTTPException(
                    status_code=409,
                    detail=f"Task {task.id} was saved inactive; configure personal billing and consent before activation.",
                ) from error
            await AgentTask.objects.update_by_id(task.id, is_active=True)
            task = await AgentTask.objects.get(id=task.id)

    return _task_response(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    request: TaskUpdate,
    _user = Depends(require_model_perm("agenttask", ActionType.UPDATE)),
):
    """Update a task — only provided fields are changed."""
    task = await AgentTask.objects.get(id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    updates = request.model_dump(exclude_unset=True)
    task_plan_raw = updates.pop("task_plan", None)
    task_plan_supplied = "task_plan" in request.model_fields_set
    stored_plan = (task.payload or {}).get("task_plan") if isinstance(task.payload, dict) else None
    selection_fields = {
        key: updates.pop(key) for key in ("tracked_users", "keywords_list", "keywords_match") if key in updates
    }
    collection_supplied = "collection" in updates
    collection_raw = updates.pop("collection", None) if collection_supplied else None
    if "payload" in updates or selection_fields or collection_supplied or "job_type" in updates:
        base_payload = _web_api_payload(updates.get("payload", task.payload))
        if not collection_supplied:
            if "collection" in base_payload:
                collection_raw = base_payload["collection"]
            else:
                stored = _web_api_payload(task.payload)
                collection_raw = stored.get("collection") if "collection" in stored else None
        updates["payload"] = _collection_payload(
            _selection_payload(
                SimpleNamespace(
                    tracked_users=selection_fields.get("tracked_users"),
                    keywords_list=selection_fields.get("keywords_list"),
                    keywords_match=selection_fields.get("keywords_match"),
                ),
                base_payload,
            ),
            collection_raw,
            job_type=updates.get("job_type", task.job_type),
        )
    if "name" in updates:
        updates["name"] = updates["name"].strip()[:100]

    plan_source_ids = None
    if task_plan_supplied or stored_plan is not None:
        from app.services.task_plans import prepare_task_plan_update

        policy_input = dict(updates)
        if task_plan_supplied:
            policy_input["task_plan"] = task_plan_raw
        if collection_supplied:
            policy_input["collection"] = collection_raw
        try:
            mutation = prepare_task_plan_update(
                task.payload, policy_input, job_type=updates.get("job_type", task.job_type)
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        updates = mutation.changes
        if mutation.cleared:
            stored_plan = None
        if task_plan_supplied:
            plan_source_ids = updates.get("source_ids")

    # Validate cron if changing
    if "cron_expr" in updates:
        if not AgentTask.objects.validate_cron(updates["cron_expr"]):
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {updates['cron_expr']}")

        # Recalculate next_run_at
        from app.tasks.cron import next_run_at, resolve_tz
        from app.models.managers.tenant_manager import tenants
        from app.core.tenant_context import current_tenant_id
        from app.services.task_plans import task_plan_timezone

        tid = current_tenant_id()
        tenant = await tenants.get(id=tid) if tid else None
        tz = task_plan_timezone(
            task_plan_raw if task_plan_supplied else stored_plan,
            resolve_tz(tenant),
        )

        if updates["cron_expr"].strip() == "@once":
            updates["next_run_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
        else:
            updates["next_run_at"] = next_run_at(updates["cron_expr"], tz)

    # Update sources if provided
    source_ids = updates.pop("source_ids", None)
    if plan_source_ids is not None:
        source_ids = plan_source_ids

    if task_plan_supplied:
        from app.core.database import new_session
        from app.models.managers.agent_task_manager import AgentTaskManager

        session = new_session()
        try:
            with tenant_scope(task.tenant_id):
                async with session.begin():
                    await AgentTask.objects.update_by_id(task_id, session=session, **updates)
                    with service_permission_scope("agenttask", "update"):
                        await AgentTaskManager().set_sources(task_id, source_ids or [], session=session)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        finally:
            await session.close()
    else:
        from app.core.database import new_session
        from app.models.managers.agent_task_manager import AgentTaskManager

        # Resolve both row and relationship writes in the same persisted
        # workspace, including legacy callers without a task_plan envelope.
        session = new_session()
        try:
            with tenant_scope(task.tenant_id):
                async with session.begin():
                    if updates:
                        await AgentTask.objects.update_by_id(task_id, session=session, **updates)
                    if source_ids is not None:
                        await AgentTaskManager().set_sources(task_id, source_ids, session=session)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        finally:
            await session.close()

    task = await AgentTask.objects.get(id=task_id)
    return _task_response(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int,
    _user = Depends(require_model_perm("agenttask", ActionType.DELETE)),
):
    """Delete a task."""
    task = await AgentTask.objects.get(id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await AgentTask.objects.delete(id=task_id)


@router.patch("/{task_id}/pause")
async def pause_task(
    task_id: int,
    resume: bool = False,
    _user = Depends(require_model_perm("agenttask", ActionType.UPDATE)),
):
    """Pause or resume a task."""
    task = await AgentTask.objects.get(id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # The manager serializes schedule changes with source admission. Re-enter
    # the persisted tenant of the already-scoped task for that lock.
    with tenant_scope(task.tenant_id):
        await AgentTask.objects.update_by_id(task_id, is_active=resume)
    task = await AgentTask.objects.get(id=task_id)
    return _task_response(task)


@router.post("/{task_id}/run")
async def run_task(
    task_id: int,
    request: TaskRunRequest = TaskRunRequest(),
    _user = Depends(require_model_perm("agenttask", ActionType.UPDATE)),
):
    """Run a task immediately (one-off execution)."""
    from app.jobs.dispatcher import run_job_now
    from app.jobs.enqueue import enqueue_task_run

    task = await AgentTask.objects.get(id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    from app.jobs.source_activity import SourceJobBusy

    try:
        job = await enqueue_task_run(
            task,
            extra_payload={"_one_off": True, "force_refresh": False, "force_reanalyze": False},
            initiated_by_user_id=getattr(_user, "id", None),
            supersede_unfinished=True,
        )
    except SourceJobBusy as busy:
        raise HTTPException(status_code=409, detail={"code": "source_busy", "job_id": busy.job.id}) from None
    except ValueError as error:
        from app.services.tenancy.entitlements import EntitlementDenied

        if isinstance(error, EntitlementDenied):
            raise HTTPException(status_code=403, detail=error.decision.to_dict()) from None
        raise
    outcome = await run_job_now(job.id, allow_retry=False) or {}

    return {
        "job_id": job.id,
        "task_id": task_id,
        "status": outcome.get("status", "unknown"),
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }


@router.post("/run", response_model=dict)
async def run_task_oneoff(
    job_type: str,
    payload: dict = {},
    source_ids: list[int] = [],
    scenario_id: Optional[int] = None,
    _user = Depends(require_model_perm("agenttask", ActionType.UPDATE)),
):
    """Run a one-off task (creates a temporary @once task)."""
    from datetime import datetime, timezone
    import secrets

    from app.jobs.dispatcher import run_job_now
    from app.jobs.enqueue import enqueue_task_run
    from app.models.managers.agent_task_manager import AgentTaskManager

    # Create temporary task
    stamp = datetime.now(timezone.utc)
    safe_payload = _web_api_payload(payload)
    safe_payload = _collection_payload(
        safe_payload,
        safe_payload.get("collection") if "collection" in safe_payload else None,
        job_type=job_type,
    )
    task = await AgentTask.objects.create(
        name=f"one-off-{job_type}-{stamp:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        cron_expr="@once",
        job_type=job_type,
        payload=safe_payload,
        agent_scenario_id=scenario_id,
        is_active=False,
        next_run_at=None,
    )

    if source_ids:
        with tenant_scope(task.tenant_id):
            await AgentTaskManager().add_sources(task.id, source_ids)

    from app.jobs.source_activity import SourceJobBusy

    try:
        job = await enqueue_task_run(
            task,
            extra_payload={"_one_off": True, "force_refresh": False, "force_reanalyze": False},
            initiated_by_user_id=getattr(_user, "id", None),
        )
    except SourceJobBusy as busy:
        raise HTTPException(status_code=409, detail={"code": "source_busy", "job_id": busy.job.id}) from None
    except ValueError as error:
        from app.services.tenancy.entitlements import EntitlementDenied

        if isinstance(error, EntitlementDenied):
            raise HTTPException(status_code=403, detail=error.decision.to_dict()) from None
        raise
    outcome = await run_job_now(job.id, allow_retry=False) or {}

    return {
        "job_id": job.id,
        "task_id": task.id,
        "status": outcome.get("status", "unknown"),
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }
