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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.agent.prompts import AGENT_SYSTEM_PROMPT
from app.agent.tools import TOOL_REGISTRY, call_tool, tool_specs
from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.services.tenancy.resolver import is_platform_owner, resolve_inbound, tenant_daily_cost_limit

logger = logging.getLogger(__name__)


def is_owner(inbound: Any) -> bool:
    """Platform owner allowlist (env ids), independent of workspace membership."""
    return is_platform_owner(getattr(inbound, "channel", ""), getattr(inbound, "user_id", ""))


def _pending_confirmation(session: Any) -> Optional[dict]:
    state = session.state or {}
    pending = state.get("pending_confirmation")
    if isinstance(pending, dict) and pending.get("expires_at", "") > datetime.now(timezone.utc).isoformat():
        return pending
    if pending:
        state.pop("pending_confirmation", None)
        session.state = state
    return None


async def _set_pending(session: Any, payload: dict) -> None:
    state = dict(session.state or {})
    state["pending_confirmation"] = payload
    await session.save_state(state)


async def _clear_pending(session: Any) -> None:
    state = dict(session.state or {})
    if state.pop("pending_confirmation", None) is not None:
        await session.save_state(state)


def _format_tool_result(name: str, result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _confirmation_text(call_name: str, args: dict) -> str:
    preview = json.dumps(args, ensure_ascii=False, default=str)
    if len(preview) > 500:
        preview = preview[:500] + "..."
    return f"Требуется подтверждение: {call_name} с параметрами {preview}. Ответьте «да» или «нет»."


async def handle_inbound(inbound: Any) -> Optional[str]:
    """
    Process one inbound message, return the reply text (None if ignored).

    Non-owner messages and empty texts are ignored. Write tools that require
    confirmation are staged in session.state['pending_confirmation'] and only
    executed after a positive confirmation reply.
    """
    if not getattr(inbound, "text", None):
        return None

    resolution = await resolve_inbound(inbound)
    if resolution is None:
        logger.info(f"Ignoring unbound message from {inbound.channel}:{inbound.user_id}")
        return None

    # Everything below runs as the workspace that owns this chat: managers
    # filter by tenant_id, writes are stamped with it, tools see only its data.
    with tenant_scope(resolution.tenant_id):
        return await _handle_in_tenant(inbound, resolution)


async def _handle_in_tenant(inbound: Any, resolution: Any) -> Optional[str]:
    """Agent turn inside an already resolved workspace."""
    text = inbound.text.strip()

    if resolution.onboarded:
        from app.models.managers.tenant_manager import tenants

        tenant = await tenants.get(id=resolution.tenant_id)
        name = getattr(tenant, "name", "workspace")
        return (
            f"Готово! Этот чат привязан к рабочему пространству «{name}». "
            f"Роль: {resolution.role}. Спросите что-нибудь или напишите /help."
        )

    limit = await tenant_daily_cost_limit(resolution.tenant_id)
    if limit and await _cost_today() >= limit:
        return "Дневной лимит расходов на агента исчерпан. Попробуйте позже."

    from app.models.managers.agent_session_manager import agent_sessions

    session = await agent_sessions.get_or_create(
        channel=inbound.channel,
        chat_id=str(inbound.chat_id),
        kind="channel" if getattr(inbound, "is_channel_post", False) else "private",
        is_owner=resolution.is_owner,
    )
    if session is None:
        logger.error(f"Failed to create agent session for {inbound.channel}:{inbound.chat_id}")
        return "Не удалось открыть сессию агента."

    if text.split()[0].split("@")[0].lower() == "/stop":
        from app.models.managers.agent_message_manager import agent_messages

        await agent_messages.clear(session.id)
        await _clear_pending(session)
        return "История диалога очищена."

    # 1) Confirmation flow first (before touching the model)
    pending = _pending_confirmation(session)
    if pending:
        verdict = text.lower()
        if verdict in ("да", "yes", "y", "ok", "+", "подтверждаю"):
            await _clear_pending(session)
            await session.append("user", text)
            try:
                result = await call_tool(pending["name"], pending["args"])
                body = _format_tool_result(pending["name"], result)
                reply = f"Выполнено: {pending['name']}\n{body[:3000]}"
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Confirmed tool {pending['name']} failed: {e}")
                reply = f"Ошибка выполнения {pending['name']}: {e}"
            await session.append("assistant", reply)
            await session.touch()
            return reply
        if verdict in ("нет", "no", "n", "-", "отменяю"):
            await _clear_pending(session)
            await session.append("user", text)
            reply = "Отменено."
            await session.append("assistant", reply)
            await session.touch()
            return reply
        # Not a clear verdict - drop the pending action and fall through to the model
        await _clear_pending(session)

    # 2) Model loop with tool calling
    await session.append("user", text)
    messages = await _build_messages(session)
    specs = tool_specs()

    reply: Optional[str] = None
    for _iteration in range(max(1, settings.AGENT_MAX_ITERATIONS)):
        if limit and await _cost_today() >= limit:
            reply = "Дневной лимит расходов исчерпан во время обработки запроса."
            break

        try:
            response = await _chat(messages, specs)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Agent LLM call failed: {e}", exc_info=True)
            reply = f"Ошибка LLM: {e}"
            await session.append("assistant", reply)
            break

        tool_calls = response.get("tool_calls") or []
        content = (response.get("content") or "").strip()
        await _record_usage(session, response.get("usage") or {})

        if not tool_calls:
            reply = content or "Готово."
            await session.append("assistant", reply)
            break

        # Persist assistant turn, then execute each tool call
        await session.append("assistant", content, tool_calls=tool_calls)
        messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})

        stop_loop = False
        tool_output = ""
        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("arguments") or {}
            spec = TOOL_REGISTRY.get(name)
            if spec is None:
                tool_output = f"Unknown tool: {name}"
            elif spec.confirm:
                await _set_pending(
                    session,
                    {
                        "name": name,
                        "args": args,
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    },
                )
                tool_output = _confirmation_text(name, args)
                stop_loop = True
            else:
                try:
                    result = await call_tool(name, args)
                    tool_output = _format_tool_result(name, result)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Tool {name} failed: {e}")
                    tool_output = f"Tool error: {e}"
            await session.append("tool", tool_output, tool_call_id=call.get("id"))
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": tool_output})

        if stop_loop:
            reply = tool_output
            break
    else:
        reply = reply or "Достигнут лимит итераций агента."

    if reply is None:
        reply = "Не удалось получить ответ агента."
        await session.append("assistant", reply)

    await session.touch()
    return reply


async def _build_messages(session: Any) -> list[dict]:
    """System prompt + session history as OpenAI-style messages."""
    history = await session.history()
    return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}] + history


async def _chat(messages: list[dict], specs: list[dict]) -> dict:
    """Call the LLM with tool specs; returns {content, tool_calls, usage}."""
    from app.services.ai.llm_client import LLMClient
    from app.services.digest.builder import resolve_model

    model = await resolve_model()
    client = await LLMClient.create(model)
    return await client.chat(messages, tools=specs)


async def _cost_today() -> float:
    from app.models.managers.agent_message_manager import agent_messages

    return await agent_messages.cost_today()


async def _record_usage(session: Any, usage: dict) -> None:
    if not usage:
        return
    from app.models.managers.agent_message_manager import agent_messages

    await agent_messages.record_usage(session_id=session.id, usage=usage)
