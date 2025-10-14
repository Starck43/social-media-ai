"""
Application Models Package.

This package contains all data models for the application, including:
- Core models (User, SocialAccount, SocialGroup)
— Content models (Post, Comment)
— Analytics models (Statistics, AIAnalysisResult)
— Notification models (Notification)
"""

from __future__ import annotations

# Core models
from .base import Base, TimestampMixin
from .model_type import ModelType

# Import all models to ensure they are registered with SQLAlchemy
from .permission import Permission
from .role import Role
from .user import User

# Social monitoring models
from .platform import Platform
from .source import Source, SourceUserRelationship
from .bot_scenario import BotScenario

# AI models
from .llm_provider import LLMProvider

# Analytics models
from .ai_analytics import AIAnalytics

# Notification models
from .notification import Notification


__all__ = [
    # Base classes
    'Base',
    'TimestampMixin',

    # Core models
    'ModelType',
    'Permission',
    'Role',
    'User',

    # Social monitoring models
    'Platform',
    'Source',
    'SourceUserRelationship',
    'BotScenario',

    # AI models
    'LLMProvider',

    # Analytics models
    'AIAnalytics',

    # Notification models
    'Notification',
]
