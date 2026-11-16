"""Tenant-fenced managers for synchronized CRM entities and greeting history (`crm_entities`, `greeting_history`, P14-T02..T04)."""

from __future__ import annotations

from app.core.tenant_context import TenantContextError, current_tenant_id
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import GreetingHistory
from app.models.managers.base_manager import BaseManager


class CrmEntityManager(BaseManager):
    """Enforces workspace isolation on all `CrmEntity` queries."""

    def __init__(self):
        super().__init__(CrmEntity)

    def get_queryset(self, session=None):
        tenant = current_tenant_id()
        if tenant is None:
            raise TenantContextError("CRM entity access requires a selected workspace")
        return super().get_queryset(session).filter(tenant_id=tenant)


class GreetingHistoryManager(BaseManager):
    """Enforces workspace isolation on all `GreetingHistory` queries."""

    def __init__(self):
        super().__init__(GreetingHistory)

    def get_queryset(self, session=None):
        tenant = current_tenant_id()
        if tenant is None:
            raise TenantContextError("Greeting history access requires a selected workspace")
        return super().get_queryset(session).filter(tenant_id=tenant)
