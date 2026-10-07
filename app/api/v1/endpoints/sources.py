"""Source CRUD API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_model_perm
from app.models import Source, Platform
from app.schemas.source import SourceCreate, SourceUpdate, SourceResponse
from app.types import ActionType

router = APIRouter(tags=["sources"])


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    _user = Depends(require_model_perm("source", ActionType.VIEW)),
):
    """List all sources in the current workspace."""
    sources = await Source.objects.filter().order_by(Source.id)
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
    _user = Depends(require_model_perm("source", ActionType.VIEW)),
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
    _user = Depends(require_model_perm("source", ActionType.CREATE)),
):
    """Create a new source in the current workspace."""
    # Verify platform exists
    platform = await Platform.objects.get(id=request.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Платформа {request.platform_id} не найдена")

    source = await Source.objects.create(
        platform_id=request.platform_id,
        name=request.name.strip()[:255],
        source_type=request.source_type,
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
    _user = Depends(require_model_perm("source", ActionType.UPDATE)),
):
    """Update a source — only provided fields are changed."""
    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = request.model_dump(exclude_unset=True)
    if "name" in updates:
        updates["name"] = updates["name"].strip()[:255]
    if "external_id" in updates:
        updates["external_id"] = updates["external_id"].strip()[:100]

    await Source.objects.update_by_id(source_id, **updates)

    source = await Source.objects.get(id=source_id)
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
    _user = Depends(require_model_perm("source", ActionType.DELETE)),
):
    """Delete a source."""
    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    await Source.objects.delete(id=source_id)
