"""
Application Models Package.

This package contains all data models for the application, including:
- Core models (User, SocialAccount, SocialGroup)
— Content models (Post, Comment)
— Analytics models (Statistics, AIAnalysisResult)
— Notification models (Notification)
"""

from __future__ import annotations

# Analytics models
from .ai_analytics import AIAnalytics

# Core models
from .base import Base, TimestampMixin
from .bot_scenario import BotScenario
from .digest_run import DigestRun
from .job import Job
from .llm_model import LLMModel

# Agent runtime (import after Base: these modules do `from . import Base`)
from .agent_memory import AgentMemory
from .agent_message import AgentMessage
from .agent_session import AgentSession

# AI models
from .llm_provider import LLMProvider
from .model_type import ModelType

# Notification models
from .notification import Notification

# Import all models to ensure they are registered with SQLAlchemy
from .permission import Permission

# Social monitoring models
from .platform import Platform
from .role import Role

# Scheduler / jobs
from .schedule import Schedule
from .source import Source, SourceUserRelationship
from .user import User

__all__ = [
    # Base classes
    "Base",
    "TimestampMixin",
    # Core models
    "ModelType",
    "Permission",
    "Role",
    "User",
    # Social monitoring models
    "Platform",
    "Source",
    "SourceUserRelationship",
    "BotScenario",
    # AI models
    "LLMProvider",
    "LLMModel",
    # Analytics models
    "AIAnalytics",
    # Scheduler / jobs
    "Schedule",
    "Job",
    "DigestRun",
    # Agent runtime
    "AgentSession",
    "AgentMessage",
    "AgentMemory",
    # Notification models
    "Notification",
]
