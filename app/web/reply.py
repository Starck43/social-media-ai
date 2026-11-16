"""Web reply surface (P15-T08): the form only collects text; the server owns provenance.

Mounted under `/app/communications/reply`. Creating a draft and scheduling it stay
two separate permissions, so a reply form cannot also approve a send.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from app.core.database import async_session_maker
from app.services.communications.outbox_store import OutboxConflict
from app.services.communications.reply import ReplyUnavailable, create_reply_draft

from .deps import ensure_csrf

router = APIRouter(prefix="/app/communications/reply")


def _allowed(request: Request, action: str) -> bool:
    user = getattr(request.state, "web_user", None)
    perms = getattr(request.state, "web_perms", None)
    return bool(user and user.is_active and perms and perms.can("comms.outbox", action))


def _response(status: int, **body) -> JSONResponse:
    return JSONResponse({"ok": status < 400, **body}, status_code=status, headers={"Cache-Control": "no-store"})


@router.post("")
async def reply_draft(
    request: Request,
    thread_id: int = Form(...),
    inbound_message_id: int = Form(...),
    body: str = Form(..., max_length=1_000_000),
    subject: str = Form("", max_length=998),
    timezone: str = Form("UTC", max_length=64),
    idempotency_key: str = Form("", max_length=128),
    token: str = Form("", alias="_csrf"),
):
    """Create one reply draft; approval and scheduling remain on the outbox decision surface."""
    if not ensure_csrf(request, token) or not _allowed(request, "update"):
        return _response(403)
    key = idempotency_key or uuid4().hex
    try:
        async with async_session_maker() as session:
            draft = await create_reply_draft(
                session,
                thread_id=thread_id,
                inbound_message_id=inbound_message_id,
                subject=subject,
                body=body,
                timezone=timezone,
                idempotency_key=key,
            )
            await session.commit()
    except PermissionError:
        return _response(403)
    except (OutboxConflict, ValueError):
        return _response(409)
    except ReplyUnavailable:
        return _response(404)
    return _response(201, draft=draft, decision_surface="/app/communications?tab=outbox")
