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

import re
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .deps import add_flash, ensure_csrf, render

router = APIRouter(prefix="/chat")

# Transcript depth on first paint. The loop only ever feeds the last 20 turns
# to the model (see `agent_messages.recent`), so a long page is scroll, not
# context.
TRANSCRIPT_LIMIT = 40


def _format_message_time(dt_val, role: str) -> str:
    """Format created_at for display: RU format, today = time only.

    Accepts either a datetime object or an ISO-format string from the DB.
    """
    if dt_val is None:
        return ""

    # Parse string if needed (DB returns ISO format like "2026-10-04 11:56:36.630582+00:00")
    if isinstance(dt_val, str):
        dt_val = dateutil_parser.parse(dt_val)

    # Use UTC (database stores UTC, Moscow is UTC+3)
    local = dt_val.astimezone(timezone.utc)
    time_str = local.strftime("%H:%M")

    if role == "user":
        # Always show full date for user messages
        return local.strftime("%d.%m.%Y, %H:%M")

    # For assistant: today = time only, other days = full date
    now_utc = datetime.now(timezone.utc)
    if local.date() == now_utc.date():
        return time_str
    return local.strftime("%d.%m.%Y, %H:%M")


def _json_block_html(json_text: str) -> str:
    """Wrap a JSON payload in the styled block with a working copy button.

    The copy handler is inline ``onclick`` because the markup is injected via
    ``x-html`` — Alpine does not initialise elements added that way.

    Escaping order matters: HTML-escape first, then JS-escape the payload for
    the template literal, then ``&quot;``-escape the double quotes so they do
    not terminate the attribute.
    """
    display = json_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    js_safe = display.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${").replace('"', "&quot;")
    return (
        '<div class="json-block">'
        '<div class="json-block-header">'
        '<span class="json-block-label">JSON</span>'
        f'<button type="button" class="json-copy-btn" onclick="navigator.clipboard.writeText(`{js_safe}`)">'
        "Копировать</button>"
        "</div>"
        '<pre class="whitespace-pre-wrap break-all text-xs text-cyan-200">'
        f"{display}"
        "</pre>"
        "</div>"
    )


def _render_assistant_content(content: str | None) -> str:
    """Render assistant content with JSON block support.

    Detects ```json ... ``` blocks and wraps them in a styled container.
    Also detects standalone JSON (starts with { or [) and formats it.
    """
    if not content:
        return ""

    # Match ```json ... ``` blocks (more flexible pattern)
    content = re.sub(
        r"```json\s*([\s\S]*?)```",
        lambda m: _json_block_html(m.group(1)),
        content,
    )

    # If the content is a standalone JSON object/array (trimmed), wrap it
    stripped = content.strip()
    if (stripped.startswith("{") or stripped.startswith("[")) and not stripped.startswith("<"):
        import json as json_mod

        try:
            json_mod.loads(stripped)
            # Valid JSON — escape and wrap
            return _json_block_html(stripped)
        except (json_mod.JSONDecodeError, ValueError):
            pass  # Not valid JSON, leave as-is

    return content


def _enrich_message(msg) -> dict:
    """Enrich a message with formatted fields for the template."""
    return {
        "id": msg.id,
        "role": msg.role,
        "content": _render_assistant_content(msg.content) if msg.role != "user" else msg.content,
        "created_at": _format_message_time(msg.created_at, msg.role),
    }


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

    # Enrich messages with formatted fields
    enriched = [_enrich_message(msg) for msg in messages]

    return render(
        request,
        "web/chat.html",
        section="chat",
        messages=enriched,
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

    raw_messages = await _transcript(user.id)
    enriched = [_enrich_message(msg) for msg in raw_messages]

    return render(
        request,
        "web/chat.html",
        section="chat",
        messages=enriched,
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


@router.post("/message/{message_id}/delete")
async def chat_message_delete(
    request: Request,
    message_id: int,
    token: str = Form("", alias="_csrf"),
):
    """Delete a single message from the transcript."""
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/chat", status_code=302)

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        return RedirectResponse("/app/chat", status_code=302)

    from app.agent.runtime import web_session_id
    from app.models.agent_message import AgentMessage

    msg = await AgentMessage.objects.filter(
        id=message_id,
        tenant_id=tenant_id,
    ).first()

    if msg is None:
        add_flash(request, "error", "Сообщение не найдено")
        return RedirectResponse("/app/chat", status_code=302)

    # Allow deleting own messages; anyone in workspace can delete assistant messages
    if msg.role == "user" and msg.session_id != await web_session_id(user.id):
        add_flash(request, "error", "Можно удалять только свои сообщения")
        return RedirectResponse("/app/chat", status_code=302)

    await AgentMessage.objects.delete(id=message_id)
    add_flash(request, "success", "Сообщение удалено")
    return RedirectResponse("/app/chat", status_code=302)
