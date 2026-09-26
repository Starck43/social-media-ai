from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import with_db_session

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..bot_action import BotAction
else:
    BotAction = "BotAction"


class BotActionManager(BaseManager):
    """Manager for bot action ledger operations."""

    def __init__(self):
        from ..bot_action import BotAction as B

        super().__init__(B)

    @with_db_session
    async def get_pending_actions(
        self,
        session: AsyncSession,
        limit: int = 100,
        scenario_id: Optional[int] = None,
    ) -> Sequence[BotAction]:
        """Get pending actions awaiting approval or execution."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        query = select(BotAction).where(BotAction.status == BotActionStatus.PENDING)

        if scenario_id:
            query = query.where(BotAction.agent_scenario_id == scenario_id)

        query = query.order_by(BotAction.created_at.desc()).limit(limit)
        result = await session.execute(query)
        return result.scalars().all()

    @with_db_session
    async def get_recent_actions(
        self,
        session: AsyncSession,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[BotAction]:
        """Get recent actions within the specified time window."""
        from ..bot_action import BotAction

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = (
            select(BotAction).where(BotAction.created_at >= since).order_by(BotAction.created_at.desc()).limit(limit)
        )
        result = await session.execute(query)
        return result.scalars().all()

    @with_db_session
    async def count_actions_in_window(
        self,
        session: AsyncSession,
        agent_scenario_id: int,
        hours: int = 1,
    ) -> int:
        """Count actions for a scenario within the time window (for rate limiting)."""
        from ..bot_action import BotAction

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = select(func.count(BotAction.id)).where(
            and_(
                BotAction.agent_scenario_id == agent_scenario_id,
                BotAction.created_at >= since,
            )
        )
        result = await session.execute(query)
        return result.scalar() or 0

    @with_db_session
    async def get_last_action_time(
        self,
        session: AsyncSession,
        agent_scenario_id: int,
    ) -> Optional[datetime]:
        """Get the timestamp of the last executed action for a scenario (for cooldown)."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        query = (
            select(BotAction.created_at)
            .where(
                and_(
                    BotAction.agent_scenario_id == agent_scenario_id,
                    BotAction.status == BotActionStatus.EXECUTED,
                )
            )
            .order_by(BotAction.created_at.desc())
            .limit(1)
        )
        result = await session.execute(query)
        return result.scalar()

    @with_db_session
    async def approve_action(
        self,
        session: AsyncSession,
        action_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[BotAction]:
        """Approve a pending action for execution."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        action = await self.get(id=action_id, session=session)
        if not action or action.status != BotActionStatus.PENDING:
            return None

        action.status = BotActionStatus.APPROVED
        action.confirmed_by = user_id
        action.confirmed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(action)
        return action

    @with_db_session
    async def reject_action(
        self,
        session: AsyncSession,
        action_id: int,
        user_id: int,
    ) -> Optional[BotAction]:
        """Reject a pending action."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        action = await self.get(id=action_id, session=session)
        if not action or action.status != BotActionStatus.PENDING:
            return None

        action.status = BotActionStatus.REJECTED
        action.confirmed_by = user_id
        action.confirmed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(action)
        return action

    @with_db_session
    async def mark_executed(
        self,
        session: AsyncSession,
        action_id: int,
        result: Optional[dict] = None,
    ) -> Optional[BotAction]:
        """Mark an action as successfully executed."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        action = await self.get(id=action_id, session=session)
        if not action:
            return None

        action.status = BotActionStatus.EXECUTED
        action.result = result or {}
        action.attempts += 1
        await session.commit()
        await session.refresh(action)
        return action

    @with_db_session
    async def mark_failed(
        self,
        session: AsyncSession,
        action_id: int,
        error: str,
    ) -> Optional[BotAction]:
        """Mark an action as failed."""
        from ...types import BotActionStatus
        from ..bot_action import BotAction

        action = await self.get(id=action_id, session=session)
        if not action:
            return None

        action.status = BotActionStatus.FAILED
        action.error = error
        action.attempts += 1
        await session.commit()
        await session.refresh(action)
        return action
