"""Aggregate SQLAdmin views grouped by application use case."""

from .users import UserAdmin, RoleAdmin, PermissionAdmin, TenantAdmin, UserCredentialAdmin
from .sources import PlatformAdmin, SourceAdmin
from .scenarios import AgentScenarioAdmin
from .analytics import AIAnalyticsAdmin
from .notifications import NotificationAdmin
from .llm_catalog import LLMProviderAdmin, LLMModelAdmin
from .collection import CollectedItemAdmin
from .operations import BotActionAdmin, AgentTaskAdmin, JobAdmin

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
