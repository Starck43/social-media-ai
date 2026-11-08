"""Versioned documents and revision approval router (P12-T03).

Provides the `/app/documents` route displaying versioned contracts, specifications,
content hashes, and revision approval statuses.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import document_rows
from app.web.deps import render

router = APIRouter()


@router.get("/documents")
async def documents_page(request: Request, boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")):
    """Render versioned documents page with immutable SHA256 hashes and approval states."""
    return render(
        request,
        "web/documents.html",
        evidence_rows=await document_rows(boundary),
        boundary=boundary,
    )


# P19 business-document management uses the same service as the bearer API;
# HTML writes are separately CSRF-protected and never call the API with cookies.
async def _business_invoke(operation):
    from fastapi import HTTPException

    from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable

    try:
        return await operation
    except DocumentUnavailable:
        raise HTTPException(404, "Документ недоступен") from None
    except DocumentConflict:
        raise HTTPException(409, "Документ изменился; обновите страницу") from None
    except PermissionError:
        raise HTTPException(403, "Недостаточно прав") from None
    except ValueError:
        raise HTTPException(422, "Некорректные данные") from None


def _boundary(value):
    from fastapi import HTTPException

    if value not in {"local_only", "company", "cloud"}:
        raise HTTPException(422, "Некорректная граница данных")
    return value


async def _business_form(request):
    from fastapi import HTTPException

    from app.web.deps import ensure_csrf

    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf", "")):
        raise HTTPException(403, "Некорректный CSRF-токен")
    if len(list(form.keys())) != len(list(form.multi_items())):
        raise HTTPException(422, "Повторные поля запрещены")
    values = {key: value for key, value in form.items() if key != "_csrf"}
    boundary = _boundary(values.pop("boundary", "cloud"))
    for key in ("contact_id", "case_evidence_id", "owner_user_id", "expected_revision_id"):
        if key not in values:
            continue
        raw = values[key]
        if raw == "" and key != "expected_revision_id":
            values[key] = None
        elif isinstance(raw, str) and raw.isascii() and raw.isdecimal() and not raw.startswith("0"):
            values[key] = int(raw)
        else:
            raise HTTPException(422, "Некорректный идентификатор")
    if "tags" in values:
        values["tags"] = [value.strip() for value in values["tags"].split(",") if value.strip()]
    if values.get("change_description") == "":
        values.pop("change_description")
    return boundary, values


def _back(boundary, document_id=None):
    from fastapi.responses import RedirectResponse

    suffix = f"/{document_id}" if document_id is not None else ""
    return RedirectResponse(f"/app/documents/manage{suffix}?boundary={boundary}", status_code=303)


@router.get("/documents/manage")
@router.get("/documents/manage/{document_id}")
async def manage_documents(
    request: Request,
    document_id: int | None = None,
    boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$"),
    before_id: int | None = Query(None, gt=0),
    history_before: int | None = Query(None, gt=0),
    from_id: int | None = Query(None, gt=0),
    to_id: int | None = Query(None, gt=0),
    granularity: str = Query("line", pattern="^(line|word)$"),
):
    from app.core.permissions import get_current_user, has_permission
    from app.services.knowledge.documents import document_service
    from app.services.knowledge.versions import version_service
    from app.types import ActionType

    page = await _business_invoke(document_service.list(boundary=boundary, before_id=before_id))
    editing = await _business_invoke(document_service.get(document_id, boundary=boundary)) if document_id else None
    templates = await _business_invoke(document_service.list_templates())
    history = None
    diff = None
    if editing and boundary == "local_only":
        history = await _business_invoke(version_service.list(document_id, boundary=boundary, before_id=history_before))
        if (from_id is None) != (to_id is None):
            from fastapi import HTTPException

            raise HTTPException(422, "Выберите две версии")
        if from_id is not None:
            diff = await _business_invoke(
                version_service.diff(document_id, from_id, to_id, boundary=boundary, granularity=granularity)
            )
    actor = get_current_user()
    return render(
        request,
        "web/document_management.html",
        boundary=boundary,
        items=page["items"],
        next_before_id=page["next_before_id"],
        editing=editing,
        history=history,
        diff=diff,
        templates=templates["items"],
        can_create=has_permission(actor, "source", ActionType.CREATE),
        can_update=has_permission(actor, "source", ActionType.UPDATE),
        can_delete=has_permission(actor, "source", ActionType.DELETE),
    )


@router.post("/documents/manage/create")
async def create_business_document(request: Request):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.document_contracts import DocumentCreate
    from app.services.knowledge.documents import document_service

    boundary, values = await _business_form(request)
    try:
        data = DocumentCreate.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректные данные документа") from None
    item = await _business_invoke(document_service.create(data))
    return _back(item.data_boundary, item.id)


@router.post("/documents/manage/{document_id}/edit")
async def edit_business_document(request: Request, document_id: int):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.document_contracts import DocumentUpdate
    from app.services.knowledge.documents import document_service

    boundary, values = await _business_form(request)
    try:
        data = DocumentUpdate.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректные данные документа") from None
    item = await _business_invoke(document_service.update(document_id, data, boundary=boundary))
    return _back(item.data_boundary, item.id)


@router.post("/documents/manage/{document_id}/transition")
async def transition_business_document(request: Request, document_id: int):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.document_contracts import DocumentTransition
    from app.services.knowledge.documents import document_service

    boundary, values = await _business_form(request)
    try:
        data = DocumentTransition.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректный переход") from None
    await _business_invoke(document_service.transition(document_id, data, boundary=boundary))
    return _back(boundary, document_id)


@router.post("/documents/manage/{document_id}/delete")
async def delete_business_document(request: Request, document_id: int):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.document_contracts import DocumentVersion
    from app.services.knowledge.documents import document_service

    boundary, values = await _business_form(request)
    if values.pop("confirm", None) != "delete":
        raise HTTPException(422, "Не подтверждено удаление всех версий")
    try:
        data = DocumentVersion.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректная версия") from None
    await _business_invoke(document_service.delete(document_id, data, boundary=boundary))
    return _back(boundary)


@router.post("/documents/manage/template")
async def create_document_template(request: Request):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.document_contracts import TemplateCreate
    from app.services.knowledge.documents import document_service

    boundary, values = await _business_form(request)
    try:
        data = TemplateCreate.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректный шаблон") from None
    await _business_invoke(document_service.create_template(data))
    return _back(boundary)


@router.post("/documents/manage/{document_id}/restore/{revision_id}")
async def restore_business_document(request: Request, document_id: int, revision_id: int):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services.knowledge.versions import RestoreRequest, version_service

    boundary, values = await _business_form(request)
    try:
        data = RestoreRequest.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Некорректная версия") from None
    await _business_invoke(version_service.restore(document_id, revision_id, data, boundary=boundary))
    return _back(boundary, document_id)
