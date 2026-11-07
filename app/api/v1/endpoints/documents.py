"""Tenant-scoped business document and template CRUD."""

from fastapi import Depends, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, validate_business_routes
from app.schemas.business import BusinessPage, BusinessSchema
from app.services.knowledge.document_contracts import (
    Boundary,
    DocumentCreate,
    DocumentOut,
    DocumentTransition,
    DocumentUpdate,
    DocumentVersion,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    TemplateVersion,
)
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable, document_service
from app.types import ActionType

router = business_router("documents", permission=require_model_perm("source", ActionType.VIEW))


class DeletedOut(BusinessSchema):
    deleted: bool


async def invoke(operation):
    try:
        return await operation
    except DocumentUnavailable:
        raise HTTPException(404) from None
    except DocumentConflict:
        raise HTTPException(409) from None
    except PermissionError:
        raise HTTPException(403) from None
    except ValueError:
        raise HTTPException(422) from None


@router.get("/templates", response_model=BusinessPage[TemplateOut])
async def list_templates(limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, gt=0)):
    return await invoke(document_service.list_templates(limit=limit, before_id=before_id))


@router.post(
    "/templates",
    response_model=TemplateOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def create_template(request: TemplateCreate):
    return await invoke(document_service.create_template(request))


@router.get("/templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: int = Path(gt=0)):
    return await invoke(document_service.get_template(template_id))


@router.put(
    "/templates/{template_id}",
    response_model=TemplateOut,
    dependencies=[Depends(require_model_perm("source", ActionType.UPDATE))],
)
async def update_template(request: TemplateUpdate, template_id: int = Path(gt=0)):
    return await invoke(document_service.update_template(template_id, request))


@router.delete(
    "/templates/{template_id}",
    response_model=DeletedOut,
    dependencies=[Depends(require_model_perm("source", ActionType.DELETE))],
)
async def delete_template(request: TemplateVersion, template_id: int = Path(gt=0)):
    return await invoke(document_service.delete_template(template_id, request))


@router.get("", response_model=BusinessPage[DocumentOut])
async def list_documents(
    boundary: Boundary = "cloud", limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, gt=0)
):
    return await invoke(document_service.list(boundary=boundary, limit=limit, before_id=before_id))


@router.post(
    "",
    response_model=DocumentOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def create_document(request: DocumentCreate):
    return await invoke(document_service.create(request))


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: int = Path(gt=0), boundary: Boundary = "cloud"):
    return await invoke(document_service.get(document_id, boundary=boundary))


@router.patch(
    "/{document_id}",
    response_model=DocumentOut,
    dependencies=[Depends(require_model_perm("source", ActionType.UPDATE))],
)
async def update_document(request: DocumentUpdate, document_id: int = Path(gt=0), boundary: Boundary = "cloud"):
    return await invoke(document_service.update(document_id, request, boundary=boundary))


@router.post(
    "/{document_id}/transition",
    response_model=DocumentOut,
    dependencies=[Depends(require_model_perm("source", ActionType.UPDATE))],
)
async def transition_document(request: DocumentTransition, document_id: int = Path(gt=0), boundary: Boundary = "cloud"):
    return await invoke(document_service.transition(document_id, request, boundary=boundary))


@router.delete(
    "/{document_id}", response_model=DeletedOut, dependencies=[Depends(require_model_perm("source", ActionType.DELETE))]
)
async def delete_document(request: DocumentVersion, document_id: int = Path(gt=0), boundary: Boundary = "cloud"):
    return await invoke(document_service.delete(document_id, request, boundary=boundary))


validate_business_routes(router)
