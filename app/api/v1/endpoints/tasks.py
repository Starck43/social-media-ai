"""Task CRUD + run API endpoints."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_model_perm
from app.core.tenant_context import tenant_scope
from app.models import AgentTask
from app.schemas.task import TaskCreate, TaskUpdate, TaskRunRequest, TaskResponse
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

    # Validate cron
    if not AgentTask.objects.validate_cron(request.cron_expr):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {request.cron_expr}")

    # Get tenant timezone for next_run_at
    from app.core.tenant_context import current_tenant_id
    tid = current_tenant_id()
    tenant = await tenants.get(id=tid) if tid else None
    tz = resolve_tz(tenant)

    # Calculate next run
    if request.cron_expr.strip() == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(request.cron_expr, tz)

    task = await AgentTask.objects.create(
        name=request.name.strip()[:100],
        cron_expr=request.cron_expr,
        job_type=request.job_type,
        payload=_collection_payload(
            _selection_payload(request, _web_api_payload(request.payload)),
            _request_collection(request, request.payload),
            job_type=request.job_type,
        ),
        agent_scenario_id=request.agent_scenario_id,
        is_active=request.is_active,
        next_run_at=next_run,
    )

    # Link sources if provided
    if request.source_ids:
        from app.models.managers.agent_task_manager import AgentTaskManager
        with tenant_scope(task.tenant_id):
            await AgentTaskManager().add_sources(task.id, request.source_ids)

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

    # Validate cron if changing
    if "cron_expr" in updates:
        if not AgentTask.objects.validate_cron(updates["cron_expr"]):
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {updates['cron_expr']}")

        # Recalculate next_run_at
        from app.tasks.cron import next_run_at, resolve_tz
        from app.models.managers.tenant_manager import tenants
        from app.core.tenant_context import current_tenant_id

        tid = current_tenant_id()
        tenant = await tenants.get(id=tid) if tid else None
        tz = resolve_tz(tenant)

        if updates["cron_expr"].strip() == "@once":
            updates["next_run_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
        else:
            updates["next_run_at"] = next_run_at(updates["cron_expr"], tz)

    # Update sources if provided
    source_ids = updates.pop("source_ids", None)

    if updates:
        await AgentTask.objects.update_by_id(task_id, **updates)

    if source_ids is not None:
        from app.models.managers.agent_task_manager import AgentTaskManager
        # The task was loaded through the request's tenant-guarded queryset;
        # re-enter its persisted tenant for the manager's admission lock.
        with tenant_scope(task.tenant_id):
            await AgentTaskManager().add_sources(task_id, source_ids)

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
    outcome = await run_job_now(job.id, allow_retry=False) or {}

    return {
        "job_id": job.id,
        "task_id": task.id,
        "status": outcome.get("status", "unknown"),
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }
