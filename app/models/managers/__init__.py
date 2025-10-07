"""
Package containing all query managers for the application.

This package provides query managers that handle database operations for models.
"""

from .user_manager import UserManager
from .role_manager import RoleManager
from .permission_manager import PermissionManager
from .notification_manager import NotificationManager

# Social monitoring managers
from .platform_manager import PlatformManager
from .source_manager import SourceManager, SourceUserRelationshipManager
from .bot_scenario_manager import BotScenarioManager

# Analytics managers
from .ai_analytics_manager import AIAnalyticsManager

# Base manager
from .base_manager import BaseManager

__all__ = [
    # Base manager
    'BaseManager',

    # Core managers
    'UserManager',
    'RoleManager',
    'PermissionManager',
    'NotificationManager',

    # Social monitoring managers
    'PlatformManager',
    'SourceManager',
    'SourceUserRelationshipManager',
    'BotScenarioManager',

    # Analytics managers
    'AIAnalyticsManager',
]
