"""
Enum types organized by domain.
This package contains all enum types used in the application.
"""

# User and permissions
from .user_types import UserRoleType, ActionType

# Platform and sources
from .platform_types import PlatformType, SourceType, MonitoringStatus

# Content types
from .content_types import ContentType, MediaType

# Analysis types
from .analysis_types import AnalysisType, SentimentLabel, PeriodType

# Bot types
from .bot_types import BotActionType, BotTriggerType

# LLM types
from .llm_types import LLMProviderType, LLMStrategyType

# Notifications
from .notification_types import NotificationType


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
    "LLMProviderType",
    "LLMStrategyType",
    
    # Notifications
    "NotificationType",
]
