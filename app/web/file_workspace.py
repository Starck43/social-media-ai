"""Default-off standalone private controls; no generic URL/provenance input."""

import json
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.integrations.file_workspace.yandex_content import FileDownloadUnavailable
from app.models.managers.file_content_manager import FileContentManager
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager, PrivateInsightUnavailable
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.action_forms import action_form, action_token
from app.services.file_workspace.file_content import FileContentService
from app.web.deps import render
from app.web.file_catalog import catalog

router = APIRouter(prefix="/knowledge/files")
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; script-src 'none'",
}


def _error(status=404):
    return HTTPException(status, "Действие с файлом недоступно", headers=PRIVATE_HEADERS)


def _args(request, object_id, connection_id):
    if not settings.FILE_CATALOG_UI_ENABLED or not settings.FILE_WORKSPACE_ACTIONS_ENABLED:
        raise _error()
    user = getattr(request.state, "web_user", None)
    tenant = getattr(request.state, "tenant_id", None)
    if (
        user is None
        or type(user.id) is not int
        or user.id <= 0
        or type(tenant) is not int
        or tenant <= 0
        or not 1 <= object_id <= 2147483647
    ):
        raise _error()
    if set(request.query_params) != {"connection_id"} or len(request.query_params.getlist("connection_id")) != 1:
        raise _error()
    return dict(tenant_id=tenant, actor_id=user.id, connection_id=connection_id, object_id=object_id)


def _token(request, args):
    return action_token(request.session, settings.SECRET_KEY, **args)


def _manager():
    cipher = PrivateCipher(settings.CREDENTIALS_KEY) if settings.FILE_PRIVATE_RESULTS_UI_ENABLED else None
    return FileInsightManager(enabled=True, cipher=cipher)


def _content():
    return FileContentService(FileContentManager(enabled=True))


def _url(args):
    return f'/app/knowledge/files/{args["object_id"]}/workspace?' + urlencode({"connection_id": args["connection_id"]})


def _mount_args(args):
    return {k: v for k, v in args.items() if k != "object_id"}


async def _page(request, args, *, preview=None, result=None, message=None):
    item = await catalog.detail(**args)
    if item["kind"] != "file":
        raise ConnectionDenied()
    manager = _manager()
    consent = await manager.consent_state(**_mount_args(args))
    if preview is not None and (
        not consent["processing"] or preview.context.processing_policy_key != consent["revision"]
    ):
        raise ConnectionDenied()
    if result is not None and (
        not consent["processing"]
        or not consent["retention"]
        or result["binding"]["consent_revision"] != consent["revision"]
    ):
        raise ConnectionDenied()
    records = []
    if settings.FILE_PRIVATE_RESULTS_UI_ENABLED and consent["processing"] and consent["retention"]:
        records = await manager.list_results(**args)
    response = render(
        request,
        "web/file_workspace.html",
        file=item,
        connection_id=args["connection_id"],
        action_url=_url(args),
        file_csrf=_token(request, args),
        consent=consent,
        records=records,
        results_enabled=settings.FILE_PRIVATE_RESULTS_UI_ENABLED,
        preview=preview,
        result=result,
        message=message,
    )
    response.headers.update(PRIVATE_HEADERS)
    return response


async def _form(request, args, keys):
    try:
        return await action_form(request, set(keys) | {"_csrf"}, _token(request, args))
    except Exception:
        raise _error(403) from None


@router.get("/{object_id}/workspace")
async def file_workspace_page(request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)):
    args = _args(request, object_id, connection_id)
    try:
        return await _page(request, args)
    except (ConnectionDenied, PrivateInsightUnavailable):
        raise _error() from None
    except (DBAPIError, ValueError):
        raise _error(503) from None


@router.post("/{object_id}/workspace/consent")
async def file_workspace_consent(
    request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)
):
    args = _args(request, object_id, connection_id)
    form = await _form(request, args, {"processing", "retention"})
    if form["processing"] not in {"0", "1"} or form["retention"] not in {"0", "1"}:
        raise _error(400)
    if form["retention"] == "1" and not settings.FILE_PRIVATE_RESULTS_UI_ENABLED:
        raise _error(400)
    try:
        await catalog.detail(**args)
        await _manager().set_consent(
            **_mount_args(args),
            processing=form["processing"] == "1",
            retention=form["retention"] == "1",
            policy_revision=POLICY_REVISION,
        )
    except (ConnectionDenied, PrivateInsightUnavailable):
        raise _error() from None
    except (DBAPIError, ValueError):
        raise _error(503) from None
    return RedirectResponse(_url(args), status_code=303, headers=PRIVATE_HEADERS)


@router.post("/{object_id}/workspace/original")
async def file_workspace_original(
    request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)
):
    args = _args(request, object_id, connection_id)
    await _form(request, args, set())
    try:
        attachment = await _content().original(**args)
    except FileDownloadUnavailable:
        raise _error(503) from None
    headers = dict(PRIVATE_HEADERS)
    headers.update(
        {
            "Content-Disposition": 'attachment; filename="original.txt"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
    )
    return Response(attachment.payload, media_type="application/octet-stream", headers=headers)


@router.post("/{object_id}/workspace/preview")
async def file_workspace_preview(
    request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)
):
    args = _args(request, object_id, connection_id)
    form = await _form(request, args, {"start", "end"})
    if any(not form[k].isascii() or not form[k].isdigit() or len(form[k]) > 4 for k in ("start", "end")):
        raise _error(400)
    try:
        evidence = await _content().preview(**args, start=int(form["start"]), end=int(form["end"]))
        return await _page(request, args, preview=evidence)
    except FileDownloadUnavailable:
        raise _error(503) from None
    except (ConnectionDenied, PrivateInsightUnavailable):
        raise _error() from None
    except (DBAPIError, ValueError):
        raise _error(503) from None


@router.post("/{object_id}/workspace/result")
async def file_workspace_result(request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)):
    args = _args(request, object_id, connection_id)
    if not settings.FILE_PRIVATE_RESULTS_UI_ENABLED:
        raise _error()
    form = await _form(request, args, {"result_id"})
    try:
        result = await _manager().get(**args, result_id=form["result_id"])
        return await _page(request, args, result=json.loads(result.payload))
    except (ConnectionDenied, PrivateInsightUnavailable):
        raise _error() from None
    except (DBAPIError, ValueError):
        raise _error(503) from None


@router.post("/{object_id}/workspace/forget")
async def file_workspace_forget(request: Request, object_id: int, connection_id: int = Query(..., ge=1, le=2147483647)):
    args = _args(request, object_id, connection_id)
    if not settings.FILE_PRIVATE_RESULTS_UI_ENABLED:
        raise _error()
    form = await _form(request, args, {"confirm"})
    if form["confirm"] != "forget":
        raise _error(400)
    try:
        await _manager().forget_object(**args)
    except (ConnectionDenied, PrivateInsightUnavailable):
        raise _error() from None
    except DBAPIError:
        raise _error(503) from None
    return RedirectResponse(_url(args), status_code=303, headers=PRIVATE_HEADERS)
