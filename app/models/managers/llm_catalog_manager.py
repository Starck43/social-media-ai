"""Managers for catalog custody and plan grants.

Reads are deliberately narrow: catalog lists never select credential columns,
and no manager here is `__personal_scoped__` — the service in
`app/services/llm_catalog/` owns the owner/superuser rules and the
caller-owned transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..llm.llm_catalog import LLMCatalog
    from ..llm.plan_model_grant import PlanModelGrant


class LLMCatalogManager(BaseManager):
    """Query the platform catalog and a user's personal catalog."""

    def __init__(self):
        from ..llm.llm_catalog import LLMCatalog

        super().__init__(LLMCatalog)

    async def get_personal(self, user_id: int) -> "LLMCatalog" | None:
        from ..llm.llm_catalog import PERSONAL_CATALOG_KIND

        if type(user_id) is not int or user_id <= 0:
            raise PermissionError("A positive user id is required")
        return await self.filter(kind=PERSONAL_CATALOG_KIND, owner_user_id=user_id).first()

    async def get_platform(self) -> "LLMCatalog" | None:
        from ..llm.llm_catalog import PLATFORM_CATALOG_KIND

        return await self.filter(kind=PLATFORM_CATALOG_KIND).first()


class PlanModelGrantManager(BaseManager):
    """Query plan grants in the deterministic order used by the console."""

    def __init__(self):
        from ..llm.plan_model_grant import PlanModelGrant

        super().__init__(PlanModelGrant)

    async def ordered_for(self, plan: str, capability: str, *, active_only: bool = True) -> list["PlanModelGrant"]:
        from ..llm.plan_model_grant import PlanModelGrant

        query = self.filter(plan=plan, capability=capability)
        if active_only:
            query = query.filter(is_active=True)
        return await query.order_by(
            PlanModelGrant.is_default.desc(), PlanModelGrant.priority, PlanModelGrant.llm_model_id
        ).all()

    async def active_default(self, plan: str, capability: str) -> "PlanModelGrant" | None:
        from ..llm.plan_model_grant import PlanModelGrant

        return await self.filter(plan=plan, capability=capability, is_active=True, is_default=True).first()
