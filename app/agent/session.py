"""Agent session helpers: history loading and LLM message building."""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.models import AgentMessage, AgentSession
from app.models.managers.agent_message_manager import agent_messages
from app.models.managers.agent_session_manager import agent_sessions


def _call_id(call: dict[str, Any], row_id: int, idx: int) -> str:
    return call.get("id") or f"call_{row_id}_{idx}"


async def load_history(session: AgentSession) -> list[dict[str, Any]]:
    """Recent turns of the conversation as OpenAI-style messages, oldest first.

    Truncating to the context window can break tool transcripts: an assistant
    message with `tool_calls` whose `role=tool` results fell outside the window
    (or a leading tool result whose assistant call was cut) is rejected by the
    API. Therefore, both sides are filtered against the set of call ids present
    on *both* sides — unmatched tool_calls are stripped from assistant
    messages and unmatched tool results are dropped entirely.
    """
    rows = await agent_messages.recent(session.id, settings.AGENT_HISTORY_LIMIT)

    assistant_ids: set[str] = set()
    tool_ids: set[str] = set()
    for row in rows:
        if row.role == "assistant" and row.tool_calls:
            for i, call in enumerate(row.tool_calls):
                assistant_ids.add(_call_id(call, row.id, i))
        elif row.role == "tool" and row.tool_name:
            tool_ids.add(row.tool_name)
    valid_ids = assistant_ids & tool_ids

    messages: list[dict[str, Any]] = []
    for row in rows:
        if row.role == "tool":
            if row.tool_name not in valid_ids:
                continue
            messages.append({"role": "tool", "tool_call_id": row.tool_name, "content": row.content or ""})
            continue
        msg: dict[str, Any] = {"role": row.role, "content": row.content or ""}
        if row.role == "assistant" and row.tool_calls:
            calls = []
            for i, call in enumerate(row.tool_calls):
                cid = _call_id(call, row.id, i)
                if cid not in valid_ids:
                    continue
                args = call.get("arguments")
                calls.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": call.get("name", ""),
                            "arguments": args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False),
                        },
                    }
                )
            if calls:
                msg["tool_calls"] = calls
        messages.append(msg)
    return messages


async def remember(
    session: AgentSession,
    role: str,
    content: str | None = None,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_name: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
) -> AgentMessage:
    """Persist one conversation turn and bump the activity timestamp."""
    row = await agent_messages.append(
        session_id=session.id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_name=tool_name,
        tokens=tokens,
        cost=cost,
    )
    await agent_sessions.touch(session.id)
    return row
