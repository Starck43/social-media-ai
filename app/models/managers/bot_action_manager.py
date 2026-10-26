"""Bot action ledger queries and status transitions.

Raw ``select`` statements carry ``_tenant_criterion()`` explicitly, so ledger
reads fail closed exactly like the queryset paths of ``BaseManager``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import with_db_session
from app.types import BotActionStatus

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..scheduling.bot_action import BotAction


class BotActionManager(BaseManager["BotAction"]):
    """Ledger of proposed bot actions (created PENDING with ``dry_run=True``).

    Status machine: PENDING → APPROVED | REJECTED; APPROVED → EXECUTED | FAILED.
    A transition from any other status is refused (returns None). Approval here
    is a status change only; actor-bound approval, idempotency and external
    execution belong to a separate activation contract (see P9-T08).
    """

    def __init__(self) -> None:
        from ..scheduling.bot_action import BotAction

        super().__init__(BotAction)

    def _scope(self, agent_task_id: int | None, agent_scenario_id: int | None) -> ColumnElement[bool] | None:
        """Per-task scope; scenario only for legacy rows written without a task."""
        model = self.model
        if agent_task_id is not None:
            return model.agent_task_id == agent_task_id
        if agent_scenario_id is not None:
            return and_(model.agent_task_id.is_(None), model.agent_scenario_id == agent_scenario_id)
        return None

    @with_db_session
    async def get_pending_actions(
        self,
        session: AsyncSession,
        limit: int = 100,
        scenario_id: int | None = None,
    ) -> Sequence[BotAction]:
        """Pending actions of the current workspace, newest first."""
        model = self.model
        query = select(model).where(model.status == BotActionStatus.PENDING, *self._tenant_criterion())
        if scenario_id:
            query = query.where(model.agent_scenario_id == scenario_id)
        query = query.order_by(model.created_at.desc()).limit(limit)
        return (await session.execute(query)).scalars().all()

    @with_db_session
    async def get_recent_actions(
        self,
        session: AsyncSession,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[BotAction]:
        """Actions of the current workspace created in the last ``hours``, newest first."""
        model = self.model
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = (
            select(model)
            .where(model.created_at >= since, *self._tenant_criterion())
            .order_by(model.created_at.desc())
            .limit(limit)
        )
        return (await session.execute(query)).scalars().all()

    @with_db_session
    async def count_actions_in_window(
        self,
        session: AsyncSession,
        agent_task_id: int | None = None,
        hours: int = 1,
        agent_scenario_id: int | None = None,
    ) -> int:
        """Count actions created within the window (rate limiting).

        Counted per task: the guards live on the task, and two tasks sharing a
        scenario must not spend one budget. ``agent_scenario_id`` covers rows
        written before that move, which carry a NULL task.
        """
        scope = self._scope(agent_task_id, agent_scenario_id)
        if scope is None:
            return 0
        model = self.model
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = select(func.count(model.id)).where(scope, model.created_at >= since, *self._tenant_criterion())
        return (await session.execute(query)).scalar() or 0

    @with_db_session
    async def get_last_action_time(
        self,
        session: AsyncSession,
        agent_task_id: int | None = None,
        agent_scenario_id: int | None = None,
    ) -> datetime | None:
        """Creation time of the last EXECUTED action (cooldown), same scope as the count."""
        scope = self._scope(agent_task_id, agent_scenario_id)
        if scope is None:
            return None
        model = self.model
        query = (
            select(model.created_at)
            .where(scope, model.status == BotActionStatus.EXECUTED, *self._tenant_criterion())
            .order_by(model.created_at.desc())
            .limit(1)
        )
        return (await session.execute(query)).scalar()

    async def _transition(
        self,
        session: AsyncSession,
        action_id: int,
        allowed_from: frozenset[BotActionStatus],
        status: BotActionStatus,
        *,
        count_attempt: bool = False,
        **changes: Any,
    ) -> BotAction | None:
        """Apply one status transition to a visible row; None if missing or not allowed."""
        action = await self.get(id=action_id, session=session)
        if action is None or action.status not in allowed_from:
            return None
        action.status = status
        if count_attempt:
            action.attempts = (action.attempts or 0) + 1
        for field, value in changes.items():
            setattr(action, field, value)
        await session.commit()
        await session.refresh(action)
        return action

    @with_db_session
    async def approve_action(
        self,
        session: AsyncSession,
        action_id: int,
        user_id: int | None = None,
    ) -> BotAction | None:
        """PENDING → APPROVED, recording the confirming membership."""
        return await self._transition(
            session,
            action_id,
            _DECIDABLE,
            BotActionStatus.APPROVED,
            confirmed_by=user_id,
            confirmed_at=datetime.now(UTC),
        )

    @with_db_session
    async def reject_action(
        self,
        session: AsyncSession,
        action_id: int,
        user_id: int,
    ) -> BotAction | None:
        """PENDING → REJECTED, recording the deciding membership."""
        return await self._transition(
            session,
            action_id,
            _DECIDABLE,
            BotActionStatus.REJECTED,
            confirmed_by=user_id,
            confirmed_at=datetime.now(UTC),
        )

    @with_db_session
    async def mark_executed(
        self,
        session: AsyncSession,
        action_id: int,
        result: dict[str, Any] | None = None,
    ) -> BotAction | None:
        """APPROVED → EXECUTED with the provider result; counts one attempt."""
        return await self._transition(
            session, action_id, _EXECUTABLE, BotActionStatus.EXECUTED, count_attempt=True, result=result or {}
        )

    @with_db_session
    async def mark_failed(
        self,
        session: AsyncSession,
        action_id: int,
        error: str,
    ) -> BotAction | None:
        """APPROVED → FAILED with a redacted error; counts one attempt."""
        return await self._transition(
            session, action_id, _EXECUTABLE, BotActionStatus.FAILED, count_attempt=True, error=error
        )


_DECIDABLE = frozenset({BotActionStatus.PENDING})
_EXECUTABLE = frozenset({BotActionStatus.APPROVED})
