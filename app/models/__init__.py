"""Public model facade and explicit manager registration."""

from __future__ import annotations

# Base must exist before domain modules that import it through this facade.
from .base import Base, TenantScopedMixin, TimestampMixin

# isort: split
from .agent import AgentFeedback, AgentMemory, AgentMessage, AgentSession
from .analysis.agent_scenario import AgentScenario
from .analysis.analyzed_content_receipt import AnalyzedContentReceipt
from .analysis.ai_analytics import AIAnalytics
from .billing import (
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLegacyAttribution,
    PersonalLegacyAttributionConsent,
    PersonalLLMBillingIncident,
    PersonalLLMBoundPolicy,
    PersonalLLMBudgetDay,
    PersonalLLMDispatch,
    PersonalLLMLedgerEntry,
    PersonalLLMReservation,
    PersonalLLMReservationState,
    TaskBillingPayer,
)
from .collection import CollectedItem, Platform, Source
from .identity import ModelType, Permission, PlatformRole, Role, User, UserCredential
from .llm import (
    LLMAttempt,
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMDispatchReceipt,
    LLMModel,
    LLMOutcomeEvent,
    LLMProvider,
)
from .notifications import DigestRun, Notification, NotificationPreference
from .scheduling import AgentTask, BotAction, Job
from .tenancy import Tenant, TenantChannel, TenantInvite, TenantUser, WorkspaceRole

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
    "PersonalBillingAccount",
    "TaskBillingPayer",
    "PersonalLLMDispatch",
    "PersonalBillingCutover",
    "PersonalLLMBoundPolicy",
    "PersonalLLMBudgetDay",
    "PersonalLLMBillingIncident",
    "PersonalLLMReservation",
    "PersonalLLMReservationState",
    "PersonalLLMLedgerEntry",
    "PersonalLegacyAttributionConsent",
    "PersonalLegacyAttribution",
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
    "AnalyzedContentReceipt",
    "LLMAttempt",
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
    "NotificationPreference",
    # Raw collected content
    "CollectedItem",
]

# All canonical classes exist before any public model manager is bound.
import sys as _sys

from .bootstrap import bind_managers as _bind_managers

_bind_managers(_sys.modules[__name__])
