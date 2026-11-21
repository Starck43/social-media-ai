"""Versioned canonical task API (P22-T07).

The v1 `/tasks` namespace stays the social-monitoring surface; `/api/v2/tasks`
exposes the canonical local task so the web UI, the CLI and the agent tools act
on one identity. Every mutation delegates to the services accepted in P22-T03…
T06 — nothing here re-implements rights, versions or admission.
"""

import json
from typing import Literal

from fastapi import Depends, Header, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, validate_business_routes
from app.core.tenant_context import current_tenant_id
from app.schemas.business import BusinessPage
from app.schemas.work_tasks import (
    DecisionConfirmation,
    DecisionOutcome,
    DecisionPreview,
    DecisionRequest,
    DeleteConfirmation,
    DeleteOutcome,
    DeletePreview,
    DeleteScope,
    LegacyTarget,
    PlanDraft,
    PlanPreview,
    RunReceipt,
    RunReceiptPage,
    WorkTaskActions,
    WorkTaskCompletion,
    WorkTaskCreate,
    WorkTaskUpdate,
    WorkTaskVersion,
)
from app.services.commands.task_decisions import task_decision_service
from app.services.commands.task_deletion import task_deletion_service
from app.services.execution_plan_contracts import ExecutionPlanError
from app.services.planner.canonical_tasks import CanonicalTaskOut, task_service
from app.services.planner.legacy_migration import resolve_legacy_task
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.services.task_execution_plans import execution_plan_service
from app.services.task_run_admission import TaskRunAdmissionError, task_run_service
from app.services.workspace.task_surface import canonical_task_actions
from app.types import ActionType

router = business_router("tasks", permission=require_model_perm("plannertask", ActionType.VIEW))

# Admission codes that are a state conflict rather than a malformed request.
CONFLICT_CODES = frozenset({"request_key_conflict", "occurrence_conflict", "active_run", "busy_run"})


async def invoke(operation):
    """Translate service refusals into the documented status codes, never details."""
    try:
        return await operation
    except PlannerUnavailable:
        raise HTTPException(404) from None
    except TaskRunAdmissionError as error:
        raise HTTPException(404 if "unavailable" in error.code else 422) from None
    except PlannerConflict:
        raise HTTPException(409) from None
    except PermissionError:
        raise HTTPException(403) from None
    except ExecutionPlanError:
        raise HTTPException(422) from None
    except ValueError:
        raise HTTPException(422) from None


async def _admission(operation):
    """Run-now: replay codes are conflicts, malformed keys invalid requests."""

    async def run():
        try:
            return await operation
        except TaskRunAdmissionError as error:
            raise HTTPException(409 if error.code in CONFLICT_CODES else 422) from None
        except PermissionError:
            raise HTTPException(403) from None
        except PlannerUnavailable:
            raise HTTPException(404) from None
        except PlannerConflict:
            raise HTTPException(409) from None
        except (ExecutionPlanError, ValueError):
            raise HTTPException(422) from None

    return await run()


@router.get("", response_model=BusinessPage[CanonicalTaskOut])
async def list_tasks(
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
    status: Literal["todo", "in_progress", "done", "cancelled"] | None = None,
    view: Literal["mine", "team"] | None = None,
):
    return await invoke(task_service.list(limit=limit, before_id=before_id, status=status, view=view))


@router.get("/{task_id}", response_model=CanonicalTaskOut)
async def get_task(task_id: int = Path(gt=0)):
    return await invoke(task_service.get(task_id))


@router.post(
    "",
    response_model=CanonicalTaskOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))],
)
async def create_task(request: WorkTaskCreate):
    return await invoke(task_service.create(request))


@router.patch(
    "/{task_id}",
    response_model=CanonicalTaskOut,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def update_task(request: WorkTaskUpdate, task_id: int = Path(gt=0)):
    return await invoke(task_service.update(task_id, request))


@router.post(
    "/{task_id}/plan/preview",
    response_model=PlanPreview,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def preview_plan(request: PlanDraft, task_id: int = Path(gt=0)):
    return await invoke(execution_plan_service.preview(task_id, json.dumps(request.plan)))


@router.post(
    "/{task_id}/complete",
    response_model=WorkTaskCompletion,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def complete_task(request: WorkTaskVersion, task_id: int = Path(gt=0)):
    return await invoke(task_service.complete(task_id, request.expected_version))


@router.post(
    "/{task_id}/delete/preview",
    response_model=DeletePreview,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.DELETE))],
)
async def preview_delete(request: DeleteScope, task_id: int = Path(gt=0)):
    return await invoke(task_deletion_service.preview(task_id, scope=request.scope))


@router.post(
    "/{task_id}/delete",
    response_model=DeleteOutcome,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.DELETE))],
)
async def confirm_delete(request: DeleteConfirmation, task_id: int = Path(gt=0)):
    return await invoke(task_deletion_service.confirm(task_id, scope=request.scope, confirmation=request.confirmation))


@router.post(
    "/{task_id}/runs",
    response_model=RunReceipt,
    status_code=202,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def run_task(
    task_id: int = Path(gt=0),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    return await _admission(
        task_run_service.admit_manual(
            task_id,
            expected_revision=await _current_revision(task_id),
            request_key=idempotency_key,
        )
    )


async def _current_revision(task_id: int) -> int:
    """A run is admitted against the plan revision the caller can see right now."""
    reference = await execution_plan_service.get_reference(task_id)
    if reference is None:
        raise HTTPException(409)
    return reference.revision


@router.get("/{task_id}/runs", response_model=RunReceiptPage)
async def list_runs(
    task_id: int = Path(gt=0),
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
):
    return await invoke(task_run_service.list_receipts(task_id, limit=limit, before_id=before_id))


@router.get("/{task_id}/actions", response_model=WorkTaskActions)
async def task_actions(task_id: int = Path(gt=0)):
    return await invoke(canonical_task_actions(task_id))


@router.post(
    "/{task_id}/decisions/preview",
    response_model=DecisionPreview,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def preview_decision(request: DecisionRequest, task_id: int = Path(gt=0)):
    return await invoke(task_decision_service.preview(task_id, request.reference, outcome=request.outcome))


@router.post(
    "/{task_id}/decisions",
    response_model=DecisionOutcome,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def decide(request: DecisionConfirmation, task_id: int = Path(gt=0)):
    return await invoke(task_decision_service.decide(task_id, confirmation=request.confirmation, reason=request.reason))


@router.get("/legacy/{namespace}/{legacy_id}", response_model=LegacyTarget)
async def resolve_legacy(namespace: str = Path(min_length=1, max_length=64), legacy_id: int = Path(gt=0)):
    """Opaque legacy URL → canonical task; unresolvable or invisible is one 404."""
    from app.core.database import async_session_maker

    tenant_id = current_tenant_id()
    async with async_session_maker() as session:
        task = await resolve_legacy_task(session, tenant_id=tenant_id, namespace=namespace, legacy_id=legacy_id)
        if task is None:
            raise HTTPException(404)
        projected = await task_service.get(task.id, session=session)
    return {"namespace": namespace, "legacy_id": legacy_id, "task": projected}


validate_business_routes(router)
