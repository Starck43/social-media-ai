"""Default-off folder restriction editor on the existing personal file workspace."""

import hashlib
import hmac
import json

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


def _token(request, args, *, path, access, revision):
    key = "folder-policy:" + settings.SECRET_KEY
    base = action_token(request.session, key, **args, object_id=1)
    payload = json.dumps([path, access, revision], ensure_ascii=True, separators=(",", ":"))
    return hmac.new(
        key.encode(), ("folder-policy-form-v1:" + base + ":" + payload).encode(), hashlib.sha256
    ).hexdigest()


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
    tokens = {
        access: _token(request, args, path=view["path"], access=access, revision=view["revision"])
        for access in ("none", "read_only")
    }
    return _render_catalog(request, "web/folder_permissions.html", policy=view, policy_csrf=tokens)


@router.post("/{connection_id}/permissions")
async def folder_permissions_save(request: Request, connection_id: int):
    args = _args(request, connection_id)
    if not 1 <= connection_id <= 2147483647 or request.query_params:
        raise _error(400)
    try:
        # The submitted action is fixed by its form; each token also binds the
        # exact path and policy fingerprint displayed when that form was rendered.
        form = await action_form(
            request,
            {"_csrf", "path", "access", "revision"},
            lambda fields: _token(
                request,
                args,
                path=fields["path"],
                access=fields["access"],
                revision=fields["revision"],
            ),
        )
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
