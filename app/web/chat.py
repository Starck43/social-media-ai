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
import logging
import re
from datetime import timezone

from dateutil import parser as dateutil_parser
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from .chat_workspace import router as workspace_router
from .chat_workspace import selected_task, workspace_denied
from .deps import add_flash, ensure_csrf, render, render_section_denied
from .nav import section_of

router = APIRouter(prefix="/chat")
router.include_router(workspace_router)

# Transcript depth on first paint. The loop only ever feeds the last 20 turns
# to the model (see `agent_messages.recent`), so a long page is scroll, not
# context.
TRANSCRIPT_LIMIT = 40


def _format_message_time(dt_val) -> str:
    """Format created_at for display: RU format, always with the full date.

    Accepts either a datetime object or an ISO-format string from the DB.
    Both sides of the dialog show the same stamp, so a hovered assistant
    reply reads exactly like the author's message next to it.
    """
    if dt_val is None:
        return ""

    # Parse string if needed (DB returns ISO format like "2026-10-04 11:56:36.630582+00:00")
    if isinstance(dt_val, str):
        dt_val = dateutil_parser.parse(dt_val)

    # Use UTC (database stores UTC, Moscow is UTC+3)
    local = dt_val.astimezone(timezone.utc)
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
    """Enrich a message with formatted fields for the template.

    Assistant replies may carry quick-reply choices; they are split off here so
    the bubble never shows the marker and the client renders buttons instead.
    """
    from app.agent.quick_replies import split_quick_replies

    if msg.role == "user":
        content, options = msg.content, []
    else:
        content, options = split_quick_replies(msg.content)
        content = _render_assistant_content(content)
    return {
        "id": msg.id,
        "role": msg.role,
        "content": content,
        "options": options,
        "created_at": _format_message_time(msg.created_at),
    }


async def _confirmation_pending(user_id: int) -> bool:
    """Whether this user's chat is waiting for a yes/no on a staged action or large attachment."""
    from app.agent.runtime import web_session_id

    session_id = await web_session_id(user_id)
    if session_id is None:
        return False
    from app.models import AgentSession

    row = await AgentSession.objects.get(id=session_id)
    if row is None:
        return False
    state = row.state if isinstance(row.state, dict) else {}
    return isinstance(state.get("pending_confirmation"), dict) or isinstance(
        state.get("pending_attachment_confirmation"), dict
    )


def _valid_chat_actor(request: Request) -> tuple[Any, int] | None:
    """Verify concrete integer user.id and tenant_id on the request state."""
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(user, "id", None)
    if user is None or type(user_id) is not int or user_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
        return None
    return user, tenant_id


def _display_transcript(messages) -> list[dict]:
    """Presentation only: tool-call placeholders stay in storage, not in bubbles."""
    return [
        _enrich_message(msg) for msg in messages if msg.role in ("user", "assistant") and (msg.content or "").strip()
    ]


def _membership_role(request: Request) -> str | None:
    """The caller's workspace role codename."""
    tenant_id = getattr(request.state, "tenant_id", None)
    for membership in getattr(request.state, "memberships", []) or []:
        if membership.tenant_id == tenant_id:
            if membership.role is None:
                return None
            codename = membership.role.codename
            value = getattr(codename, "value", None) or getattr(codename, "name", codename)
            return str(value).lower()
    return None


def _membership_role_id(request: Request) -> int | None:
    """The role_id of the caller in the active workspace."""
    tenant_id = getattr(request.state, "tenant_id", None)
    for membership in getattr(request.state, "memberships", []) or []:
        if membership.tenant_id == tenant_id:
            return membership.role_id
    return None


def _is_owner(request: Request) -> bool:
    """True when the caller is the owner of the active workspace."""
    tenant_id = getattr(request.state, "tenant_id", None)
    for membership in getattr(request.state, "memberships", []) or []:
        if membership.tenant_id == tenant_id:
            return membership.is_owner
    return False


def _chat_denied(request: Request, is_ajax: bool):
    """Deny before importing runtime, reading history or mutating messages."""
    perms = getattr(request.state, "web_perms", None)
    if perms is not None and getattr(perms, "can_open_chat", False):
        return None
    message = "Чат недоступен для текущего участника рабочего пространства"
    if is_ajax:
        return JSONResponse({"error": message}, status_code=403)
    return render_section_denied(request, section_of("/app/chat"))


