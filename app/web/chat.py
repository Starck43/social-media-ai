"""Chat with the agent in the browser — `/app/chat`.

The messenger chat and this page are the *same* conversation, not a second
implementation: `app/agent/runtime.py::handle_web_message` reuses the shared
turn (`_handle_in_tenant`) with the workspace the middleware already resolved,
so the daily cost cap, the confirmation gate, the session row and every tool
behave exactly as they do in Telegram.

What the web adds is the transport: a transcript read from
`agent_messages`, and a POST that renders the reply inline instead of
polling a job.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .deps import add_flash, ensure_csrf, render

router = APIRouter(prefix="/chat")

# Transcript depth on first paint. The loop only ever feeds the last 20 turns
# to the model (see `agent_messages.recent`), so a long page is scroll, not
# context.
TRANSCRIPT_LIMIT = 40


def _membership_role(request: Request) -> str:
    """The caller's role in the active workspace, as `Resolution.role` sees it."""
    tenant_id = getattr(request.state, "tenant_id", None)
    for membership in getattr(request.state, "memberships", []) or []:
        if membership.tenant_id == tenant_id:
            return membership.role
    return "member"


@router.get("")
@router.get("/")
async def chat_page(request: Request):
    """Render the transcript; opens no session and spends nothing."""
    from app.agent.runtime import web_session_id

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        return render(request, "web/chat.html", section="chat", messages=[], is_owner=False)

    messages = []
    session_id = await web_session_id(user.id)
    if session_id is not None:
        from app.models.managers.agent_message_manager import agent_messages

        messages = await agent_messages.recent(session_id, limit=TRANSCRIPT_LIMIT)

    return render(
        request,
        "web/chat.html",
        section="chat",
        messages=messages,
        is_owner=_membership_role(request) == "owner",
    )


@router.post("")
async def chat_send(
    request: Request,
    text: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    """Run one agent turn and show the answer on the same page."""
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/chat", status_code=302)

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        return RedirectResponse("/app/chat", status_code=302)

    text = (text or "").strip()
    if not text:
        return RedirectResponse("/app/chat", status_code=302)

    from app.agent.runtime import handle_web_message

    # No permission gate here: talking to the agent is a read-ish action every
    # workspace member may do, and the agent's own tool layer already applies
    # `WebPerms`-equivalent rules (owner-only writes via `Resolution.is_owner`).
    reply = await handle_web_message(
        text,
        tenant_id=tenant_id,
        user_id=user.id,
        role=_membership_role(request),
    )

    if reply is None:
        add_flash(request, "error", "Агент не ответил — попробуйте ещё раз")
    return render(
        request,
        "web/chat.html",
        section="chat",
        messages=await _transcript(user.id),
        is_owner=_membership_role(request) == "owner",
    )


async def _transcript(user_id: int) -> list:
    """The transcript after a turn, without a redirect round-trip."""
    from app.agent.runtime import web_session_id

    session_id = await web_session_id(user_id)
    if session_id is None:
        return []
    from app.models.managers.agent_message_manager import agent_messages

    return await agent_messages.recent(session_id, limit=TRANSCRIPT_LIMIT)
