"""Public model facade and explicit manager registration."""

from __future__ import annotations

# Agent runtime (import after Base: these modules do `from . import Base`)
from .agent_feedback import AgentFeedback
from .agent_memory import AgentMemory
from .agent_message import AgentMessage
from .agent_scenario import AgentScenario
from .agent_session import AgentSession

# Task runner / jobs
from .agent_task import AgentTask

# Analytics models
from .ai_analytics import AIAnalytics

# Core models
from .base import Base, TenantScopedMixin, TimestampMixin
from .bot_action import BotAction

# Raw collected content (staged until an analysis consumes it)
from .collected_item import CollectedItem
from .digest_run import DigestRun
from .job import Job
from .llm_attempt import LLMAttempt
from .llm_budget import LLMBudgetDay, LLMBudgetReservation
from .llm.llm_budget_baseline import LLMBudgetBaseline
from .llm_dispatch_receipt import LLMDispatchReceipt
from .llm_model import LLMModel
from .llm_outcome_event import LLMOutcomeEvent

# AI models
from .llm_provider import LLMProvider
from .model_type import ModelType

# Notification models
from .notification import Notification

# Import all models to ensure they are registered with SQLAlchemy
from .permission import Permission

# Social monitoring models
from .platform import Platform
from .role import PlatformRole, Role
from .source import Source
from .tenancy.workspace_role import WorkspaceRole

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
    "PlatformRole",
    "WorkspaceRole",
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
    "LLMAttempt",
    "LLMBudgetBaseline",
    "LLMBudgetDay",
    "LLMBudgetReservation",
    "LLMDispatchReceipt",
    "LLMOutcomeEvent",
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

# All canonical classes exist before any public model manager is bound.
import sys as _sys

from .bootstrap import bind_managers as _bind_managers

_bind_managers(_sys.modules[__name__])
