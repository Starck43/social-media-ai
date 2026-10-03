"""
Application Models Package.

This package contains all data models for the application, including:
- Core models (User, SocialAccount, SocialGroup)
— Content models (Post, Comment)
— Analytics models (Statistics, AIAnalysisResult)
— Notification models (Notification)
"""

from __future__ import annotations

# Agent runtime (import after Base: these modules do `from . import Base`)
from .agent_feedback import AgentFeedback
from .agent_memory import AgentMemory
from .agent_message import AgentMessage
from .agent_session import AgentSession

# Analytics models
from .ai_analytics import AIAnalytics

# Core models
from .base import Base, TenantScopedMixin, TimestampMixin
from .agent_scenario import AgentScenario
from .bot_action import BotAction
from .digest_run import DigestRun
from .job import Job
from .llm_model import LLMModel

# AI models
from .llm_provider import LLMProvider
from .model_type import ModelType

# Notification models
from .notification import Notification

# Raw collected content (staged until an analysis consumes it)
from .collected_item import CollectedItem

# Import all models to ensure they are registered with SQLAlchemy
from .permission import Permission

# Social monitoring models
from .platform import Platform
from .role import Role

# Task runner / jobs
from .agent_task import AgentTask
from .source import Source

# Multi-tenant core
from .tenant import (
    Tenant,
    TenantChannel,
    TenantInvite,
    TenantUser,
)
from .user import User
from .user_credential import UserCredential

__all__ = [
    # Base classes
    "Base",
    "TimestampMixin",
    "TenantScopedMixin",
    # Core models
    "ModelType",
    "Permission",
    "Role",
    "User",
    # Multi-tenant
    "Tenant",
    "TenantUser",
    "TenantInvite",
    "TenantChannel",
    "UserCredential",
    # Social monitoring models
    "Platform",
    "Source",
    "AgentScenario",
    "BotAction",
    # AI models
    "LLMProvider",
    "LLMModel",
    # Analytics models
    "AIAnalytics",
    # Task runner / jobs
    "AgentTask",
    "Job",
    "DigestRun",
    # Agent runtime
    "AgentSession",
    "AgentMessage",
    "AgentMemory",
    "AgentFeedback",
    # Notification models
    "Notification",
    # Raw collected content
    "CollectedItem",
]
