from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Sequence

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_session import AgentSession


class AgentSessionManager(BaseManager["AgentSession"]):
    """Sessions of the agent: one row per (channel, chat_id)."""

    def __init__(self):
        from ..agent_session import AgentSession

        super().__init__(AgentSession)

    async def get_or_create(
        self,
        *,
        channel: str,
        chat_id: str,
        kind: str = "private",
        is_owner: bool = False,
    ) -> AgentSession | None:
        """Fetch the conversation for this chat, creating it on first contact.

        `is_owner` is upgraded (never downgraded) so adding an id to the
        allowlist later does not require touching existing rows.
        """
        session = await self.get(channel=channel, chat_id=chat_id)
        if session is not None:
            if is_owner and not session.is_owner:
                return await self.update_by_id(session.id, is_owner=True)
            return session

        return await self.create(
            channel=channel,
            chat_id=chat_id,
            kind=kind,
            is_owner=is_owner,
            is_active=True,
            state={},
        )

    async def touch(self, session_id: int, when: Optional[datetime] = None) -> None:
        """Record activity timestamp (used for idle detection / retention)."""
        await self.update_by_id(session_id, last_message_at=when or datetime.now(timezone.utc))

    async def set_state(self, session_id: int, **updates: Any) -> None:
        """Merge keys into `state` (pass value=None to drop a key)."""
        session = await self.get(id=session_id)
        if session is None:
            return
        state = dict(session.state or {})
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        await self.update_by_id(session_id, state=state)

    async def set_update_offset(self, session_id: int, offset: int) -> None:
        await self.set_state(session_id, update_offset=offset)

    async def active_sessions(self, channel: Optional[str] = None) -> Sequence["AgentSession"]:
        """Enabled conversations, optionally narrowed to one channel."""
        if channel:
            return await self.filter(channel=channel, is_active=True)
        return await self.filter(is_active=True)


agent_sessions = AgentSessionManager()
