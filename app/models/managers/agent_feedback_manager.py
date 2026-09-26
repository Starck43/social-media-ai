from __future__ import annotations

from typing import TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_feedback import AgentFeedback


class AgentFeedbackManager(BaseManager["AgentFeedback"]):
    """Ratings collected from chat feedback commands."""

    def __init__(self):
        from ..agent_feedback import AgentFeedback

        super().__init__(AgentFeedback)

    async def recent_notes(self, vote: str = "bad", limit: int = 20) -> list["AgentFeedback"]:
        """Latest notes of one kind — input for reflection and prompt review."""
        from ..agent_feedback import AgentFeedback

        rows = await self.filter(AgentFeedback.vote == vote).order_by(AgentFeedback.created_at.desc()).limit(limit)
        return list(rows)


agent_feedback = AgentFeedbackManager()
