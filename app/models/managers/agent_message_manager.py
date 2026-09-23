from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_message import AgentMessage


class AgentMessageManager(BaseManager["AgentMessage"]):
    """Conversation history for the agent loop."""

    def __init__(self):
        from ..agent_message import AgentMessage

        super().__init__(AgentMessage)

    async def append(
        self,
        *,
        session_id: int,
        role: str,
        content: Optional[str] = None,
        tool_calls: Optional[list[dict[str, Any]]] = None,
        tool_name: Optional[str] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
    ) -> "AgentMessage":
        """Persist one conversation turn."""
        return await self.create(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_name=tool_name,
            tokens=tokens,
            cost=cost,
        )

    async def recent(self, session_id: int, limit: int = 20) -> list["AgentMessage"]:
        """Last `limit` turns, oldest first (ready to feed into an LLM)."""
        model = self.model
        assert model is not None
        rows = await self.filter(session_id=session_id).order_by(model.id.desc()).limit(limit)
        return list(reversed(list(rows)))

    async def clear(self, session_id: int) -> int:
        """Drop the transcript of a conversation (keeps the session row)."""
        return await self.filter(session_id=session_id).delete()

    async def cost_today(self, now: Optional[datetime] = None) -> float:
        """Total USD spent by the agent today (UTC day) — the daily cap check."""
        from datetime import timezone

        now = now or datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self.filter(created_at__gte=day_start)
        return sum(float(row.cost or 0.0) for row in rows)

    async def record_usage(self, *, session_id: int, usage: dict[str, Any]) -> None:
        """Attribute token/cost metadata from an LLM reply to the latest turn."""
        tokens = int(usage.get("total_tokens") or 0)
        cost = float(usage.get("cost") or 0.0)
        if not tokens and not cost:
            return
        model = self.model
        assert model is not None
        latest = await self.filter(session_id=session_id).order_by(model.id.desc()).limit(1)
        latest = list(latest)
        if not latest:
            return
        row = latest[0]
        await self.update_by_id(row.id, tokens=tokens, cost=cost)


agent_messages = AgentMessageManager()
