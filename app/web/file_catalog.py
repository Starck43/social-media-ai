"""Default-off read UI for the saved cloud catalog; never a provider proxy."""

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.managers.file_catalog_manager import CatalogInputError, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.web.deps import render

router = APIRouter(prefix="/knowledge/files")
catalog = FileCatalogManager()


def _actor(request):
    if not settings.FILE_CATALOG_UI_ENABLED:
        raise HTTPException(404, "Каталог недоступен")
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or type(tenant_id) is not int or tenant_id <= 0:
        raise HTTPException(404, "Каталог недоступен")
    return {"tenant_id": tenant_id, "actor_id": user.id}


def _unavailable():
    # Same response for foreign, revoked, unsupported or missing selectors.
    return HTTPException(404, "Каталог недоступен")


def _render_catalog(request, template, **context):
    response = render(request, template, **context)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        }
    )
    return response


@router.get("")
async def file_catalog_page(
    request: Request,
    connection_id: int | None = Query(None, ge=1, le=2_147_483_647),
    q: str = Query("", max_length=200),
    after: int = Query(0, ge=0, le=2_147_483_647),
):
    args = _actor(request)
    try:
        mounts = await catalog.connections(**args)
        selected = next((mount for mount in mounts if mount["id"] == connection_id), None)
        if connection_id is not None and selected is None:
            raise ConnectionDenied()
        selected = selected or (mounts[0] if mounts else None)
        result = (
            await catalog.list_page(**args, connection_id=selected["id"], query=q, after=after, limit=50)
            if selected
            else {"items": [], "next_after": None}
        )
    except ConnectionDenied:
        raise _unavailable() from None
    except CatalogInputError:
        raise HTTPException(400, "Некорректный запрос каталога") from None
    except DBAPIError:
        # A missing/unavailable catalog is not an empty, synchronized folder.
        raise HTTPException(503, "Каталог временно недоступен") from None
    next_url = None
    if result["next_after"] is not None:
        next_url = "/app/knowledge/files?" + urlencode(
            {
                "connection_id": selected["id"],
                "q": q,
                "after": result["next_after"],
            }
        )
    return _render_catalog(
        request,
        "web/file_catalog.html",
        mounts=mounts,
        selected=selected,
        files=result["items"],
        q=q,
        next_url=next_url,
        after=after,
    )


@router.get("/{object_id}")
async def file_catalog_detail(
    request: Request,
    object_id: int,
    connection_id: int = Query(..., ge=1, le=2_147_483_647),
):
    args = _actor(request)
    try:
        # Detail never trusts a posted title, path, source_ref, URL or boundary.
        item = await catalog.detail(**args, connection_id=connection_id, object_id=object_id)
    except ConnectionDenied:
        raise _unavailable() from None
    except DBAPIError:
        raise HTTPException(503, "Каталог временно недоступен") from None
    return _render_catalog(request, "web/file_catalog_detail.html", file=item, connection_id=connection_id)
