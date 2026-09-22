"""
Enum types organized by domain.
This package contains all enum types used in the application.
"""

# Analysis types
from .analysis_types import AnalysisType, PeriodType, SentimentLabel

# Bot types
from .bot_types import BotActionType, BotTriggerType

# Content types
from .content_types import ContentType, MediaType

# LLM types
from .llm_types import LLMStrategyType

# Notifications
from .notification_types import NotificationType

# Platform and sources
from .platform_types import MonitoringStatus, PlatformType, SourceType

# User and permissions
from .user_types import ActionType, UserRoleType

__all__ = [
    # User
    "UserRoleType",
    "ActionType",
    # Platform
    "PlatformType",
    "SourceType",
    "MonitoringStatus",
    # Content
    "ContentType",
    "MediaType",
    # Analysis
    "AnalysisType",
    "SentimentLabel",
    "PeriodType",
    # Bot
    "BotActionType",
    "BotTriggerType",
    # LLM
    "LLMStrategyType",
    # Notifications
    "NotificationType",
]
