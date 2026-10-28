"""Public model facade and explicit manager registration."""

from __future__ import annotations

# Base must exist before domain modules that import it through this facade.
from .base import Base, TenantScopedMixin, TimestampMixin

# isort: split
from .actions import AgentAction
from .agent import AgentFeedback, AgentMemory, AgentMessage, AgentSession
from .analysis.agent_scenario import AgentScenario
from .analysis.ai_analytics import AIAnalytics
from .analysis.analyzed_content_receipt import AnalyzedContentReceipt
from .audit import ActionAuditEvent
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
from .collection import (
    CollectedItem,
    CollectionCandidate,
    CollectionLaneRun,
    CollectionSeen,
    Platform,
    Source,
)
from .identity import LoginAttempt, ModelType, Permission, PlatformRole, Role, User, UserCredential
from .llm import (
    LLMAttempt,
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMDispatchReceipt,
    LLMModel,
    LLMOutcomeEvent,
    LLMProvider,
)
from .notifications import DigestRun, Notification, NotificationPreference, NotificationReceipt
from .scheduling import AgentTask, BotAction, Job
from .tenancy import EntitlementUsage, Tenant, TenantChannel, TenantInvite, TenantUser, WorkspaceRole

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
    "LoginAttempt",
    # Multi-tenant
    "Tenant",
    "EntitlementUsage",
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
    "AgentAction",
    "ActionAuditEvent",
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
    "NotificationReceipt",
    # Raw collected content
    "CollectedItem",
    "CollectionSeen",
    "CollectionCandidate",
    "CollectionLaneRun",
]

# All canonical classes exist before any public model manager is bound.
import sys as _sys

from .bootstrap import bind_managers as _bind_managers

_bind_managers(_sys.modules[__name__])
