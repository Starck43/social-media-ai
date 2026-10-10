"""Agent session helpers: history loading and LLM message building."""

from __future__ import annotations

from typing import Any

from app.agent.tools import to_openai_call
from app.core.config import settings
from app.models import AgentMessage, AgentSession
from app.models.managers.agent_message_manager import agent_messages
from app.models.managers.agent_session_manager import agent_sessions


def _call_id(call: dict[str, Any], row_id: int, idx: int) -> str:
    return call.get("id") or f"call_{row_id}_{idx}"


async def load_history(session: AgentSession) -> list[dict[str, Any]]:
    """Replay recent turns with tool results paired to their adjacent batch.

    Only the contiguous tool rows immediately following an assistant call batch
    can answer that batch. Global ID membership cannot prove this relationship:
    interrupted turns, duplicate results or reused IDs can form invalid history.
    Unmatched calls/results are omitted without inventing results, expanding the
    history window, changing stored rows or reordering ordinary conversation.
    """
    rows = await agent_messages.recent(session.id, settings.AGENT_HISTORY_LIMIT)
    messages: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if row.role == "tool":
            # A result without the immediately preceding batch is an orphan.
            continue
        msg: dict[str, Any] = {"role": row.role, "content": row.content or ""}
        if row.role != "assistant" or not row.tool_calls:
            messages.append(msg)
            continue

        calls_by_id: dict[str, dict[str, Any]] = {}
        for i, call in enumerate(row.tool_calls):
            cid = _call_id(call, row.id, i)
            if cid not in calls_by_id:
                calls_by_id[cid] = call
        results: list[dict[str, Any]] = []
        answered: set[str] = set()
        while index < len(rows) and rows[index].role == "tool":
            result = rows[index]
            index += 1
            cid = result.tool_name
            if cid not in calls_by_id or cid in answered:
                continue
            answered.add(cid)
            results.append({"role": "tool", "tool_call_id": cid, "content": result.content or ""})
        calls = [
            to_openai_call(call, cid)
            for cid, call in calls_by_id.items()
            if cid in answered
        ]
        if calls:
            msg["tool_calls"] = calls
        messages.append(msg)
        messages.extend(results)
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
