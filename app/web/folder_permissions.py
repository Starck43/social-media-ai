"""Default-off folder restriction editor on the existing personal file workspace."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.action_forms import action_form, action_token
from app.services.file_workspace.folder_permissions import (
    FolderPolicyConflict,
    FolderPolicyInputError,
    get_folder_permissions,
    set_folder_permissions,
)
from app.web.file_catalog import _actor, _render_catalog

router = APIRouter(prefix="/knowledge/folders")


def _args(request, connection_id):
    args = _actor(request)
    return {**args, "connection_id": connection_id}


def _token(request, args):
    return action_token(request.session, "folder-policy:" + settings.SECRET_KEY, **args, object_id=1)


def _error(code):
    return HTTPException(
        code, "Настройки доступа недоступны. Откройте форму заново.", headers={"Cache-Control": "no-store"}
    )


@router.get("/{connection_id}/permissions")
async def folder_permissions_page(
    request: Request, connection_id: int, path: str | None = Query(None, max_length=1024)
):
    args = _args(request, connection_id)
    if not 1 <= connection_id <= 2147483647:
        raise _error(404)
    try:
        view = await get_folder_permissions(**args, path=path)
    except ConnectionDenied:
        raise _error(404) from None
    except FolderPolicyInputError:
        raise _error(400) from None
    except DBAPIError:
        raise _error(503) from None
    return _render_catalog(request, "web/folder_permissions.html", policy=view, policy_csrf=_token(request, args))


@router.post("/{connection_id}/permissions")
async def folder_permissions_save(request: Request, connection_id: int):
    args = _args(request, connection_id)
    if not 1 <= connection_id <= 2147483647 or request.query_params:
        raise _error(400)
    try:
        form = await action_form(request, {"_csrf", "path", "access", "revision"}, _token(request, args))
    except (ValueError, UnicodeError):
        raise _error(403) from None
    try:
        await set_folder_permissions(
            **args, path=form["path"], access=form["access"], expected_revision=form["revision"]
        )
    except ConnectionDenied:
        raise _error(404) from None
    except FolderPolicyConflict:
        raise _error(409) from None
    except FolderPolicyInputError:
        raise _error(400) from None
    except DBAPIError:
        raise _error(503) from None
    return RedirectResponse(
        f"/app/knowledge/folders/{connection_id}/permissions", status_code=303, headers={"Cache-Control": "no-store"}
    )
