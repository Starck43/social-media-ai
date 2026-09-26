"""
Package containing all query managers for the application.

This package provides query managers that handle database operations for models.
"""

# Analytics managers
from .ai_analytics_manager import AIAnalyticsManager

# Base manager
from .base_manager import BaseManager, Prefetch, prefetch
from .agent_scenario_manager import AgentScenarioManager
from .agent_task_manager import AgentTaskManager
from .bot_action_manager import BotActionManager
from .notification_manager import NotificationManager
from .permission_manager import PermissionManager

# Social monitoring managers
from .platform_manager import PlatformManager
from .role_manager import RoleManager
from .source_manager import SourceManager
from .user_manager import UserManager

__all__ = [
    # Base manager
    "BaseManager",
    "Prefetch",
    "prefetch",
    # Core managers
    "UserManager",
    "RoleManager",
    "PermissionManager",
    "NotificationManager",
    # Social monitoring managers
    "PlatformManager",
    "SourceManager",
    "BotActionManager",
    "AgentScenarioManager",
    "AgentTaskManager",
    # Analytics managers
    "AIAnalyticsManager",
]
