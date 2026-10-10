"""Agent chat runtime: one inbound message -> tool-calling loop -> reply.

Flow:
  1. Resolve/create an agent session (channel + chat_id).
  2. Load trimmed history (orphan tool messages are dropped).
  3. Ask the model (LLMClient.chat) with the tool registry attached.
  4. While the model returns tool calls: execute them (confirmation-gated for write tools),
  append tool results, ask again - bounded by AGENT_MAX_ITERATIONS.
  5. Persist all messages + cost, reply through the channel.

Guards: chat→tenant routing, membership, per-message iteration cap, per-tenant
daily cost cap.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.agent.confirmation import (
    confirmed_dispatch_scope, make_pending_intent, pending_actor_matches, pending_rejection,
)
from app.agent.identity import (
    RuntimeIdentity, refresh_runtime_identity, resolve_runtime_identity, session_matches_identity,
)
from app.agent.prompts import DEFAULT_SYSTEM_PROMPT
from app.agent.tools import TOOL_REGISTRY, call_tool, to_openai_call, tool_specs
from app.channels.base import Inbound
from app.core.config import settings
from app.core.permissions import get_current_user, has_permission_by_codename, permission_scope
from app.core.tenant_context import tenant_scope
from app.services.tenancy.resolver import (
    Resolution,
    is_platform_owner,
    resolve_inbound,
    tenant_daily_cost_limit,
)

logger = logging.getLogger(__name__)

# Chat surface for the browser UI. One session per user id, mirroring how a
# private messenger chat maps to a conversation.
WEB_CHANNEL = "web"


def is_owner(inbound: Any) -> bool:
    """Platform owner allowlist (env ids), independent of workspace membership."""
    return is_platform_owner(getattr(inbound, "channel", ""), getattr(inbound, "user_id", ""))


def _runtime_error_code(error: BaseException) -> str:
    """Bounded runtime categories; never inspect message, args or traceback."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection_error"
    if isinstance(error, PermissionError):
        return "permission_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    return "unexpected_error"


def _log_runtime_failure(level: int, event: str, error: BaseException) -> None:
    """Log internal static event names and categories, without private data."""
    logger.log(level, "%s error_code=%s", event, _runtime_error_code(error))


def _pending_confirmation(session: Any) -> Optional[dict]:
    state = session.state if isinstance(session.state, dict) else {}
    pending = state.get("pending_confirmation")
    # Expired/legacy intents must be explicitly refused on yes, never executed.
    return pending if isinstance(pending, dict) else None


async def _set_pending(session: Any, payload: dict) -> None:
    state = dict(session.state) if isinstance(session.state, dict) else {}
    state["pending_confirmation"] = payload
    await session.save_state(state)


async def _clear_pending(session: Any) -> None:
    state = dict(session.state) if isinstance(session.state, dict) else {}
    if state.pop("pending_confirmation", None) is not None:
        await session.save_state(state)


