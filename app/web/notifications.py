"""Workspace-bound attention snapshot and explicit shared event-read actions."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from app.services.notifications.attention import AttentionContext, AttentionFeed, positive_id

from .deps import ensure_csrf

router = APIRouter(prefix="/notifications")


def _context(request):
    user = getattr(request.state, "web_user", None)
    workspace = getattr(request.state, "tenant_id", None)
    if user is None or not user.is_active or not positive_id(user.id) or not positive_id(workspace):
        return None
    # Membership is refreshed by middleware on every GET and POST. Operator
    # status does not turn this bell into the operator's fleet-wide filter.
    if not any(
        m.tenant_id == workspace and m.user_id == user.id and m.channel == "web" and m.is_active
        for m in getattr(request.state, "memberships", [])
    ):
        return None
    context = AttentionContext(workspace, user.id)
    for header, value in (("X-App-Workspace", workspace), ("X-App-User", user.id)):
        expected = request.headers.get(header)
        if expected is not None and expected != str(value):
            return None
    return context


def _unavailable(status=409):
    return JSONResponse({"ok": False, "reset": True}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("")
@router.get("/")
async def notifications_list(request: Request, limit: int = 30):
    context = _context(request)
    if context is None:
        return _unavailable()
    feed = AttentionFeed(context, getattr(request.state, "web_perms", None))
    return JSONResponse(await feed.snapshot(limit), headers={"Cache-Control": "no-store"})


def _read_context(request, token, workspace_id):
    if not ensure_csrf(request, token):
        return None
    context = _context(request)
    if context is None or str(context.workspace_id) != workspace_id:
        return None
    return context


@router.post("/read-all")
async def notifications_read_all(request: Request, token: str = Form("", alias="_csrf"), workspace_id: str = Form("")):
    """Read permitted persisted messages in the bounded window, never signals."""
    context = _read_context(request, token, workspace_id)
    if context is None:
        return _unavailable(403)
    feed = AttentionFeed(context, getattr(request.state, "web_perms", None))
    count = await feed.read_window()
    if count is None:
        return _unavailable(403)
    return JSONResponse(
        {"marked": count, "windowed": True, "context": context.payload()}, headers={"Cache-Control": "no-store"}
    )


@router.post("/read/{notification_id}")
async def notification_read_one(
    request: Request, notification_id: int, token: str = Form("", alias="_csrf"), workspace_id: str = Form("")
):
    context = _read_context(request, token, workspace_id)
    if context is None:
        return _unavailable(403)
    feed = AttentionFeed(context, getattr(request.state, "web_perms", None))
    ok = positive_id(notification_id) and await feed.read_one(notification_id)
    return JSONResponse({"ok": bool(ok), "context": context.payload()}, headers={"Cache-Control": "no-store"})
