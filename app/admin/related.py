"""Related-record form helpers; all mutations stay on SQLAdmin's guarded CRUD."""

import re
from typing import Any

from sqladmin import Admin
from sqladmin.helpers import get_object_identifier
from sqlalchemy import inspect
from sqlalchemy.orm import MANYTOMANY, MANYTOONE
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

TOKEN_PATTERN = re.compile(r"[a-f0-9]{32}\Z")


async def related_controls(view: Any, form: Any, request: Request) -> list[dict[str, Any]]:
    """Expose registered targets only for fields actually rendered by this form.

    UI hints are not authorization: the normal target routes recheck the actor,
    operation and object. No row reads, new relationship fields or write paths.
    """
    admin = view._admin_ref
    targets = {target.model: target for target in admin._views if getattr(target, "model", None)}
    fields = {field.name: field for field in form}
    controls = []
    for relation in inspect(view.model).relationships:
        if relation.viewonly or relation.direction not in (MANYTOONE, MANYTOMANY):
            continue
        field = fields.get(relation.key)
        target = targets.get(relation.mapper.class_)
        if field is None or target is None or not target.is_accessible(request):
            continue
        create = target.can_create and await target.check_can_create(request)
        edit = target.can_edit and target.has_permission(request, "edit")
        if not (create or edit):
            continue
        controls.append(
            {
                "field": field.id,
                "label": str(field.label.text),
                "create": str(request.url_for("admin:create", identity=target.identity)) if create else None,
                # Append the encoded identifier in the browser, not a supplied URL.
                "edit": (
                    str(request.url_for("admin:edit", identity=target.identity, pk="__related_pk__")) if edit else None
                ),
                "identity": target.identity,
            }
        )
    return controls


async def related_token(request: Request) -> str | None:
    """Retain popup context across POST validation errors as well as GETs."""
    source = await request.form() if request.method == "POST" else request.query_params
    token = source.get("_related_token", "")
    return token if isinstance(token, str) and TOKEN_PATTERN.fullmatch(token) else None


class RelatedAdmin(Admin):
    """Replace only a successful popup CRUD redirect with a return receipt.

    Authentication, permissions, validation, hooks, persistence and audit remain
    in Admin.create/edit. Failed/custom responses and ordinary forms are intact.
    """

    async def create(self, request: Request) -> Response:
        response = await super().create(request)
        return await self._related_response(request, response)

    async def edit(self, request: Request) -> Response:
        response = await super().edit(request)
        return await self._related_response(request, response)

    def get_save_redirect_url(self, request: Request, form: Any, model_view: Any, obj: Any) -> Any:
        token = form.get("_related_token", "")
        if isinstance(token, str) and TOKEN_PATTERN.fullmatch(token):
            request.state.related_saved = {
                "type": "admin-related-saved",
                "token": token,
                "identity": model_view.identity,
                "id": str(get_object_identifier(obj)),
                "label": str(obj),
            }
        return super().get_save_redirect_url(request, form, model_view, obj)

    async def _related_response(self, request: Request, response: Response) -> Response:
        saved = getattr(request.state, "related_saved", None)
        if request.method != "POST" or not isinstance(response, RedirectResponse) or not saved:
            return response
        result = await self.templates.TemplateResponse(request, "sqladmin/related_saved.html", {"saved": saved})
        result.headers["Cache-Control"] = "no-store"
        return result
