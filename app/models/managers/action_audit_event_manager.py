"""Append-only persistence, tenant-fenced reads and plan-aligned expiry."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, text

from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

from .base_manager import BaseManager

_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$", re.ASCII)
_SAFE_ACTIONS = frozenset({"scheduling.task_admit"})
_SAFE_OUTCOMES = frozenset({"succeeded", "unknown"})
_SAFE_REASONS = frozenset({"launch_unconfirmed"})
_SAFE_RESOURCE_KIND = "agent_task"
_MAX_DB_ID = 2147483647
MAX_EXPORT_SIZE = 1000
MAX_EXPORT_WINDOW = timedelta(days=90)


class ActionAuditEventManager(BaseManager):
    """Purpose-built manager; ordinary ORM mutations are intentionally absent."""

    def __init__(self):
        from ..audit.action_audit_event import ActionAuditEvent

        super().__init__(ActionAuditEvent)

    @staticmethod
    def _tenant() -> int:
        tenant_id = current_tenant_id()
        if is_bypass() or type(tenant_id) is not int or not 0 < tenant_id <= _MAX_DB_ID:
            raise TenantContextError("Action audit requires an explicit member workspace scope")
        return tenant_id

    @staticmethod
    def _validate(
        *,
        actor_kind: str,
        actor_id: int | str,
        action: str,
        outcome: str,
        resource_kind: str | None,
        resource_id: int | None,
        reason_code: str | None,
        request_id: str | None,
        job_id: int | None,
        receipt_ref: str | None,
    ) -> dict[str, Any]:
        if action not in _SAFE_ACTIONS or _ACTION.fullmatch(action) is None:
            raise ValueError("Unsupported action-audit action")
        if outcome not in _SAFE_OUTCOMES:
            raise ValueError("Unsupported persisted action-audit outcome")
        if actor_kind == "user":
            if type(actor_id) is not int or not 0 < actor_id <= _MAX_DB_ID:
                raise ValueError("Invalid action-audit user identity")
            actor_id = str(actor_id)
        elif (actor_kind, actor_id) not in {("service", "task_runner"), ("scheduler", "scheduler")}:
            raise ValueError("Invalid action-audit actor identity")
        if resource_kind != _SAFE_RESOURCE_KIND:
            raise ValueError("Invalid action-audit resource kind")
        if type(resource_id) is not int or not 0 < resource_id <= _MAX_DB_ID:
            raise ValueError("Invalid action-audit resource identity")
        resource_id = str(resource_id)
        if reason_code is not None and reason_code not in _SAFE_REASONS:
            raise ValueError("Invalid action-audit reason code")
        if request_id is not None or receipt_ref is not None:
            raise ValueError("Request and receipt references are not enabled for this action")
        if job_id is not None and (type(job_id) is not int or not 0 < job_id <= _MAX_DB_ID):
            raise ValueError("Invalid action-audit job identity")
        return {
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "action": action,
            "outcome": outcome,
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "reason_code": reason_code,
            "request_id": request_id,
            "job_id": job_id,
            "receipt_ref": receipt_ref,
        }

    async def record_success(
        self,
        *,
        session,
        actor_kind: str,
        actor_id: int | str,
        action: str,
        resource_kind: str,
        resource_id: int,
        job_id: int | None = None,
    ):
        """Insert success evidence into the caller's business transaction."""
        tenant_id = self._tenant()
        fields = self._validate(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action=action,
            outcome="succeeded",
            resource_kind=resource_kind,
            resource_id=resource_id,
            reason_code=None,
            request_id=None,
            job_id=job_id,
            receipt_ref=None,
        )
        event = self.model(
            tenant_id=tenant_id,
            event_id=uuid.uuid4().hex,
            **fields,
        )
        session.add(event)
        await session.flush()
        return event

    async def record_unknown(
        self,
        *,
        actor_kind: str,
        actor_id: int | str,
        action: str,
        resource_kind: str,
        resource_id: int,
        reason_code: str,
        job_id: int | None = None,
    ):
        """Persist a known indeterminate observation in its own transaction."""
        tenant_id = self._tenant()
        fields = self._validate(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action=action,
            outcome="unknown",
            resource_kind=resource_kind,
            resource_id=resource_id,
            reason_code=reason_code,
            request_id=None,
            job_id=job_id,
            receipt_ref=None,
        )
        async with self._get_owned_session() as session:
            event = self.model(
                tenant_id=tenant_id,
                event_id=uuid.uuid4().hex,
                **fields,
            )
            session.add(event)
            await session.flush()
        return event

    async def list_events(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int = 50,
        before_id: int | None = None,
        outcome: str | None = None,
        action: str | None = None,
    ) -> list:
        """Read only a bounded, explicitly tenant-scoped event window."""
        tenant_id = self._tenant()
        if since.tzinfo is None or until.tzinfo is None or since >= until:
            raise ValueError("Action audit reads require an aware, ordered time range")
        if until - since > MAX_EXPORT_WINDOW:
            raise ValueError("Action audit time range may not exceed 90 days")
        if type(limit) is not int or not 1 <= limit <= MAX_EXPORT_SIZE:
            raise ValueError("Action audit result size must be between 1 and 1000")
        if before_id is not None and (type(before_id) is not int or before_id <= 0):
            raise ValueError("Invalid action audit cursor")
        if outcome is not None and outcome not in _SAFE_OUTCOMES:
            raise ValueError("Unsupported persisted action-audit outcome")
        if action is not None and action not in _SAFE_ACTIONS:
            raise ValueError("Unsupported action-audit action")
        conditions = [
            self.model.tenant_id == tenant_id,
            self.model.occurred_at >= since,
            self.model.occurred_at < until,
        ]
        if before_id is not None:
            conditions.append(self.model.id < before_id)
        if outcome is not None:
            conditions.append(self.model.outcome == outcome)
        if action is not None:
            conditions.append(self.model.action == action)
        async with self._get_owned_session() as session:
            return list(
                (
                    await session.execute(
                        select(self.model).where(*conditions).order_by(self.model.id.desc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )

    async def delete_expired(self, *, now: datetime | None = None, batch_size: int = 500, session=None) -> int:
        """Expire evidence using the workspace's existing plan retention limit.

        This is the only supported delete path. The DB trigger requires a
        transaction-local marker, while ordinary manager deletes are refused.
        """
        tenant_id = self._tenant()
        if type(batch_size) is not int or not 1 <= batch_size <= 5000:
            raise ValueError("Invalid action-audit cleanup batch size")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Action-audit cleanup requires an aware timestamp")
        from ..tenancy.tenant import Tenant

        if session is None:
            async with self._get_owned_session() as owned:
                return await self.delete_expired(now=now, batch_size=batch_size, session=owned)
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
        if tenant is None:
            raise TenantContextError("Action-audit cleanup workspace does not exist")
        retention_days = Tenant.PLAN_LIMITS.get(tenant.plan, {}).get("retention_days")
        if type(retention_days) is not int or retention_days < 1:
            raise RuntimeError("Workspace action-audit retention policy is unavailable")
        cutoff = now - timedelta(days=retention_days)
        await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
        expired_ids = (
            select(self.model.id)
            .where(self.model.tenant_id == tenant_id, self.model.occurred_at < cutoff)
            .order_by(self.model.id)
            .limit(batch_size)
        )
        result = await session.execute(
            delete(self.model).where(self.model.id.in_(expired_ids)).execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def create(self, *args, **kwargs):
        raise PermissionError("Use the validated action-audit event methods")

    async def update(self, *args, **kwargs):
        raise PermissionError("Action-audit events are immutable")

    async def update_by_id(self, *args, **kwargs):
        raise PermissionError("Action-audit events are immutable")

    async def delete(self, *args, **kwargs):
        raise PermissionError("Use the scoped action-audit retention pass")

    async def delete_by_id(self, *args, **kwargs):
        raise PermissionError("Action-audit events are immutable")
