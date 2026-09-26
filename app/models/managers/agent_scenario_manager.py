from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent_scenario import AgentScenario
else:
    # Use string literals to avoid circular imports
    AgentScenario = "AgentScenario"


class AgentScenarioManager(BaseManager):
    """Manager for bot scenario operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..agent_scenario import AgentScenario as B

        super().__init__(B)

    async def get_default_scenario(self, db: AsyncSession) -> Optional[AgentScenario]:
        """
        Retrieve the default scenario for the current tenant.

        Returns the active scenario marked is_default=True, or None if none exists.
        """
        result = await db.execute(select(self.model).where(self.model.is_default == True, self.model.is_active == True))
        return result.scalars().first()

    async def get_active_scenarios(self, db: AsyncSession, skip: int = 0, limit: int = 100) -> Sequence[AgentScenario]:
        """
        Retrieve all active bot scenarios with pagination.

        Args:
                db: Database session
                skip: Amount records to skip
                limit: Maximum amount records to return

        Returns:
                List of active AgentScenario objects
        """
        result = await db.execute(select(self.model).where(self.model.is_active).offset(skip).limit(limit))
        return result.scalars().all()

    async def get_by_name(self, db: AsyncSession, name: Mapped[str]) -> Optional[AgentScenario]:
        """
        Retrieve bot scenario by exact name match.

        Args:
                db: Database session
                name: Scenario name to search for

        Returns:
                AgentScenario object if found, None otherwise
        """
        result = await db.execute(select(self.model).where(self.model.name == name))
        return result.scalars().first()

    async def get_scenarios_by_content_type(self, db: AsyncSession, content_type: str) -> Sequence[AgentScenario]:
        """
        Retrieve scenarios that work with specific content type.

        Args:
                db: Database session
                content_type: Type of content (e.g., 'posts', 'comments', 'videos')

        Returns:
                List of matching AgentScenario objects
        """
        query = select(self.model).where(self.model.is_active)

        # Filter by content type in JSON array
        if content_type:
            query = query.where(self.model.content_types.contains([content_type]))

        result = await db.execute(query)
        return result.scalars().all()

    async def get_scenarios_by_scope(self, db: AsyncSession, scope_filter: dict) -> Sequence[AgentScenario]:
        """
        Retrieve scenarios that match specific scope conditions.

        Args:
                db: Database session
                scope_filter: Dictionary with scope conditions to match

        Returns:
                List of AgentScenario objects that match the scope
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
        is_default: bool = False,
    ) -> AgentScenario:
        """
        Create a new agent scenario with validation.

        Args:
                db: Database session
                name: Scenario name
                scope: JSON conditions and variables for AI behavior
                ai_prompt: AI prompt for response generation
                action_type: Action type bot performs (or None for analysis-only)
                content_types: List of content types to monitor
                is_active: Whether the scenario is active
                is_default: Whether this is the tenant's default scenario

        Returns:
                Created AgentScenario object
        """
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
            is_default=is_default,
        )

        db.add(scenario)
        await db.commit()
        await db.refresh(scenario)
        return scenario

    async def update_scenario_activity(
        self, db: AsyncSession, scenario_id: int, is_active: bool
    ) -> Optional[AgentScenario]:
        """
        Update scenario active status.

        Args:
                db: Database session
                scenario_id: ID of the scenario to update
                is_active: New active status

        Returns:
                Updated AgentScenario object if found, None otherwise
        """
        scenario = await self.get(scenario_id)
        if scenario:
            scenario.is_active = is_active
            await db.commit()
            await db.refresh(scenario)
        return scenario

    async def get_scenarios_by_action_type(
        self, db: AsyncSession, action_type: Optional[str] = None
    ) -> Sequence[AgentScenario]:
        """
        Retrieve scenarios filtered by action type.

        Args:
                db: Database session
                action_type: Action type to filter by (None for analysis-only scenarios)

        Returns:
                List of AgentScenario objects
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
    ) -> Sequence[AgentScenario]:
        """
        Get active scenarios excluding those in a cooldown.

        Args:
                db: Database session
                recently_used_scenario_ids: List of scenario IDs that are in cooldown

        Returns:
                List of available AgentScenario objects
        """
        if not recently_used_scenario_ids:
            return await self.get_active_scenarios(db)

        result = await db.execute(
            select(self.model).where(and_(self.model.is_active, ~self.model.id.in_(recently_used_scenario_ids)))
        )
        return result.scalars().all()
