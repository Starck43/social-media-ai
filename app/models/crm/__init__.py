"""CRM models package (`CrmEntity`, `GreetingHistory`, P14-T02..T04)."""

from .entity import CRM_ENTITY_KINDS, CRM_PROVIDERS, CrmEntity
from .greeting_history import (
    GREETING_CHANNELS,
    GREETING_EVENT_TYPES,
    GREETING_STATUSES,
    GreetingHistory,
)

__all__ = [
    "CRM_ENTITY_KINDS",
    "CRM_PROVIDERS",
    "CrmEntity",
    "GREETING_CHANNELS",
    "GREETING_EVENT_TYPES",
    "GREETING_STATUSES",
    "GreetingHistory",
]
