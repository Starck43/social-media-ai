"""
Package containing all query managers for the application.

This package provides query managers that handle database operations for models.
"""

from .user_manager import UserManager
from .social_manager import SocialAccountBaseManager, SocialGroupBaseManager
from .content_manager import PostManager, CommentManager
from .notification_manager import NotificationManager
from .analytics_manager import StatisticsManager, AIAnalysisResultManager
from .permission_manager import PermissionManager

__all__ = [
    'UserManager',
    'SocialAccountBaseManager',
    'SocialGroupBaseManager',
    'PostManager',
    'CommentManager',
    'NotificationManager',
    'StatisticsManager',
    'AIAnalysisResultManager',
    'PermissionManager',
]