@router.get("")
@router.get("/")
async def chat_page(request: Request):
    """Render the transcript; opens no session and spends nothing.

    For AJAX requests returns JSON so the client can update the DOM.
    """
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )

    denied = _chat_denied(request, is_ajax)
    if denied is not None:
        return denied

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
    enriched = _display_transcript(messages)
    from app.services.chat_attachments import resolve_workspace_attachment_policy

    att_policy = await resolve_workspace_attachment_policy(
        tenant_id=tenant_id,
        user_id=user.id,
        tenant_obj=getattr(request.state, "tenant", None),
    )
    policy_dict = att_policy.to_public_dict()

    if is_ajax:
        return JSONResponse(
            {
                "messages": enriched,
                "awaiting_confirmation": await _confirmation_pending(user.id),
                "is_owner": _is_owner(request),
                "attachment_policy": policy_dict,
            }
        )

    return render(
        request,
        "web/chat.html",
        section="chat",
        messages=enriched,
        is_owner=_is_owner(request),
        awaiting_confirmation=await _confirmation_pending(user.id),
        attachment_policy=policy_dict,
    )


@router.post("")
async def chat_send(
    request: Request,
    text: str = Form(""),
    token: str = Form("", alias="_csrf"),
    task_id: int | None = Form(None),
    attachment_ids: str | None = Form(None),
    confirm_large: str | None = Form(None),
):
    """Run one agent turn and show the answer on the same page.

    For AJAX requests (X-Requested-With / Accept: application/json) returns
    JSON with the enriched transcript so the browser can append messages
    without a full page reload.
    """
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )

    denied = _chat_denied(request, is_ajax)
    if denied is not None:
        return denied

    if not ensure_csrf(request, token):
        if is_ajax:
            return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403)
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/chat", status_code=302)

    actor = _valid_chat_actor(request)
    if actor is None:
        if is_ajax:
            return JSONResponse({"error": "Не авторизован"}, status_code=401)
        return RedirectResponse("/app/chat", status_code=302)
    user, tenant_id = actor

    raw_att_ids = attachment_ids if isinstance(attachment_ids, str) else ""
    parsed_att_ids = [item.strip() for item in raw_att_ids.split(",") if item.strip()]
    confirm_large_flag = (confirm_large if isinstance(confirm_large, str) else "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    text = (text if isinstance(text, str) else "").strip()
    if not text and parsed_att_ids:
        text = "Проанализируй прикреплённые вложения в контексте моих задач."
    if not text:
        if is_ajax:
            return JSONResponse({"error": "Пустое сообщение"}, status_code=400)
        return RedirectResponse("/app/chat", status_code=302)

    resolved_task_id = task_id if type(task_id) is int else None
    # An explicit selection is context, never authority. Validate it again at
    # send time; keep confirmation replies unchanged for the shared runtime gate.
    if resolved_task_id is not None and not await _confirmation_pending(user.id):
        denied = workspace_denied(request)
        if denied is not None:
            return denied
        try:
            definition = await selected_task(request, resolved_task_id) if resolved_task_id > 0 else None
        except PermissionError:
            definition = None
        if definition is None:
            if is_ajax:
                return JSONResponse({"error": "Выбранная задача больше недоступна. Снимите выбор."}, status_code=403)
            add_flash(request, "error", "Выбранная задача недоступна")
            return RedirectResponse("/app/chat", status_code=303)
        text = f"[Контекст: задача #{resolved_task_id}]\n{text}"

    turn_attachments: list[dict] = []
    if parsed_att_ids:
        from app.services.chat_attachments import (
            AttachmentValidationError,
            attachment_store,
            evaluate_turn_attachments,
            resolve_workspace_attachment_policy,
        )

        try:
            loaded_pairs = attachment_store.build_turn_payloads(
                tenant_id=tenant_id,
                user_id=user.id,
                attachment_ids=parsed_att_ids,
            )
            policy = await resolve_workspace_attachment_policy(
                tenant_id=tenant_id,
                user_id=user.id,
                tenant_obj=getattr(request.state, "tenant", None),
            )
            evaluate_turn_attachments([pair[0] for pair in loaded_pairs], policy)
            turn_attachments = [pair[1] for pair in loaded_pairs]
        except AttachmentValidationError as exc:
            if exc.code in {"model_capability_unsupported", "model_token_limit_exceeded"}:
                attachment_store.cleanup_actor(tenant_id=tenant_id, user_id=user.id, attachment_ids=parsed_att_ids)
            if is_ajax:
                return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status_code)
            add_flash(request, "error", exc.message)
            return RedirectResponse("/app/chat", status_code=302)

    from app.agent.runtime import LIMIT_REACHED_PREFIX, handle_web_message

    # The runtime reloads the owner membership before it admits a paid turn.
    try:
        extra_kwargs: dict = {}
        if turn_attachments:
            extra_kwargs["attachments"] = turn_attachments
            if confirm_large_flag:
                extra_kwargs["confirm_large_attachments"] = True
        reply = await handle_web_message(
            text,
            tenant_id=tenant_id,
            user_id=user.id,
            role_id=_membership_role_id(request),
            role_codename=_membership_role(request),
            **extra_kwargs,
        )

        # A spent daily cap is not an answer: it is a refusal. It becomes a real
        # 429 with a code, so the browser styles it as a failure instead of
        # showing "200 with an apology" where the message looked lost.
        limit_reached = isinstance(reply, str) and reply.startswith(LIMIT_REACHED_PREFIX)
        if is_ajax and limit_reached:
            return JSONResponse(
                {"error": reply, "code": "daily_limit_reached", "messages": []},
                status_code=429,
            )

        if reply is None:
            if is_ajax:
                return JSONResponse({"error": "Агент не ответил — попробуйте ещё раз"}, status_code=500)
            add_flash(request, "error", "Агент не ответил — попробуйте ещё раз")

        raw_messages = await _transcript(user.id)
        enriched = _display_transcript(raw_messages)

        if limit_reached:
            add_flash(request, "error", reply)
            return RedirectResponse("/app/chat", status_code=302)

        # "A waiting confirmation" is a question the owner must answer, not a
        # missing answer: the client must not add the "no text" notice on top.
        awaiting = await _confirmation_pending(user.id)
        if is_ajax:
            return JSONResponse(
                {
                    "messages": enriched,
                    "warning": (
                        "Агент завершил обработку без текстового ответа. "
                        "Проверьте результат действия или уточните запрос."
                        if not (reply or "").strip() and not awaiting
                        else None
                    ),
                    "awaiting_confirmation": awaiting,
                    "is_owner": _is_owner(request),
                }
            )

        return render(
            request,
            "web/chat.html",
            section="chat",
            messages=enriched,
            is_owner=_is_owner(request),
            awaiting_confirmation=awaiting,
        )
    except PermissionError:
        if is_ajax:
            return JSONResponse({"error": "Доступ к чату отозван"}, status_code=403)
        return render_section_denied(request, section_of("/app/chat"))
    except Exception:
        logging.getLogger(__name__).exception("Chat POST failed")
        if is_ajax:
            return JSONResponse(
                {"error": "Не удалось обработать сообщение. Подробности в логах."},
                status_code=500,
            )
        add_flash(request, "error", "Не удалось обработать сообщение. Подробности в логах.")
        return RedirectResponse("/app/chat", status_code=302)


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
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )

    denied = _chat_denied(request, is_ajax)
    if denied is not None:
        return denied

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
    from app.models.agent.agent_message import AgentMessage

    msg = await AgentMessage.objects.filter(
        id=message_id,
        tenant_id=tenant_id,
    ).first()

    if msg is None:
        if is_ajax:
            return JSONResponse({"error": "Сообщение не найдено"}, status_code=404)
        add_flash(request, "error", "Сообщение не найдено")
        return RedirectResponse("/app/chat", status_code=302)

    # A transcript and all its messages belong to one member's private chat.
    if msg.session_id != await web_session_id(user.id):
        if is_ajax:
            return JSONResponse({"error": "Можно удалять только свои сообщения"}, status_code=403)
        add_flash(request, "error", "Можно удалять только свои сообщения")
        return RedirectResponse("/app/chat", status_code=302)

    await AgentMessage.objects.delete(id=message_id)

    if is_ajax:
        return JSONResponse({"ok": True})

    add_flash(request, "success", "Сообщение удалено")
    return RedirectResponse("/app/chat", status_code=302)


