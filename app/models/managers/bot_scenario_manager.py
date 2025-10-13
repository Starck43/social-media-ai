from __future__ import annotations

from typing import Optional, Sequence, TYPE_CHECKING

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..bot_scenario import BotScenario
else:
    # Use string literals to avoid circular imports
    BotScenario = "BotScenario"


class BotScenarioManager(BaseManager):
    """Manager for bot scenario operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..bot_scenario import BotScenario as B

        super().__init__(B)

    async def get_active_scenarios(self, db: AsyncSession, skip: int = 0, limit: int = 100) -> Sequence[BotScenario]:
        """
        Retrieve all active bot scenarios with pagination.

        Args:
                db: Database session
                skip: Amount records to skip
                limit: Maximum amount records to return

        Returns:
                List of active BotScenario objects
        """
        result = await db.execute(select(self.model).where(self.model.is_active).offset(skip).limit(limit))
        return result.scalars().all()

    async def get_by_name(self, db: AsyncSession, name: Mapped[str]) -> Optional[BotScenario]:
        """
        Retrieve bot scenario by exact name match.

        Args:
                db: Database session
                name: Scenario name to search for

        Returns:
                BotScenario object if found, None otherwise
        """
        result = await db.execute(select(self.model).where(self.model.name == name))
        return result.scalars().first()

    async def get_scenarios_by_content_type(self, db: AsyncSession, content_type: str) -> Sequence[BotScenario]:
        """
        Retrieve scenarios that work with specific content type.

        Args:
                db: Database session
                content_type: Type of content (e.g., 'posts', 'comments', 'videos')

        Returns:
                List of matching BotScenario objects
        """
        query = select(self.model).where(self.model.is_active)

        # Filter by content type in JSON array
        if content_type:
            query = query.where(self.model.content_types.contains([content_type]))

        result = await db.execute(query)
        return result.scalars().all()

    async def get_scenarios_by_scope(self, db: AsyncSession, scope_filter: dict) -> Sequence[BotScenario]:
        """
        Retrieve scenarios that match specific scope conditions.

        Args:
                db: Database session
                scope_filter: Dictionary with scope conditions to match

        Returns:
                List of BotScenario objects that match the scope
        """
        if not scope_filter:
            return await self.get_active_scenarios(db)

        # Get all active scenarios
        scenarios = await self.get_active_scenarios(db)
        matching_scenarios = []

        for scenario in scenarios:
            scope = scenario.scope or {}

            # Check if scenario scope matches filter
            matches = True
            for key, value in scope_filter.items():
                if key not in scope or scope[key] != value:
                    matches = False
                    break

            if matches:
                matching_scenarios.append(scenario)

        return matching_scenarios

    async def create_scenario(
        self,
        db: AsyncSession,
        name: Mapped[str],
        scope: Optional[dict] = None,
        ai_prompt: Optional[str] = None,
        action_type: Optional[str] = None,
        content_types: Optional[list] = None,
        is_active: bool = True,
        cooldown_minutes: int = 30,
    ) -> BotScenario:
        """
        Create a new bot scenario with validation.

        Args:
                db: Database session
                name: Scenario name
                scope: JSON conditions and variables for AI behavior
                ai_prompt: AI prompt for response generation
                action_type: Action type bot performs (or None for analysis-only)
                content_types: List of content types to monitor
                is_active: Whether the scenario is active
                cooldown_minutes: Cooldown period between triggers

        Returns:
                Created BotScenario object
        """
        # Check if scenario with same name already exists
        existing = await self.get_by_name(db, name)
        if existing:
            raise ValueError(f"Scenario with name '{name}' already exists")

        scenario = self.model(
            name=name,
            scope=scope or {},
            ai_prompt=ai_prompt,
            action_type=action_type,
            content_types=content_types or [],
            is_active=is_active,
            cooldown_minutes=cooldown_minutes,
        )

        db.add(scenario)
        await db.commit()
        await db.refresh(scenario)
        return scenario

    async def update_scenario_activity(
        self, db: AsyncSession, scenario_id: int, is_active: bool
    ) -> Optional[BotScenario]:
        """
        Update scenario active status.

        Args:
                db: Database session
                scenario_id: ID of the scenario to update
                is_active: New active status

        Returns:
                Updated BotScenario object if found, None otherwise
        """
        scenario = await self.get(scenario_id)
        if scenario:
            scenario.is_active = is_active
            await db.commit()
            await db.refresh(scenario)
        return scenario

    async def get_scenarios_by_action_type(
        self, db: AsyncSession, action_type: Optional[str] = None
    ) -> Sequence[BotScenario]:
        """
        Retrieve scenarios filtered by action type.

        Args:
                db: Database session
                action_type: Action type to filter by (None for analysis-only scenarios)

        Returns:
                List of BotScenario objects
        """
        query = select(self.model).where(self.model.is_active)

        if action_type is None:
            # Get analysis-only scenarios (action_type is NULL)
            query = query.where(self.model.action_type.is_(None))
        else:
            # Get scenarios with specific action type
            query = query.where(self.model.action_type == action_type)

        result = await db.execute(query)
        return result.scalars().all()

    async def get_scenarios_with_cooldown(
        self, db: AsyncSession, recently_used_scenario_ids: list[int]
    ) -> Sequence[BotScenario]:
        """
        Get active scenarios excluding those in a cooldown.

        Args:
                db: Database session
                recently_used_scenario_ids: List of scenario IDs that are in cooldown

        Returns:
                List of available BotScenario objects
        """
        if not recently_used_scenario_ids:
            return await self.get_active_scenarios(db)

        result = await db.execute(
            select(self.model).where(and_(self.model.is_active, ~self.model.id.in_(recently_used_scenario_ids)))
        )
        return result.scalars().all()
