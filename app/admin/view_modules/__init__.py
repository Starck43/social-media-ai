"""Aggregate SQLAdmin views grouped by application use case."""

from .analytics import AIAnalyticsAdmin
from .collection import CollectedItemAdmin
from .llm_catalog import LLMModelAdmin, LLMProviderAdmin
from .notifications import NotificationAdmin
from .operations import AgentTaskAdmin, BotActionAdmin, JobAdmin
from .scenarios import AgentScenarioAdmin
from .sources import PlatformAdmin, SourceAdmin
from .users import PermissionAdmin, RoleAdmin, TenantAdmin, UserAdmin, UserCredentialAdmin

__all__ = [
    "UserAdmin",
    "RoleAdmin",
    "PermissionAdmin",
    "TenantAdmin",
    "UserCredentialAdmin",
    "PlatformAdmin",
    "SourceAdmin",
    "AgentScenarioAdmin",
    "AIAnalyticsAdmin",
    "NotificationAdmin",
    "LLMProviderAdmin",
    "LLMModelAdmin",
    "CollectedItemAdmin",
    "BotActionAdmin",
    "AgentTaskAdmin",
    "JobAdmin",
]
