"""Public model facade and explicit manager registration."""

from __future__ import annotations

# Base must exist before domain modules that import it through this facade.
from .base import Base, TenantScopedMixin, TimestampMixin

# isort: split
from .actions import AgentAction, BotAction, BusinessCommand, BusinessCommandTarget
from .agent import AgentFeedback, AgentMemory, AgentMessage, AgentSession
from .analysis.agent_scenario import AgentScenario
from .analysis.ai_analytics import AIAnalytics
from .analysis.analyzed_content_receipt import AnalyzedContentReceipt
from .audit import ActionAuditEvent, WorkspaceOffboardingEvent
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
from .communications import (
    ContactChannelStat,
    MessageThread,
    OutboxMessage,
    OutboxReplyEvidence,
    OutboxTarget,
    UnifiedMessage,
)
from .contacts import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
from .crm import CrmEntity, GreetingHistory
from .file_workspace import FileCatalogObject, FileCatalogObservation, FileCatalogScan
from .file_workspace.private_insights import (
    FileInsightAttempt,
    FileInsightMount,
    FileInsightObjectState,
    FilePrivateInsight,
)
from .identity import LoginAttempt, ModelType, Permission, PlatformRole, Role, User, UserCredential
from .integrations import (
    LocalConnector,
    LocalConnectorPairing,
    LocalConnectorRequest,
    PersonalConnectorAuthorization,
    TelegramLoginAttempt,
    TelegramLoginRequestKey,
    TrustedOutboundHost,
    TrustedOutboundHostEvent,
    WorkspaceAuthorizationGrant,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
)
from .knowledge import DocumentApproval, DocumentRevision, KnowledgeDocument, KnowledgeSnippet
from .llm import (
    LLMAttempt,
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMDispatchReceipt,
    LLMModel,
    LLMOutcomeEvent,
    LLMProvider,
)
from .notifications import DigestRun, Notification, NotificationDelivery, NotificationPreference, NotificationReceipt
from .calendar import CalendarChoice, CalendarEvent, SourceObject
from .planner import PlannerEventRun, PlannerEventSettings, PlannerReminder, PlannerTask
from .scheduling import AgentTask, Job
from .tenancy import EntitlementUsage, Tenant, TenantChannel, TenantInvite, TenantUser, WorkspaceRole

__all__ = [
    "LocalConnector",
    "LocalConnectorPairing",
    "LocalConnectorRequest",
    "Contact",
    "ContactIdentity",
    "ContactConsent",
    "ContactMergeJournal",
    "MessageThread",
    "UnifiedMessage",
    "ContactChannelStat",
    "PlannerTask",
    "CalendarChoice",
    "CalendarEvent",
    "SourceObject",
    "PlannerEventRun",
    "PlannerEventSettings",
    "PlannerReminder",
    "NotificationDelivery",
    "OutboxMessage",
    "OutboxReplyEvidence",
    "OutboxTarget",
    "CrmEntity",
    "GreetingHistory",
    "FileCatalogObject",
    "FileCatalogObservation",
    "FileCatalogScan",
    "FileInsightMount",
    "FileInsightObjectState",
    "FileInsightAttempt",
    "FilePrivateInsight",
    "KnowledgeSnippet",
    "KnowledgeDocument",
    "DocumentRevision",
    "DocumentApproval",
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
    "WorkspaceConnection",
    "WorkspaceConnectionSecret",
    "PersonalConnectorAuthorization",
    "TelegramLoginAttempt",
    "TelegramLoginRequestKey",
    "TrustedOutboundHost",
    "TrustedOutboundHostEvent",
    "WorkspaceAuthorizationGrant",
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
    "BusinessCommand",
    "BusinessCommandTarget",
    "ActionAuditEvent",
    "WorkspaceOffboardingEvent",
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
