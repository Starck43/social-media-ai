from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import Text, cast

from .base_manager import BaseManager


class AgentScenarioManager(BaseManager):
    """Manager for bot scenario operations.

    No method takes a session: reads go through the queryset, so the tenant
    guard in `BaseManager` applies. The previous signatures took a caller-owned
    `AsyncSession` and ran hand-built `select()` statements on it, which
    bypassed that guard — a scenario from another workspace was reachable.
    """

    def __init__(self):
        # Use string literal to avoid circular import
        from ..agent_scenario import AgentScenario as B

        super().__init__(B)

    async def get_default_scenario(
        self, *, tenant_id: Optional[int] = None
    ) -> Optional[object]:
        """
        Retrieve the default scenario for the tenant in scope.

        Returns the active scenario marked is_default=True, or None if none
        exists. `tenant_id` narrows explicitly (operator runs); otherwise the
        ambient tenant context decides.
        """
        qs = self.filter(is_default=True, is_active=True)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        return await qs.first()

    async def get_active_scenarios(self, skip: int = 0, limit: int = 100) -> Sequence[object]:
        """
        Retrieve all active bot scenarios with pagination.

        Args:
                skip: Amount records to skip
                limit: Maximum amount records to return

        Returns:
                List of active AgentScenario objects
        """
        return await self.filter(is_active=True).offset(skip).limit(limit)

    async def get_by_name(self, name: str) -> Optional[object]:
        """
        Retrieve bot scenario by exact name match.

        Args:
                name: Scenario name to search for

        Returns:
                AgentScenario object if found, None otherwise
        """
        return await self.filter(name=name).first()

    async def get_scenarios_by_content_type(self, content_type: str) -> Sequence[object]:
        """
        Retrieve scenarios that work with specific content type.

        Args:
                content_type: Type of content (e.g., 'posts', 'comments', 'videos')

        Returns:
                List of matching AgentScenario objects
        """
        qs = self.filter(is_active=True)
        if content_type:
            # `content_types` is `JSON`, not `JSONB`: `.contains()` there emits
            # `LIKE`, which PostgreSQL rejects on a `json` column. Cast to text
            # and match the quoted value inside the array literal.
            as_text = cast(self.model.content_types, Text)
            qs = qs.filter(as_text.contains(f'"{content_type}"'))
        return await qs

    async def get_scenarios_by_scope(self, scope_filter: dict) -> Sequence[object]:
        """
        Retrieve scenarios that match specific scope conditions.

        Args:
                scope_filter: Dictionary with scope conditions to match

        Returns:
                List of AgentScenario objects that match the scope
        """
        if not scope_filter:
            return await self.get_active_scenarios()

        # Get all active scenarios
        scenarios = await self.get_active_scenarios()
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
        name: str,
        scope: Optional[dict] = None,
        ai_prompt: Optional[str] = None,
        action_type: Optional[str] = None,
        content_types: Optional[list] = None,
        is_active: bool = True,
        is_default: bool = False,
    ) -> object:
        """
        Create a new agent scenario with validation.

        Args:
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
        existing = await self.get_by_name(name)
        if existing:
            raise ValueError(f"Scenario with name '{name}' already exists")

        # `create()` stamps tenant_id from the ambient scope (fail-closed).
        return await self.create(
            name=name,
            scope=scope or {},
            ai_prompt=ai_prompt,
            action_type=action_type,
            content_types=content_types or [],
            is_active=is_active,
            is_default=is_default,
        )

    async def update_scenario_activity(self, scenario_id: int, is_active: bool) -> Optional[object]:
        """
        Update scenario active status.

        Args:
                scenario_id: ID of the scenario to update
                is_active: New active status

        Returns:
                Updated AgentScenario object if found, None otherwise
        """
        return await self.update_by_id(scenario_id, is_active=is_active)

    async def get_scenarios_by_action_type(self, action_type: Optional[str] = None) -> Sequence[object]:
        """
        Retrieve scenarios filtered by action type.

        Args:
                action_type: Action type to filter by (None for analysis-only scenarios)

        Returns:
                List of AgentScenario objects
        """
        qs = self.filter(is_active=True)

        if action_type is None:
            # Get analysis-only scenarios (action_type is NULL)
            qs = qs.filter(self.model.action_type.is_(None))
        else:
            # Get scenarios with specific action type
            qs = qs.filter(action_type=action_type)

        return await qs

    async def get_scenarios_with_cooldown(self, recently_used_scenario_ids: list[int]) -> Sequence[object]:
        """
        Get active scenarios excluding those in a cooldown.

        Args:
                recently_used_scenario_ids: List of scenario IDs that are in cooldown

        Returns:
                List of available AgentScenario objects
        """
        if not recently_used_scenario_ids:
            return await self.get_active_scenarios()

        return await self.filter(is_active=True).exclude(id__in=recently_used_scenario_ids)