def _format_tool_result(name: str, result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return str(result)


def _join_replies(*parts: Any) -> str:
    """Join reply fragments (e.g. a confirmed tool's result and the next turn)."""
    return "\n\n".join(p for p in parts if p)


# Human labels for the confirmation previews — write tools must read as a
# sentence to a non-technical owner, never as a JSON parameter dump.
_PLATFORM_LABELS = {"vk": "ВКонтакте", "telegram": "Телеграм", "max": "MAX"}
_SOURCE_TYPE_LABELS = {
    "user": "пользователь",
    "channel": "канал",
    "group": "группа",
    "public": "сообщество",
    "page": "страница",
    "chat": "чат",
}
_JOB_TYPE_LABELS = {
    "collect": "сбор контента",
    "digest": "ежедневный отчёт",
    "analyze": "анализ",
    "prune": "очистка",
    "learn": "обучение",
    "reflect": "рефлексия",
}


def _human_cron(args: dict) -> str:
    """Human-readable schedule for task_* previews; falls back to a plain line."""
    from app.tasks.cron import cron_to_human

    cron = args.get("cron") or args.get("cron_expr")
    if not cron:
        return "по расписанию"
    return cron_to_human(cron)


def _human_confirmation(name: str, args: dict) -> str:
    """One human sentence describing what a confirmed write tool will do."""
    renderers = {
        "source_add": lambda a: (
            f"добавить источник «{a.get('name') or a.get('external_id')}» — "
            f"{_PLATFORM_LABELS.get(str(a.get('platform')).lower(), a.get('platform') or '?')}, "
            f"{_SOURCE_TYPE_LABELS.get(str(a.get('source_type')).lower(), a.get('source_type') or '?')}, "
            f"@{a.get('external_id')}"
        ),
        "source_disable": lambda a: f"отключить источник №{a.get('source_id')}",
        "task_add": lambda a: (
            f"создать задачу «{a.get('name')}»: {_JOB_TYPE_LABELS.get(a.get('job_type'), a.get('job_type'))}, "
            f"{_human_cron(a)}" + (f", начиная с {a.get('start_date')}" if a.get("start_date") else "")
        ),
        "task_remove": lambda a: f"удалить задачу «{a.get('name')}»",
        "task_pause": lambda a: (
            f"{'возобновить' if a.get('pause') is False else 'поставить на паузу'} задачу «{a.get('name')}»"
        ),
        "scenario_create": lambda a: f"создать сценарий «{a.get('name')}»",
        "scenario_update": lambda a: f"изменить сценарий «{a.get('name') or a.get('id')}»",
        "scenario_clone": lambda a: f"создать копию сценария №{a.get('source_id')} с именем «{a.get('new_name')}»",
        "scenario_delete": lambda a: f"удалить сценарий №{a.get('id')}",
        "digest_send_now": lambda a: f"отправить сводку за «{a.get('period') or 'day'}» в настроенные каналы",
        "action_send": lambda a: (
            f"{'показать превью действия' if a.get('dry_run') else 'опубликовать действие'} №{a.get('action_id')}"
        ),
    }
    render = renderers.get(name)
    if render is not None:
        return f"Требуется подтверждение: {render(args)}. Ответьте «да» или «нет»."
    preview = json.dumps(args, ensure_ascii=False, default=str)
    if len(preview) > 500:
        preview = preview[:500] + "..."
    return f"Требуется подтверждение: {name} с параметрами {preview}. Ответьте «да» или «нет»."


def _check_prompt_injection(text: str) -> Optional[str]:
    """Check for prompt injection attempts and return error message if detected.
    
    Blocks common prompt injection patterns like "ignore previous instructions",
    "disregard system prompt", etc.
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    injection_patterns = [
        r"ignore.*previous.*instruction",
        r"disregard.*system.*prompt",
        r"forget.*previous.*instruction",
        r"override.*system.*prompt",
        r"bypass.*security.*check",
        r"ignore.*security.*rule",
        r"disregard.*policy",
        r"forget.*about.*system",
        r"ignore.*all.*previous",
        r"forget.*everything",
        r"ignore.*rules",
        r"bypass.*auth",
        r"ignore.*permission",
        r"disregard.*access",
        r"override.*auth",
        r"bypass.*check",
        r"ignore.*validation",
        r"disregard.*verify",
        r"forget.*about.*auth",
        r"ignore.*security",
        r"disregard.*security",
        r"forget.*security",
        r"override.*security",
        r"bypass.*security",
        r"ignore.*check",
        r"disregard.*check",
        r"forget.*check",
        r"override.*check",
        r"bypass.*check",
        r"ignore.*rule",
        r"disregard.*rule",
        r"forget.*rule",
        r"override.*rule",
        r"bypass.*rule",
        r"ignore.*policy",
        r"disregard.*policy",
        r"forget.*policy",
        r"override.*policy",
        r"bypass.*policy",
    ]
    
    for pattern in injection_patterns:
        if re.search(pattern, text_lower):
            logger.warning("agent_prompt_injection_blocked")
            return (
                "Ваш запрос содержит попытку обхода безопасности. "
                "Пожалуйста, задайте нормальный вопрос."
            )
    
    return None


async def _write_tool_result(session: Any, pending: dict, content: str) -> None:
    """Replace a staged «Требуется подтверждение» tool row with the real result.

    The loop persists the confirmation text as the tool's result while the call
    is pending; once the owner confirms we overwrite it with the actual outcome
    so a resumed model loop sees the tool as executed, not as still waiting.
    """
    call_id = pending.get("tool_call_id")
    if not call_id:
        return
    from app.models import AgentMessage

    row = (
        await AgentMessage.objects.filter(session_id=session.id, role="tool", tool_name=call_id)
        .order_by(AgentMessage.id.desc())
        .first()
    )
    if row is None:
        return
    await AgentMessage.objects.update_by_id(row.id, content=content)


async def handle_inbound(inbound: Any) -> Optional[str]:
    """
    Process one inbound message, return the reply text (None if ignored).

    Non-owner messages and empty texts are ignored. Write tools that require
    confirmation are staged in session.state['pending_confirmation'] and only
    executed after a positive confirmation reply.
    """
    text = getattr(inbound, "text", None)
    if not text or not text.strip():
        return None

    resolution = await resolve_inbound(inbound)
    if resolution is None:
        logger.info(f"Ignoring unbound message from {inbound.channel}:{inbound.user_id}")
        return None

    # Everything below runs as the workspace that owns this chat: managers
    # filter by tenant_id, writes are stamped with it, tools see only its data.
    with tenant_scope(resolution.tenant_id):
        return await _handle_in_tenant(inbound, resolution)


async def handle_web_message(
    text: str,
    *,
    tenant_id: int,
    user_id: int,
    role_id: int | None = None,
    role_codename: str | None = None,
) -> Optional[str]:
    """Run one agent turn for a browser message in the given workspace.

    The web UI already knows both halves of `Resolution` — `TenantUIMiddleware`
    resolved the workspace from the web membership and the role from
    `tenant_users` — while `resolve_inbound` only knows chats that a messenger
    binding created. Reusing it here would force every `/app/chat` request
    through an invite-code handshake it does not need.

    Everything after the resolution is the shared path: the same session row
    (`channel='web'`, one per user), the same confirmation gate, the same
    daily cost cap and the same tool loop as a messenger chat. Returns the
    reply text, or None when `text` is empty.
    """
    text = (text or "").strip()
    if not text:
        return None

    # Check for prompt injection attempts
    injection_error = _check_prompt_injection(text)
    if injection_error:
        return injection_error

    resolution = Resolution(
        tenant_id=tenant_id,
        channel=WEB_CHANNEL,
        chat_id=str(user_id),
        user_id=str(user_id),
        role_id=role_id,
        role_codename=role_codename,
    )
    inbound = Inbound(
        channel=WEB_CHANNEL,
        chat_id=str(user_id),
        user_id=str(user_id),
        text=text,
    )

    with tenant_scope(tenant_id):
        return await _handle_in_tenant(inbound, resolution)


async def web_session_id(user_id: int) -> Optional[int]:
    """The `channel='web'` agent session id for a user, if one exists yet.

    Read-only: the chat page renders the transcript without creating a session
    row for someone who has simply opened the page.
    """
    from app.models.managers.agent_session_manager import agent_sessions

    session = await agent_sessions.get(channel=WEB_CHANNEL, chat_id=str(user_id))
    return session.id if session is not None else None


async def _handle_in_tenant(inbound: Any, resolution: Any) -> Optional[str]:
    """Admit a bound active identity and retain its scope for the WHOLE turn."""
    if (
        getattr(inbound, "channel", None) != getattr(resolution, "channel", None)
        or str(getattr(inbound, "chat_id", "")) != getattr(resolution, "chat_id", None)
        or str(getattr(inbound, "user_id", "")) != getattr(resolution, "user_id", None)
    ):
        return "Не удалось подтвердить пользователя этого рабочего пространства."
    identity = await resolve_runtime_identity(resolution)
    if identity is None:
        return "Чат не связан с активным пользователем рабочего пространства. Обратитесь к администратору."
    # The scope wraps the whole turn, not just the preamble: the tool loop's gate
    # reads `get_current_user()`, and a scope that closed early would leave it
    # None. Anonymous identities are fail-closed here; keep the admitted actor
    # available for every tool gate and confirmation throughout the turn.
    with permission_scope(identity.user, is_owner=identity.is_owner):
        return await _handle_authorized_turn(inbound, resolution, identity)


async def _handle_authorized_turn(inbound: Any, resolution: Any, identity: RuntimeIdentity) -> Optional[str]:
    """An admitted turn; routing and the existing injection guard are preserved."""
    text = inbound.text.strip()
    if not text:
        return None

    # Check for prompt injection attempts
    injection_error = _check_prompt_injection(text)
    if injection_error:
        # Create a session for logging the injection attempt
        from app.models.managers.agent_session_manager import agent_sessions
        session = await agent_sessions.get_or_create(
            channel=inbound.channel,
            chat_id=str(inbound.chat_id),
            kind="channel" if getattr(inbound, "is_channel_post", False) else "private",
            is_owner=identity.is_owner,
        )
        if session_matches_identity(session, identity):
            await session.append("user", text)
            await session.append("assistant", injection_error)
            await session.touch()
        return injection_error

    if resolution.onboarded:
        from app.models.managers.tenant_manager import tenants

        tenant = await tenants.get(id=resolution.tenant_id)
        name = getattr(tenant, "name", "workspace")
        role_label = "Суперпользователь" if identity.is_owner else "Участник"
        return (
            f"Готово! Этот чат привязан к рабочему пространству «{name}». "
            f"Роль: {role_label}. Спросите что-нибудь или напишите /help."
        )

    limit = await tenant_daily_cost_limit(resolution.tenant_id)
    if limit and await _cost_today() >= limit:
        return "Дневной лимит расходов на агента исчерпан. Попробуйте позже."

    from app.models.managers.agent_session_manager import agent_sessions

    session = await agent_sessions.get_or_create(
        channel=inbound.channel,
        chat_id=str(inbound.chat_id),
        kind="channel" if getattr(inbound, "is_channel_post", False) else "private",
        is_owner=identity.is_owner,
    )
    if not session_matches_identity(session, identity):
        logger.error(f"Failed to create agent session for {inbound.channel}:{inbound.chat_id}")
        return "Не удалось открыть сессию агента."

    fresh = await refresh_runtime_identity(identity)
    if fresh is None or not session_matches_identity(session, fresh):
        return "Доступ к рабочему пространству изменился. Запрос остановлен."
    identity = fresh

    if text.split()[0].split("@")[0].lower() == "/stop":
        pending = _pending_confirmation(session)
        if (
            pending is not None and isinstance(pending.get("authorization"), dict)
            and not pending_actor_matches(pending, identity)
        ):
            return "Это подтверждение относится к другому пользователю."
        from app.models.managers.agent_message_manager import agent_messages

        await agent_messages.clear(session.id)
        await _clear_pending(session)
        return "История диалога очищена."

    if text.split()[0].split("@")[0].lower() == "/help":
        from app.agent.prompts import AGENT_HELP_TEXT

        await session.append("user", text)
        await session.append("assistant", AGENT_HELP_TEXT)
        await session.touch()
        return AGENT_HELP_TEXT

    command = text.split()[0].split("@")[0].lower()
    if command in ("/good", "/bad"):
        return await _handle_feedback(session, inbound, command, text)

    if command == "/memory":
        return await _handle_memory_command(session, identity, text)

    # 1) Confirmation flow first (before touching the model)
    pending = _pending_confirmation(session)
    if pending:
        verdict = text.lower()
        if verdict in ("да", "yes", "y", "ok", "+", "подтверждаю"):
            fresh = await refresh_runtime_identity(identity)
            spec = TOOL_REGISTRY.get(pending.get("name"))
            rejection = "identity_revoked" if fresh is None else pending_rejection(pending, fresh, session, spec)
            if rejection is None:
                with permission_scope(fresh.user, is_owner=fresh.is_owner):
                    if not has_permission_by_codename(fresh.user, spec.required_permission):
                        rejection = "permission_revoked"
            if rejection is not None:
                # Another group member cannot consume the rightful actor's intent.
                if rejection != "actor_mismatch":
                    await _clear_pending(session)
                await session.append("user", text)
                reply = "Подтверждение отклонено. Попросите подготовить действие заново."
                await session.append("assistant", reply)
                await session.touch()
                return reply
            await _clear_pending(session)
            await session.append("user", text)
            try:
                # Consumption awaits storage; reload AFTER it, immediately before effects.
                fresh = await refresh_runtime_identity(identity)
                if fresh is None or pending_rejection(pending, fresh, session, TOOL_REGISTRY.get(pending["name"])):
                    return "Подтверждение отклонено. Попросите подготовить действие заново."
                spec = TOOL_REGISTRY[pending["name"]]
                with permission_scope(fresh.user, is_owner=fresh.is_owner):
                    if not has_permission_by_codename(fresh.user, spec.required_permission):
                        return "Подтверждение отклонено: права изменились."
                    with confirmed_dispatch_scope(fresh, spec, pending["args"]):
                        result = await call_tool(pending["name"], pending["args"])
                body = _format_tool_result(pending["name"], result)
                seed = f"Выполнено: {pending['name']}\n{body[:3000]}"
            except Exception as e:  # noqa: BLE001
                _log_runtime_failure(logging.WARNING, "agent_confirmed_tool_failed", e)
                body = str(e)
                seed = f"Ошибка выполнения {pending['name']}: {e}"
            # Overwrite the staged «Требуется подтверждение» tool row so a
            # resumed model loop sees the tool as executed, not still pending.
            await _write_tool_result(session, pending, body)
            await session.append("assistant", seed)
            await session.touch()
            # Resume the model loop: the owner's «да» completes one step of the
            # plan, and the agent keeps going (create source -> create task)
            # instead of waiting for the next message.
            messages = await _build_messages(session)
            return await _run_tool_loop(session, messages, tool_specs(), limit, identity, seed_reply=seed)
        if verdict in ("нет", "no", "n", "-", "отменяю"):
            if isinstance(pending.get("authorization"), dict) and not pending_actor_matches(pending, identity):
                return "Это подтверждение относится к другому пользователю."
            await _clear_pending(session)
            await session.append("user", text)
            reply = "Отменено."
            await session.append("assistant", reply)
            await session.touch()
            return reply
        # An unrelated group member must not cancel/overwrite another actor's intent.
        if not isinstance(pending.get("authorization"), dict) or pending_actor_matches(pending, identity):
            await _clear_pending(session)

    # 2) Model loop with tool calling
    await session.append("user", text)
    messages = await _build_messages(session)
    return await _run_tool_loop(session, messages, tool_specs(), limit, identity)


async def _dispatch_or_stage(
    session: Any, name: str, args: Any, call_id: Any, identity: RuntimeIdentity,
) -> tuple[str, bool]:
    """Fresh identity and declared rights for every tool, before any effect."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"Unknown tool: {name}", False
    if not isinstance(args, dict) or any(not isinstance(key, str) for key in args):
        return "Некорректные аргументы инструмента.", False
    fresh = await refresh_runtime_identity(identity)
    if fresh is None or not session_matches_identity(session, fresh):
        return "Доступ изменился. Инструмент не выполнен.", True
    with permission_scope(fresh.user, is_owner=fresh.is_owner):
        if spec.required_permission is not None and not has_permission_by_codename(fresh.user, spec.required_permission):
            return "У вас нет прав для вызова этого инструмента.", False
        if spec.confirm:
            existing = _pending_confirmation(session)
            if (
                existing is not None and isinstance(existing.get("authorization"), dict)
                and not pending_actor_matches(existing, fresh)
            ):
                return "Ожидается подтверждение действия другого пользователя.", True
            intent = make_pending_intent(fresh, session, spec, args, call_id)
            if intent is None:
                return "Не удалось безопасно подготовить подтверждение.", True
            await _set_pending(session, intent)
            return _human_confirmation(name, intent["args"]), True
        try:
            result = await call_tool(name, args)
            return _format_tool_result(name, result), False
        except Exception as e:  # existing handler-error semantics; global redaction is separate
            _log_runtime_failure(logging.WARNING, "agent_tool_failed", e)
            return f"Tool error: {e}", False


async def _run_tool_loop(
    session: Any,
    messages: list[dict],
    specs: list[dict],
    limit: Optional[float],
    identity: RuntimeIdentity,
    *,
    seed_reply: Optional[str] = None,
) -> str:
    """Model loop: ask, execute tools, ask again — bounded by AGENT_MAX_ITERATIONS.

    `seed_reply` prefixes the final answer without being persisted by this
    function (the caller already wrote it to the transcript) — it is how a
    confirmed tool's «Выполнено: …» survives a resumed plan.
    """
    reply: Optional[str] = seed_reply
    for _iteration in range(max(1, settings.AGENT_MAX_ITERATIONS)):
        fresh = await refresh_runtime_identity(identity)
        if fresh is None or not session_matches_identity(session, fresh):
            denied = "Доступ к рабочему пространству изменился. Запрос остановлен."
            await session.append("assistant", denied)
            return _join_replies(reply, denied)
        if limit and await _cost_today() >= limit:
            reply = _join_replies(reply, "Дневной лимит расходов исчерпан во время обработки запроса.")
            break

        try:
            response = await _chat(messages, specs)
        except Exception as e:  # noqa: BLE001
            # Keep provider bodies and traceback details out of runtime logs.
            # The existing user-facing status classification is unchanged.
            _log_runtime_failure(logging.ERROR, "agent_llm_call_failed", e)
            error_reply = _llm_error_reply(e)
            reply = _join_replies(reply, error_reply)
            await session.append("assistant", error_reply)
            break

        tool_calls = response.get("tool_calls") or []
        content = (response.get("content") or "").strip()
        await _record_usage(session, response.get("usage") or {})

        if not tool_calls:
            text = content or "Готово."
            reply = _join_replies(reply, text)
            await session.append("assistant", text)
            break

        # Persist assistant turn, then execute each tool call. The in-flight
        # assistant message goes back to the API on the next iteration, so it
        # needs the wire shape (`function.arguments` as a JSON string) — the
        # flat call this loop dispatches on is rejected by the provider.
        await session.append("assistant", content, tool_calls=tool_calls)
        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [to_openai_call(call) for call in tool_calls],
            }
        )

        stop_loop = False
        stop_output = None
        tool_output = ""
        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("arguments") or {}
            if stop_loop:
                # Every provider tool_call needs a result, but no later call may run
                # or replace the first staged intent in this assistant batch.
                tool_output = "Не выполнено: обработка остановлена на предыдущем инструменте."
            else:
                tool_output, stop_loop = await _dispatch_or_stage(session, name, args, call.get("id"), identity)
                if stop_loop:
                    stop_output = tool_output
            await session.append("tool", tool_output, tool_call_id=call.get("id"))
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": tool_output})

        if stop_loop:
            reply = _join_replies(reply, stop_output)
            break
    else:
        reply = _join_replies(reply, "Достигнут лимит итераций агента.")

    if not reply:
        reply = "Не удалось получить ответ агента."
        await session.append("assistant", reply)

    await session.touch()
    return reply


