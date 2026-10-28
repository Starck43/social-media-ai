"""Source CRUD API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_model_perm
from app.models import Platform, Source
from app.schemas.source import SourceCreate, SourceResponse, SourceUpdate
from app.types import ActionType, SourceType

router = APIRouter(tags=["sources"])


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user=Depends(require_model_perm("source", ActionType.VIEW)),
):
    """List all sources in the current workspace."""
    sources = await Source.objects.filter().order_by(Source.id).limit(limit).offset(offset)
    return [
        SourceResponse(
            id=s.id,
            name=s.name,
            platform_id=s.platform_id,
            source_type=s.source_type,
            external_id=s.external_id,
            params=s.params or {},
            is_active=s.is_active,
            last_checked=s.last_checked,
            last_item_id=s.last_item_id,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sources
    ]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: int,
    _user=Depends(require_model_perm("source", ActionType.VIEW)),
):
    """Get a specific source by ID."""
    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return SourceResponse(
        id=source.id,
        name=source.name,
        platform_id=source.platform_id,
        source_type=source.source_type,
        external_id=source.external_id,
        params=source.params or {},
        is_active=source.is_active,
        last_checked=source.last_checked,
        last_item_id=source.last_item_id,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    request: SourceCreate,
    _user=Depends(require_model_perm("source", ActionType.CREATE)),
):
    """Create a new source in the current workspace."""
    # Verify platform exists
    platform = await Platform.objects.get(id=request.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Платформа {request.platform_id} не найдена")

    # `source_type` arrives as a name ("GROUP"). The enum column passes an
    # unknown string through to PostgreSQL, so without this check a typo is a
    # 500 at flush instead of a named client error.
    source_type = SourceType.get_by_name(request.source_type.strip().upper() if request.source_type else "")
    if source_type is None:
        raise HTTPException(status_code=422, detail=f"Неизвестный source_type: {request.source_type}")

    source = await Source.objects.create(
        platform_id=request.platform_id,
        name=request.name.strip()[:255],
        source_type=source_type,
        external_id=request.external_id.strip()[:100],
        params=request.params or {},
    )

    return SourceResponse(
        id=source.id,
        name=source.name,
        platform_id=source.platform_id,
        source_type=source.source_type,
        external_id=source.external_id,
        params=source.params or {},
        is_active=source.is_active,
        last_checked=source.last_checked,
        last_item_id=source.last_item_id,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    request: SourceUpdate,
    _user=Depends(require_model_perm("source", ActionType.UPDATE)),
):
    """Update a source — only provided fields are changed."""
    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = request.model_dump(exclude_unset=True)
    # An explicit `null` is a provided field, and every column here is
    # non-nullable: writing it is an integrity error at flush, stripping it is
    # a 500 on `None.strip()`. Refuse it by name instead.
    nulls = sorted(key for key, value in updates.items() if value is None)
    if nulls:
        raise HTTPException(status_code=422, detail=f"Поля не могут быть null: {', '.join(nulls)}")
    if "name" in updates:
        updates["name"] = updates["name"].strip()[:255]

    await Source.objects.update_by_id(source_id, **updates)

    source = await Source.objects.get(id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return SourceResponse(
        id=source.id,
        name=source.name,
        platform_id=source.platform_id,
        source_type=source.source_type,
        external_id=source.external_id,
        params=source.params or {},
        is_active=source.is_active,
        last_checked=source.last_checked,
        last_item_id=source.last_item_id,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    _user=Depends(require_model_perm("source", ActionType.DELETE)),
):
    """Delete a source; refused while a linked task still depends on it."""
    from app.core.database import new_session
    from app.core.tenant_context import tenant_scope
    from app.services.collection.source_deletion import (
        SourceDeleteBlocked,
        SourceDeleteScopeError,
    )
    from app.services.collection.source_deletion import delete_source as delete_workspace_source

    source = await Source.objects.get(id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    session = new_session()
    try:
        # The row is read through the tenant-guarded queryset, so its workspace
        # *is* the caller's. Re-entering that workspace keeps the deletion
        # admission lock and the linked-task decision on the owning tenant
        # instead of relying on an ambient scope the caller may not have.
        with tenant_scope(source.tenant_id):
            async with session.begin():
                removal = await delete_workspace_source(session, source_id=source_id, tenant_id=source.tenant_id)
    except SourceDeleteScopeError:
        raise HTTPException(status_code=409, detail={"error": SourceDeleteScopeError.code}) from None
    finally:
        await session.close()
    if isinstance(removal, SourceDeleteBlocked):
        # The source is still a task's scope: the same rule the web enforces, so
        # a client cannot delete what the operator's page refuses. An active
        # reader wins the reason; a paused task argues `source_is_last_task_source`
        # because resuming it would widen the task to every active source.
        raise HTTPException(
            status_code=409,
            detail={
                "error": removal.reason,
                "task_ids": [task.task_id for task in removal.blocking_tasks],
            },
        )
    if removal is None:
        raise HTTPException(status_code=404, detail="Source not found")
