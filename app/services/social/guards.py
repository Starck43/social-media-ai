"""Guards checker for bot actions safety."""

import logging
from typing import Any, Optional

from app.models import AgentScenario
from app.models.managers.bot_action_manager import BotActionManager

logger = logging.getLogger(__name__)


def extract_target_user(payload: Optional[dict[str, Any]]) -> Optional[str]:
    """Best-effort target identifier from an action payload (for blacklist/whitelist).

    The analyze job stores payload["target"] as whatever the LLM returned
    (dict with username/user_id/owner_id, or a plain string).
    """
    target = (payload or {}).get("target")
    if isinstance(target, str) and target:
        return target
    if isinstance(target, dict):
        for key in ("username", "user_id", "owner_id", "id"):
            value = target.get(key)
            if value:
                return str(value)
    return None


class GuardsChecker:
    """Checks guards before executing a bot action.

    Guards:
    - rate_limit_per_hour: max actions per hour for the scenario
    - cooldown_seconds: min seconds between actions
    - blacklist: usernames/IDs to never act on
    - whitelist: usernames/IDs to always act on (if set, only these)
    """

    def __init__(self, manager: Optional[BotActionManager] = None):
        self.manager = manager or BotActionManager()

    async def check(
        self,
        scenario: AgentScenario,
        target_user: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Check all guards for a scenario.

        Args:
            scenario: AgentScenario with guards configuration
            target_user: Username or ID of the target user (for blacklist/whitelist)

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """

        def _norm(value: Any) -> str:
            return str(value).lstrip("@").strip().lower()

        # Check whitelist (if set, only these users are allowed)
        if scenario.whitelist:
            if target_user and _norm(target_user) not in {_norm(u) for u in scenario.whitelist}:
                return False, f"User {target_user} not in whitelist"

        # Check blacklist
        if scenario.blacklist and target_user and _norm(target_user) in {_norm(u) for u in scenario.blacklist}:
            return False, f"User {target_user} is blacklisted"

        # Check rate limit
        if scenario.rate_limit_per_hour is not None:
            count = await self.manager.count_actions_in_window(agent_scenario_id=scenario.id, hours=1)
            if count >= scenario.rate_limit_per_hour:
                return False, f"Rate limit exceeded: {count}/{scenario.rate_limit_per_hour} per hour"

        # Check cooldown
        if scenario.cooldown_seconds:
            last_action_time = await self.manager.get_last_action_time(agent_scenario_id=scenario.id)
            if last_action_time:
                from datetime import datetime, timezone

                elapsed = (datetime.now(timezone.utc) - last_action_time).total_seconds()
                if elapsed < scenario.cooldown_seconds:
                    remaining = scenario.cooldown_seconds - elapsed
                    return False, f"Cooldown active: {remaining:.0f}s remaining"

        return True, None


guards_checker = GuardsChecker()
