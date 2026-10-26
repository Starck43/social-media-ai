"""Review/cancel a sealed proposal. No route claims or executes external effects."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.models import AgentAction

from .deps import ensure_csrf, render

router = APIRouter(prefix="/actions")


def _allowed(request, action):
    user = getattr(request.state, "web_user", None)
    perms = getattr(request.state, "web_perms", None)
    return bool(user and user.is_active and perms and perms.can("botaction", action))


def _denied(status=403):
    return JSONResponse({"ok": False}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("")
@router.get("/")
async def actions_list(request: Request):
    if not _allowed(request, "view"):
        return _denied()
    rows = await AgentAction.objects.get_recent_actions(hours=24 * 30, limit=50)
    response = render(request, "web/actions.html", actions=rows)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/{action_id}")
async def action_detail(request: Request, action_id: int):
    if not _allowed(request, "view"):
        return _denied()
    row = await AgentAction.objects.get(id=action_id)
    if row is None:
        return _denied(404)
    response = render(request, "web/action_detail.html", action=row, review_hash=AgentAction.objects.fingerprint(row))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{action_id}/{decision}")
async def action_decision(
    request: Request, action_id: int, decision: str, token: str = Form("", alias="_csrf"), payload_hash: str = Form("")
):
    if not ensure_csrf(request, token) or not _allowed(request, "view") or not _allowed(request, "update"):
        return _denied()
    if decision not in {"approve", "reject", "cancel"}:
        return _denied(404)
    try:
        if decision == "approve":
            if not payload_hash:
                return _denied(422)
            row = await AgentAction.objects.approve_action(action_id, expected_payload_hash=payload_hash)
        elif decision == "reject":
            row = await AgentAction.objects.reject_action(action_id)
        else:
            row = await AgentAction.objects.cancel_action(action_id)
    except PermissionError:
        return _denied()
    if row is None:
        return _denied(409)
    return RedirectResponse(f"/app/actions/{row.id}", status_code=303, headers={"Cache-Control": "no-store"})
