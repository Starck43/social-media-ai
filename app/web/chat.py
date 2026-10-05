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

import json as json_mod
import re
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

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
    """Return content for display; if it's JSON, format it nicely.

    If the content looks like a JSON array or object, wrap it in a
    ```json ``` block so the client renders it with pretty formatting.
    Otherwise return as-is.
    """
    if not content:
        return ""
    # Try to parse as JSON; if it works, format with indentation
    try:
        parsed = json_mod.loads(content)
        # Only format arrays and objects, not plain strings that happen to be valid JSON
        if isinstance(parsed, (list, dict)):
            return json_mod.dumps(parsed, ensure_ascii=False, indent=2)
    except (json_mod.JSONDecodeError, ValueError):
        pass
    # Send raw text; client's renderMarkdown() handles markdown,
    # client-side JS handles JSON blocks.
    return content or ""


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
    """Render the transcript; opens no session and spends nothing.

    For AJAX requests returns JSON so the client can update the DOM.
    """
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("accept") or "")
    )

    from app.agent.runtime import web_session_id

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        if is_ajax:
            return JSONResponse({"messages": [], "is_owner": False})
        return render(request, "web/chat.html", section="chat", messages=[], is_owner=False)

    messages = []
    session_id = await web_session_id(user.id)
    if session_id is not None:
        from app.models.managers.agent_message_manager import agent_messages

        messages = await agent_messages.recent(session_id, limit=TRANSCRIPT_LIMIT)

    # Enrich messages with formatted fields
    enriched = [_enrich_message(msg) for msg in messages]

    if is_ajax:
        return JSONResponse(
            {"messages": enriched, "is_owner": _membership_role(request) == "owner"}
        )

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
    """Run one agent turn and show the answer on the same page.

    For AJAX requests (X-Requested-With / Accept: application/json) returns
    JSON with the enriched transcript so the browser can append messages
    without a full page reload.
    """
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("accept") or "")
    )

    if not ensure_csrf(request, token):
        if is_ajax:
            return JSONResponse(
                {"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403
            )
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/chat", status_code=302)

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        if is_ajax:
            return JSONResponse({"error": "Не авторизован"}, status_code=401)
        return RedirectResponse("/app/chat", status_code=302)

    text = (text or "").strip()
    if not text:
        if is_ajax:
            return JSONResponse({"error": "Пустое сообщение"}, status_code=400)
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
        if is_ajax:
            return JSONResponse({"error": "Агент не ответил — попробуйте ещё раз"}, status_code=500)
        add_flash(request, "error", "Агент не ответил — попробуйте ещё раз")

    raw_messages = await _transcript(user.id)
    enriched = [_enrich_message(msg) for msg in raw_messages]

    if is_ajax:
        return JSONResponse(
            {
                "messages": enriched,
                "is_owner": _membership_role(request) == "owner",
            }
        )

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
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("accept") or "")
    )

    if not ensure_csrf(request, token):
        if is_ajax:
            return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403)
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/chat", status_code=302)

    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None or tenant_id is None:
        if is_ajax:
            return JSONResponse({"error": "Не авторизован"}, status_code=401)
        return RedirectResponse("/app/chat", status_code=302)

    from app.agent.runtime import web_session_id
    from app.models.agent_message import AgentMessage

    msg = await AgentMessage.objects.filter(
        id=message_id,
        tenant_id=tenant_id,
    ).first()

    if msg is None:
        if is_ajax:
            return JSONResponse({"error": "Сообщение не найдено"}, status_code=404)
        add_flash(request, "error", "Сообщение не найдено")
        return RedirectResponse("/app/chat", status_code=302)

    # Allow deleting own messages; anyone in workspace can delete assistant messages
    if msg.role == "user" and msg.session_id != await web_session_id(user.id):
        if is_ajax:
            return JSONResponse({"error": "Можно удалять только свои сообщения"}, status_code=403)
        add_flash(request, "error", "Можно удалять только свои сообщения")
        return RedirectResponse("/app/chat", status_code=302)

    await AgentMessage.objects.delete(id=message_id)

    if is_ajax:
        return JSONResponse({"ok": True})

    add_flash(request, "success", "Сообщение удалено")
    return RedirectResponse("/app/chat", status_code=302)
