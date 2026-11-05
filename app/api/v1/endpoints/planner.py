"""Typed, tenant-scoped planner CRUD, separate from scheduled agent tasks."""

from typing import Literal

from fastapi import Depends, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, validate_business_routes
from app.schemas.business import BusinessPage
from app.services.planner.contracts import (
    CompleteOut,
    DeletedOut,
    TaskCreate,
    TaskOut,
    TaskReschedule,
    TaskUpdate,
    TaskVersion,
)
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.types import ActionType

router = business_router("planner", permission=require_model_perm("plannertask", ActionType.VIEW))


async def invoke(operation):
    try:
        return await operation
    except PlannerUnavailable:
        raise HTTPException(404) from None
    except PlannerConflict:
        raise HTTPException(409) from None
    except PermissionError:
        raise HTTPException(403) from None
    except ValueError:
        raise HTTPException(422) from None


@router.get("/tasks", response_model=BusinessPage[TaskOut])
async def list_tasks(
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
    status: Literal["todo", "in_progress", "done", "cancelled"] | None = None,
):
    return await invoke(planner_service.list(limit=limit, before_id=before_id, status=status))


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: int = Path(gt=0)):
    return await invoke(planner_service.get(task_id))


@router.post(
    "/tasks",
    response_model=TaskOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))],
)
async def create_task(request: TaskCreate):
    return await invoke(planner_service.create(request))


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskOut,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def update_task(request: TaskUpdate, task_id: int = Path(gt=0)):
    return await invoke(planner_service.update(task_id, request))


@router.post(
    "/tasks/{task_id}/reschedule",
    response_model=TaskOut,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def reschedule_task(request: TaskReschedule, task_id: int = Path(gt=0)):
    return await invoke(planner_service.reschedule(task_id, request))


@router.post(
    "/tasks/{task_id}/complete",
    response_model=CompleteOut,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def complete_task(request: TaskVersion, task_id: int = Path(gt=0)):
    return await invoke(planner_service.complete(task_id, request.expected_version))


@router.delete(
    "/tasks/{task_id}",
    response_model=DeletedOut,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.DELETE))],
)
async def delete_task(request: TaskVersion, task_id: int = Path(gt=0)):
    return await invoke(planner_service.delete(task_id, request.expected_version))


validate_business_routes(router)