def _llm_error_reply(exc: BaseException) -> str:
    """A short, actionable LLM failure line for the chat — never the raw body.

    `httpx.HTTPStatusError` stringifies to `<status>: <response text>`, which for
    a provider is a JSON error document (hundreds of characters, English keys).
    Dropping it from the reply keeps the transcript readable; the status is the
    part that tells the person what to do, so it stays. 4xx means our request or
    the configuration is wrong, 5xx means the provider is unavailable — and the
    two need different reactions, so they are named rather than both called
    "ошибка".
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        return "Не удалось обратиться к языковой модели. Подробности в логах."

    if status == 401 or status == 403:
        return "Языковая модель отклонила запрос: неверный или истёкший API-ключ. Подробности в логах."
    if status == 429:
        return "Языковая модель временно недоступна из-за лимита запросов. Подробности в логах."
    if 400 <= status < 500:
        return f"Языковая модель отклонила мой запрос ({status}). Подробности в логах."
    return f"Языковая модель сейчас недоступна ({status}). Подробности в логах."


async def _handle_feedback(session: Any, inbound: Any, command: str, text: str) -> str:
    """Record /good or /bad <note> against the last assistant reply."""
    from app.models import AgentMessage
    from app.models.managers.agent_feedback_manager import agent_feedback

    rated = (
        await AgentMessage.objects.filter(session_id=session.id, role="assistant")
        .order_by(AgentMessage.id.desc())
        .first()
    )
    if rated is None:
        return "Оценивать пока нечего — я ещё ничего не отвечал в этом чате."

    note = text[len(command) :].strip() or None
    if command == "/bad" and not note:
        note = "без заметки"
    await agent_feedback.create(
        session_id=session.id,
        message_id=rated.id,
        vote="good" if command == "/good" else "bad",
        note=note,
        voter_channel=getattr(inbound, "channel", None),
        voter_external_id=str(getattr(inbound, "user_id", "") or "") or None,
    )
    reply = "Спасибо, учту." if command == "/good" else f"Принято, учту: {note}"
    await session.append("user", text)
    await session.append("assistant", reply)
    await session.touch()
    return reply


async def _handle_memory_command(session: Any, resolution: Any, text: str) -> str:
    """/memory clear — drop learned facts (owner only). Watermarks survive."""
    parts = text.split(None, 1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""
    if sub != "clear":
        return "Использование: /memory clear — стереть выученные факты о себе."
    if not getattr(resolution, "is_owner", False):
        return "Память может очищать только владелец рабочего пространства."

    from app.models.managers.agent_memory_manager import agent_memory

    removed = await agent_memory.clear_facts()
    await session.append("user", text)
    reply = f"Память очищена: удалено фактов — {removed}."
    await session.append("assistant", reply)
    await session.touch()
    return reply


async def _build_messages(session: Any) -> list[dict]:
    """System prompt (+ style and learned memory) and session history."""
    history = await session.history()
    return [{"role": "system", "content": await build_system_prompt()}] + history


async def build_system_prompt() -> str:
    """System prompt (+ style and learned memory) and session history."""
    from app.agent.prompts import ANALYTICS_SECTION, SCENARIO_SECTION, TASK_SECTION, render_style_block
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.agent_memory_manager import agent_memory
    from app.models.managers.tenant_manager import tenants

    # Use env var if set, otherwise fall back to the built-in default.
    system_prompt = settings.AGENT_SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT

    sections = [system_prompt, SCENARIO_SECTION, TASK_SECTION, ANALYTICS_SECTION]

    tenant = None
    tid = current_tenant_id()
    if tid is not None:
        tenant = await tenants.get(id=tid)
    style_block = render_style_block(getattr(tenant, "agent_style", None) if tenant else None)
    if style_block:
        sections.append(style_block)

    # Append custom system prompt override from workspace settings
    if tenant and tenant.agent_system_prompt:
        sections.append(tenant.agent_system_prompt)

    facts = await agent_memory.snapshot(limit=20)
    if facts:
        lines = "\n".join(f"- {f.key}: {f.value}" for f in facts)
        sections.append(f"Что я знаю о владельце (выучено из общения, может устареть):\n{lines}")

    return "\n\n".join(sections)


async def _chat(messages: list[dict], specs: list[dict]) -> dict:
    """Call the LLM with tool specs + provider fallback; returns {content, tool_calls, usage}."""
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.tenant_manager import tenants
    from app.services.ai.llm_client import chat_with_fallback

    # Get workspace-level overrides
    max_tokens = settings.AGENT_MAX_TOKENS
    temperature = settings.AGENT_TEMPERATURE

    tid = current_tenant_id()
    if tid is not None:
        tenant = await tenants.get(id=tid)
        if tenant:
            if tenant.agent_max_tokens is not None:
                max_tokens = tenant.agent_max_tokens
            if tenant.agent_temperature is not None:
                temperature = tenant.agent_temperature

    from app.models import LLMModel

    narrative_model = await LLMModel.objects.resolve_default_model("text", strategy="cost_efficient")
    return await chat_with_fallback(
        messages,
        preferred_model=narrative_model,
        tools=specs,
        max_tokens=max_tokens,
        temperature=temperature,
    )


async def _cost_today() -> float:
    """Today's USD spend in this workspace: agent chat + digest summaries + learning/reflect."""
    from app.services.tenancy.resolver import daily_cost_today

    return await daily_cost_today()


async def _record_usage(session: Any, usage: dict) -> None:
    if not usage:
        return
    from app.models.managers.agent_message_manager import agent_messages

    await agent_messages.record_usage(session_id=session.id, usage=usage)
