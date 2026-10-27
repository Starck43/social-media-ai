"""Sealed proposal review; one separately gated Telegram capability only."""

from uuid import uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.models import AgentAction
from app.services.actions.telegram_message import CAPABILITY, OutcomePersistenceError

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
    targets = await AgentAction.objects.telegram_message_targets() if _allowed(request, "update") else []
    response = render(request, "web/actions.html", actions=rows, telegram_targets=targets, proposal_key=uuid4().hex)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/telegram-message")
async def telegram_proposal(
    request: Request,
    binding_id: int = Form(...),
    text: str = Form(...),
    idempotency_key: str = Form(...),
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token) or not _allowed(request, "view") or not _allowed(request, "update"):
        return _denied()
    try:
        row, _ = await AgentAction.objects.propose_telegram_message(
            binding_id=binding_id,
            text=text,
            idempotency_key=idempotency_key,
        )
    except PermissionError:
        return _denied()
    except ValueError:
        return _denied(409)
    return RedirectResponse(f"/app/actions/{row.id}", status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/{action_id}")
async def action_detail(request: Request, action_id: int):
    if not _allowed(request, "view"):
        return _denied()
    row = await AgentAction.objects.get(id=action_id)
    if row is None:
        return _denied(404)
    targets = await AgentAction.objects.telegram_message_targets() if _allowed(request, "update") else []
    telegram_action = row.capability == CAPABILITY and row.dry_run is False
    available = telegram_action and any(t["id"] == (row.payload or {}).get("binding_id") for t in targets)
    response = render(
        request,
        "web/action_detail.html",
        action=row,
        review_hash=AgentAction.objects.fingerprint(row),
        telegram_action=telegram_action,
        telegram_available=available,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{action_id}/{decision}")
async def action_decision(
    request: Request, action_id: int, decision: str, token: str = Form("", alias="_csrf"), payload_hash: str = Form("")
):
    if not ensure_csrf(request, token) or not _allowed(request, "view") or not _allowed(request, "update"):
        return _denied()
    if decision not in {"approve", "reject", "cancel", "execute-telegram", "reconcile-telegram"}:
        return _denied(404)
    try:
        if decision == "approve":
            if not payload_hash:
                return _denied(422)
            row = await AgentAction.objects.approve_action(action_id, expected_payload_hash=payload_hash)
        elif decision == "execute-telegram":
            if not payload_hash:
                return _denied(422)
            result = await AgentAction.objects.execute_telegram_message(action_id, expected_payload_hash=payload_hash)
            if result is None:
                return _denied(409)
            return RedirectResponse(f"/app/actions/{action_id}", status_code=303, headers={"Cache-Control": "no-store"})
        elif decision == "reconcile-telegram":
            row = await AgentAction.objects.reconcile_telegram_message(action_id)
        elif decision == "reject":
            row = await AgentAction.objects.reject_action(action_id)
        else:
            row = await AgentAction.objects.cancel_action(action_id)
    except PermissionError:
        return _denied()
    except ValueError:
        return _denied(409)
    except OutcomePersistenceError:
        return JSONResponse(
            {"ok": False, "code": "external_outcome_unconfirmed"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    if row is None:
        return _denied(409)
    return RedirectResponse(f"/app/actions/{row.id}", status_code=303, headers={"Cache-Control": "no-store"})
