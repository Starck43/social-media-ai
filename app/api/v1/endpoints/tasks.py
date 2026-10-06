"""Task CRUD + run API endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_model_perm
from app.models import AgentTask
from app.schemas.task import TaskCreate, TaskUpdate, TaskRunRequest, TaskResponse
from app.types import ActionType

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
        payload=request.payload or {},
        agent_scenario_id=request.agent_scenario_id,
        is_active=request.is_active,
        next_run_at=next_run,
    )

    # Link sources if provided
    if request.source_ids:
        from app.models.managers.agent_task_manager import AgentTaskManager
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

    await AgentTask.objects.delete(task_id)


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

    # Merge payload if provided
    payload = dict(task.payload or {})
    if request.payload:
        payload.update(request.payload)

    job = await enqueue_task_run(task, extra_payload={"_one_off": True})
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
    task = await AgentTask.objects.create(
        name=f"one-off-{job_type}-{stamp:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        cron_expr="@once",
        job_type=job_type,
        payload=payload,
        agent_scenario_id=scenario_id,
        is_active=False,
        next_run_at=None,
    )

    if source_ids:
        await AgentTaskManager().add_sources(task.id, source_ids)

    job = await enqueue_task_run(task, extra_payload={"_one_off": True})
    outcome = await run_job_now(job.id, allow_retry=False) or {}

    return {
        "job_id": job.id,
        "task_id": task.id,
        "status": outcome.get("status", "unknown"),
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }
