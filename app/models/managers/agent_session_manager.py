from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import with_db_session

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_session import AgentSession


def _is_session_chat_conflict(error: IntegrityError) -> bool:
    """Recognize only the named PostgreSQL uniqueness conflict, never its text."""
    original = error.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code != "23505":
        return False
    constraint = getattr(original, "constraint_name", None)
    if constraint is None:
        constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint is None:
        # SQLAlchemy's asyncpg adapter keeps driver diagnostics on the cause.
        constraint = getattr(getattr(original, "__cause__", None), "constraint_name", None)
    return constraint == "uq_agent_session_chat"


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
        if session is None:
            try:
                return await self.create(
                    channel=channel,
                    chat_id=chat_id,
                    kind=kind,
                    is_owner=is_owner,
                    is_active=True,
                    state={},
                )
            except IntegrityError as error:
                # create() owns its transaction and has already rolled it back.
                # Recover only a committed winner visible in the current tenant.
                if not _is_session_chat_conflict(error):
                    raise
                session = await self.get(channel=channel, chat_id=chat_id)
                if session is None:
                    # Foreign/deleted winners are not adopted; no bypass or retry.
                    raise
        if is_owner and not session.is_owner:
            return await self.update_by_id(session.id, is_owner=True)
        return session

    async def touch(self, session_id: int, when: Optional[datetime] = None) -> None:
        """Record activity timestamp (used for idle detection / retention)."""
        await self.update_by_id(session_id, last_message_at=when or datetime.now(timezone.utc))

    @with_db_session
    async def patch_state(
        self, session_id: int, updates: dict[str, Any], session: AsyncSession,
    ) -> dict[str, Any] | None:
        """Merge keys from fresh locked state; None deletes, other keys survive.

        This serializes cooperating key writers, not full-state replacements or
        confirmation consumption. Caller-owned transactions retain the row lock
        until their own commit/rollback, following with_db_session semantics.
        """
        criteria = (self.model.id == session_id, *self._tenant_criterion())
        statement = select(self.model.state).where(*criteria).with_for_update()
        row = (await session.execute(statement)).first()
        if row is None:
            return None
        state = dict(row[0]) if isinstance(row[0], dict) else {}
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        await session.execute(
            update(self.model).where(*criteria).values(state=state)
            .execution_options(synchronize_session=False)
        )
        return state

    async def set_state(self, session_id: int, **updates: Any) -> None:
        """Merge keys into state atomically (pass value=None to drop a key)."""
        await self.patch_state(session_id, updates)

    async def set_update_offset(self, session_id: int, offset: int) -> None:
        await self.set_state(session_id, update_offset=offset)

    async def active_sessions(self, channel: Optional[str] = None) -> Sequence["AgentSession"]:
        """Enabled conversations, optionally narrowed to one channel."""
        if channel:
            return await self.filter(channel=channel, is_active=True)
        return await self.filter(is_active=True)


agent_sessions = AgentSessionManager()
