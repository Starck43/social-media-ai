"""Guards checker for bot action safety."""

import logging
from typing import Any, Optional

from app.models import AgentTask
from app.models.managers.bot_action_manager import BotActionManager

logger = logging.getLogger(__name__)


class GuardsChecker:
    """Checks guards before executing a bot action.

    Guards live on the task, not the scenario: whether to act on an analysis is a
    property of the run that produced it. `check()` therefore takes the task, and
    `check_for_action()` resolves it from an already-created action row (the agent
    chat path has a BotAction but no task in hand).

    Guards bound *how much* an automation may do:
    - rate_limit_per_hour: max actions per hour for the task
    - cooldown_seconds: min seconds between actions

    They deliberately do not decide *who* it may address: the retired
    blacklist/whitelist fields were removed instead of kept as a second,
    unexplained recipient policy.
    """

    def __init__(self, manager: Optional[BotActionManager] = None):
        self.manager = manager or BotActionManager()

    async def check(
        self,
        task: Optional[AgentTask],
        target_user: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Check all guards for a task.

        Args:
                task: the task whose guards apply (None = an ad-hoc run with no
                        configuration, which is allowed through — there is nothing to check)
                target_user: kept for call-site compatibility; no guard reads it

        Returns:
                Tuple of (allowed: bool, reason: Optional[str])
        """
        # No task means no guards were configured for this run. Nothing to
        # enforce, and an ad-hoc manual run should not be blocked by a limit it
        # never opted into.
        if task is None:
            return True, None

        # Check rate limit. Counted per task, so two tasks sharing a scenario do
        # not spend one budget between them.
        if task.rate_limit_per_hour is not None:
            count = await self.manager.count_actions_in_window(agent_task_id=task.id, hours=1)
            if count >= task.rate_limit_per_hour:
                return False, f"Rate limit exceeded: {count}/{task.rate_limit_per_hour} per hour"

        # Check cooldown
        if task.cooldown_seconds:
            last_action_time = await self.manager.get_last_action_time(agent_task_id=task.id)
            if last_action_time:
                from datetime import datetime, timezone

                elapsed = (datetime.now(timezone.utc) - last_action_time).total_seconds()
                if elapsed < task.cooldown_seconds:
                    remaining = task.cooldown_seconds - elapsed
                    return False, f"Cooldown active: {remaining:.0f}s remaining"

        return True, None

    async def check_for_action(self, action: Any, target_user: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """Guards for an existing action row.

        Used where only the BotAction is in hand (the agent chat path). Resolves
        the task the action was created under. An action created before the move
        has no task: its guards lived on the scenario, which no longer holds them,
        so there is no configuration left to enforce and the action is allowed
        rather than blocked on a rule that no longer exists anywhere.
        """
        task_id = getattr(action, "agent_task_id", None)
        if not task_id:
            return True, None
        task = await AgentTask.objects.get(id=task_id)
        if not task:
            return True, None
        return await self.check(task, target_user=target_user)


guards_checker = GuardsChecker()
