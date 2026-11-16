"""Read-only personal file metadata, through the file catalog authority."""

from urllib.parse import urlencode

from fastapi import HTTPException
from sqladmin import BaseView, expose
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app.core.config import settings
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models.managers.file_catalog_manager import CatalogInputError, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.catalog_scan import catalog_scan_status
from app.types import ActionType
from app.web.file_catalog import _scan_message

catalog = FileCatalogManager()


def _number(value, *, default=None, minimum=1):
    if value is None:
        return default
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise HTTPException(400, "Некорректный числовой параметр")
    result = int(value)
    if not minimum <= result <= 2_147_483_647:
        raise HTTPException(400, "Некорректный числовой параметр")
    return result


class FileCatalogAdmin(BaseView):
    name = "Каталог файлов"
    name_plural = "Каталог файлов"
    icon = "fa fa-folder"
    category = "Источники и сбор"
    category_icon = "fa fa-rss"

    def is_accessible(self, request: Request) -> bool:
        actor = getattr(request.state, "admin_user", None)
        return bool(
            settings.FILE_CATALOG_UI_ENABLED
            and actor
            and actor.is_active
            and actor.has_admin_access()
            and actor.has_perm_for("source", ActionType.VIEW)
        )

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    @expose("/file-catalog", methods=["GET"])
    async def file_catalog(self, request: Request):
        if not self.is_accessible(request):
            raise HTTPException(404, "Каталог недоступен")
        actor = request.state.admin_user
        tenant_id = current_tenant_id()
        if is_bypass():
            raw = request.session.get("app_tenant_id")
            try:
                tenant_id = int(raw) if raw is not None and not isinstance(raw, bool) else None
            except (TypeError, ValueError):
                tenant_id = None
        if type(tenant_id) is not int or tenant_id <= 0 or type(actor.id) is not int or actor.id <= 0:
            raise HTTPException(404, "Каталог недоступен")
        params = request.query_params
        connection_id = _number(params.get("connection_id"))
        object_id = _number(params.get("object_id"))
        after = _number(params.get("after"), default=0, minimum=0)
        query = params.get("q", "")
        args = {"tenant_id": tenant_id, "actor_id": actor.id}
        try:
            # Even a platform owner must pass the same fresh owner/membership,
            # source-view and folder-policy checks as the regular workspace UI.
            with tenant_scope(tenant_id):
                mounts = await catalog.connections(**args)
                selected = next((row for row in mounts if row["id"] == connection_id), None)
                if connection_id is not None and selected is None:
                    raise ConnectionDenied()
                selected = selected or (mounts[0] if mounts else None)
                if object_id is not None and selected is None:
                    raise ConnectionDenied()
                result = (
                    await catalog.list_page(**args, connection_id=selected["id"], query=query, after=after, limit=50)
                    if selected and object_id is None
                    else {"items": [], "next_after": None}
                )
                detail = (
                    await catalog.detail(**args, connection_id=selected["id"], object_id=object_id)
                    if object_id is not None
                    else None
                )
                status = await catalog_scan_status(**args, connection_id=selected["id"]) if selected else None
        except ConnectionDenied:
            raise HTTPException(404, "Каталог недоступен") from None
        except CatalogInputError:
            raise HTTPException(400, "Некорректный запрос каталога") from None
        except DBAPIError:
            raise HTTPException(503, "Каталог временно недоступен") from None
        next_url = None
        if result["next_after"] is not None:
            next_url = "?" + urlencode({"connection_id": selected["id"], "q": query, "after": result["next_after"]})
        # Templates receive only the fields required to operate the screen.
        files = [
            {"id": row["id"], "name": row["name"], "kind": row["kind"], "observed_at": row["observed_at"]}
            for row in result["items"]
        ]
        if detail:
            detail = {
                "name": detail["name"],
                "kind": detail["kind"],
                "observations": [
                    {
                        "number": row["number"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "observed_at": row["observed_at"],
                    }
                    for row in detail["observations"]
                ],
            }
        response = await self.templates.TemplateResponse(
            request,
            "sqladmin/file_catalog.html",
            {
                "title": self.name,
                "model_view": self,
                "mounts": [{"id": row["id"], "label": row["label"]} for row in mounts],
                "selected": selected["id"] if selected else None,
                "files": files,
                "detail": detail,
                "query": query,
                "next_url": next_url,
                "scan_message": _scan_message(status),
            },
        )
        response.headers.update(
            {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
        )
        return response
