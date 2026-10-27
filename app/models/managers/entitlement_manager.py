"""Scoped product-right reads and atomic per-job capability admission."""

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select

from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.services.tenancy.entitlements import EntitlementDenied, catalog_summary, decide

from .base_manager import BaseManager


class EntitlementManager(BaseManager):
    def __init__(self):
        from app.models.tenancy.entitlement_usage import EntitlementUsage

        super().__init__(EntitlementUsage)

    @staticmethod
    def _tenant_id() -> int:
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0 or is_bypass():
            raise TenantContextError("Entitlements require a concrete normal workspace scope")
        return tenant_id

    async def evaluate(self, session, required: Iterable[str], *, job_id: int | None = None, consume: bool = False):
        from app.models import Job, Tenant
        from app.models.managers.job_manager import JobManager

        tenant_id = self._tenant_id()
        keys = set(required)
        if consume:
            # Same READ COMMITTED / workspace NO KEY UPDATE gate as task admission.
            # Serialize quota count+insert, including distinct concurrent jobs.
            await JobManager.lock_source_admission(session, tenant_id)
        workspace = (
            await session.execute(
                select(Tenant).where(Tenant.id == tenant_id).execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used = dict(
            (
                await session.execute(
                    select(self.model.capability, func.count(self.model.id))
                    .where(
                        self.model.tenant_id == tenant_id,
                        self.model.admitted_at >= day,
                        self.model.admitted_at < day + timedelta(days=1),
                    )
                    .group_by(self.model.capability)
                )
            ).all()
        )
        reserved = set()
        job = None
        if consume:
            if type(job_id) is not int or job_id <= 0:
                raise TenantContextError("Capability usage requires a persisted execution job")
            job = (
                await session.execute(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id))
            ).scalar_one_or_none()
            if job is None or job.status != "running":
                raise TenantContextError("Capability usage requires a running job in this workspace")
            reserved = set(
                (
                    await session.execute(
                        select(self.model.capability).where(
                            self.model.tenant_id == tenant_id, self.model.job_id == job_id
                        )
                    )
                ).scalars()
            )
        decision = decide(workspace, keys, used_today=used, reserved=reserved, now=now)
        if consume:
            if not decision.allowed:
                raise EntitlementDenied(decision)
            for capability in sorted(keys - reserved):
                session.add(
                    self.model(
                        tenant_id=tenant_id,
                        capability=capability,
                        job_id=job.id,
                        task_id=job.agent_task_id,
                        initiated_by_user_id=job.initiated_by_user_id,
                        plan=decision.plan,
                        catalog_version=decision.catalog_version,
                        admitted_at=now,
                    )
                )
            await session.flush()
        return decision

    async def inspect(self):
        from app.core.database import async_session_maker
        from app.models import Tenant
        from app.services.tenancy.capabilities import CAPABILITIES

        async with async_session_maker() as session:
            workspace = (
                await session.execute(select(Tenant).where(Tenant.id == self._tenant_id()))
            ).scalar_one_or_none()
            if workspace is None:
                raise TenantContextError("Workspace unavailable")
            return {**catalog_summary(workspace), "decision": (await self.evaluate(session, CAPABILITIES)).to_dict()}

    async def admit_execution(self, required, *, job_id):
        from app.core.database import async_session_maker

        async with async_session_maker() as session:
            async with session.begin():
                return await self.evaluate(session, required, job_id=job_id, consume=True)