@router.get("/attachments/policy")
async def chat_attachment_policy(request: Request):
    """Return active workspace and model attachment capabilities and staged files."""
    denied = _chat_denied(request, is_ajax=True)
    if denied is not None:
        return denied
    actor = _valid_chat_actor(request)
    if actor is None:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    user, tenant_id = actor

    from app.services.chat_attachments import attachment_store, resolve_workspace_attachment_policy

    policy = await resolve_workspace_attachment_policy(
        tenant_id=tenant_id,
        user_id=user.id,
        tenant_obj=getattr(request.state, "tenant", None),
    )
    staged = [item.to_public_dict() for item in attachment_store.list_staged(tenant_id=tenant_id, user_id=user.id)]
    return JSONResponse(
        {**policy.to_public_dict(), "staged": staged},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/attachments")
async def chat_attachment_upload(
    request: Request,
    file: UploadFile = File(...),
    token: str = Form("", alias="_csrf"),
):
    """Validate, extract, and privately stage one photo or document for the current actor."""
    denied = _chat_denied(request, is_ajax=True)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403)
    actor = _valid_chat_actor(request)
    if actor is None:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    user, tenant_id = actor

    from app.services.chat_attachments import (
        MAX_IMAGE_BYTES,
        AttachmentValidationError,
        attachment_store,
        inspect_and_extract_attachment,
        resolve_workspace_attachment_policy,
    )

    policy = await resolve_workspace_attachment_policy(
        tenant_id=tenant_id,
        user_id=user.id,
        tenant_obj=getattr(request.state, "tenant", None),
    )
    try:
        raw_bytes = await file.read(MAX_IMAGE_BYTES + 1)
        existing = attachment_store.list_staged(tenant_id=tenant_id, user_id=user.id)
        prepared = inspect_and_extract_attachment(
            filename=getattr(file, "filename", None),
            content_type=getattr(file, "content_type", None),
            data=raw_bytes,
            policy=policy,
            existing_staged=existing,
        )
        attachment_store.save(prepared, raw_bytes)
        return JSONResponse(
            {
                "attachment": prepared.to_public_dict(),
                "policy": policy.to_public_dict(),
            },
            headers={"Cache-Control": "no-store"},
        )
    except AttachmentValidationError as exc:
        return JSONResponse(
            {
                "error": exc.message,
                "code": exc.code,
                "status": "rejected",
                "policy": policy.to_public_dict(),
            },
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store"},
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass


@router.post("/attachments/cleanup")
async def chat_attachment_cleanup(
    request: Request,
    token: str = Form("", alias="_csrf"),
    attachment_ids: str = Form(""),
):
    """Remove specified or all staged attachments for the current workspace actor."""
    denied = _chat_denied(request, is_ajax=True)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403)
    actor = _valid_chat_actor(request)
    if actor is None:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    user, tenant_id = actor

    from app.services.chat_attachments import attachment_store

    raw_ids = attachment_ids if isinstance(attachment_ids, str) else ""
    ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    removed = attachment_store.cleanup_actor(
        tenant_id=tenant_id,
        user_id=user.id,
        attachment_ids=ids if ids else None,
    )
    return JSONResponse({"ok": True, "removed": removed}, headers={"Cache-Control": "no-store"})


@router.post("/attachments/{attachment_id}/delete")
async def chat_attachment_delete(
    request: Request,
    attachment_id: str,
    token: str = Form("", alias="_csrf"),
):
    """Delete one staged attachment owned by the current workspace actor."""
    denied = _chat_denied(request, is_ajax=True)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=403)
    actor = _valid_chat_actor(request)
    if actor is None:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    user, tenant_id = actor

    from app.services.chat_attachments import attachment_store

    deleted = attachment_store.delete(tenant_id=tenant_id, user_id=user.id, attachment_id=attachment_id)
    if not deleted:
        return JSONResponse({"error": "Вложение не найдено"}, status_code=404)
    return JSONResponse({"ok": True, "id": attachment_id}, headers={"Cache-Control": "no-store"})
